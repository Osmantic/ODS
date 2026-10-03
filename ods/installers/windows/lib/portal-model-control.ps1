# Finite, user-owned Windows Lemonade control for a bound WSL installation.
# The entrypoint reads JSON; this library keeps requests testable without tasks.
. (Join-Path $PSScriptRoot 'wsl-portal-amd.ps1')

function Throw-ODSPortalControlError([string]$Code, [string]$Message) {
    $exception = [InvalidOperationException]::new($Message)
    $exception.Data['code'] = $Code
    throw $exception
}

function Read-ODSPortalPlanBytes([string]$Path) {
    # An atomic publisher may replace the pathname while this handle retains
    # its complete previous version. Readers never lock out File.Replace.
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        ([IO.FileShare]::Read -bor [IO.FileShare]::Delete))
    $buffer = [IO.MemoryStream]::new()
    try {
        if ($stream.Length -gt 65536) { Throw-ODSPortalControlError 'unmanaged' 'The runtime plan exceeds its size limit.' }
        $stream.CopyTo($buffer)
        return ,$buffer.ToArray()
    } finally { $buffer.Dispose(); $stream.Dispose() }
}

function Get-ODSPortalPlanDigest([string]$Path) {
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return -join ($hash.ComputeHash((Read-ODSPortalPlanBytes $Path)) | ForEach-Object { $_.ToString('x2') }) }
    finally { $hash.Dispose() }
}

function Get-ODSPortalControlMutexName {
    return ('Global\ODS-PortalModelControl-' + (Get-ODSPortalUserSid))
}

function Assert-ODSPortalPrivateControlFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf) -or
        ((Get-Item -LiteralPath $Path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        Throw-ODSPortalControlError 'unmanaged' 'A regular private runtime file is required.'
    }
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        $acl = Get-Acl -LiteralPath $Path -ErrorAction Stop
        $sid = Get-ODSPortalUserSid
        $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
        if (-not $acl.AreAccessRulesProtected -or $rules.Count -ne 1 -or
            $rules[0].IdentityReference.Value -ne $sid -or $rules[0].AccessControlType -ne 'Allow') {
            Throw-ODSPortalControlError 'unmanaged' 'The runtime files are not private to the current Windows user.'
        }
    }
}

function Assert-ODSPortalControlModel($Plan) {
    if ($Plan.GgufFile -isnot [string] -or $Plan.GgufFile -notmatch '^[^\\/:*?"<>|\x00-\x1f\x7f]+\.gguf$' -or
        $Plan.GgufFile -ne $Plan.GgufFile.Trim() -or $Plan.GgufFile -in @('.gguf', '..gguf') -or
        ($Plan.ContextSize -isnot [int] -and $Plan.ContextSize -isnot [long]) -or
        $Plan.ContextSize -lt 4096 -or $Plan.ContextSize -gt 262144) {
        Throw-ODSPortalControlError 'invalid_model' 'Select a GGUF basename and an integer context between 4096 and 262144.'
    }
    $file = Join-Path $Plan.ModelsDir $Plan.GgufFile
    if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or
        ((Get-Item -LiteralPath $file -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        ((Get-Item -LiteralPath $Plan.ModelsDir -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        Throw-ODSPortalControlError 'model_missing' 'The selected GGUF must exist as a regular file in the managed Windows model store.'
    }
}

function Get-ODSPortalManagedConfiguration($Request) {
    $runtimeDir = Join-Path (Get-ODSPortalStateDir) 'portal-runtime'
    $path = Join-Path $runtimeDir 'runtime.json'
    try { $tasks = @(Get-ODSPortalLemonadeTask | Where-Object { $null -ne $_ }) }
    catch { Throw-ODSPortalControlError 'unmanaged' $_.Exception.Message }
    if ($tasks.Count -ne 1 -or $tasks[0].TaskPath -ne '\' -or
        [string]::IsNullOrWhiteSpace([string]$tasks[0].Principal.UserId) -or
        (Get-ODSPortalUserSid $tasks[0].Principal.UserId) -ne (Get-ODSPortalUserSid) -or
        @($tasks[0].Actions).Count -ne 1) {
        Throw-ODSPortalControlError 'unmanaged' 'No unique Portal Lemonade task belongs to the current Windows user.'
    }
    $action = $tasks[0].Actions[0]
    $shells = @(Get-Command pwsh.exe, powershell.exe -CommandType Application -ErrorAction SilentlyContinue)
    $shell = @($shells | Where-Object { $_.Source -ieq $action.Execute -or $_.Name -ieq $action.Execute })
    $expected = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $runtimeDir 'launch.ps1') + '"'
    if ($shell.Count -ne 1 -or $action.Arguments -cne $expected -or $action.WorkingDirectory -ine $runtimeDir) {
        Throw-ODSPortalControlError 'unmanaged' 'The Portal Lemonade task does not use the managed durable launcher.'
    }
    foreach ($name in @('runtime.json', 'launch.ps1', 'backend-contract.ps1', 'env-generator.ps1')) {
        Assert-ODSPortalPrivateControlFile (Join-Path $runtimeDir $name)
    }
    $intentPath = Join-Path $runtimeDir 'intent.json'
    if (Test-Path -LiteralPath $intentPath) { Assert-ODSPortalPrivateControlFile $intentPath }
    # Hash exactly the bytes parsed below, even if an installer replaces the
    # file concurrently. The mutation rechecks this digest before publication.
    $bytes = Read-ODSPortalPlanBytes $path
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $digest = -join ($hash.ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') }) } finally { $hash.Dispose() }
    $saved = [Text.UTF8Encoding]::new($false, $true).GetString($bytes) | ConvertFrom-Json -ErrorAction Stop
    if ($saved.WslDistro -isnot [string] -or $saved.WslInstallDir -isnot [string] -or
        $saved.WslDistro -ine $Request.distro -or $saved.WslInstallDir -cne $Request.installDir) {
        Throw-ODSPortalControlError 'unmanaged' 'The Windows runtime is not bound to this WSL installation. Rerun Windows Portal setup to register it.'
    }
    if ($saved.ExecutablePath -isnot [string] -or -not [IO.Path]::IsPathRooted($saved.ExecutablePath) -or
        -not (Test-Path -LiteralPath $saved.ExecutablePath -PathType Leaf) -or
        $saved.ModelsDir -isnot [string] -or $saved.ModelsDir -ine (Join-Path (Get-ODSPortalStateDir) 'models') -or
        ($saved.Port -isnot [int] -and $saved.Port -isnot [long]) -or $saved.Port -lt 1 -or $saved.Port -gt 65535) {
        Throw-ODSPortalControlError 'unmanaged' 'The saved Windows runtime plan does not match the managed loopback model store.'
    }
    # Project only public launch fields; never return arbitrary plan properties.
    $plan = [ordered]@{}
    foreach ($name in @('ExecutablePath', 'Port', 'ModelsDir', 'ContextSize', 'GgufFile', 'WslDistro', 'WslInstallDir')) {
        $plan[$name] = $saved.$name
    }
    $taskName = if ($tasks[0].TaskName) { $tasks[0].TaskName } else { Get-ODSPortalLemonadeTaskName }
    return [pscustomobject]@{ Task = $tasks[0]; TaskName = $taskName; ShellPath = $shell[0].Source; Plan = $plan
        PlanPath = $path; PlanDigest = $digest; ReadyPath = Join-Path $runtimeDir 'ready.json' }
}

function Get-ODSPortalManagedObservation($Configuration) {
    $plan = $Configuration.Plan
    $ready = Get-Content -LiteralPath $Configuration.ReadyPath -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    if ($ready.Error -or $ready.Port -ne $plan.Port -or $ready.ContextSize -ne $plan.ContextSize -or
        [string]::IsNullOrWhiteSpace([string]$ready.ModelId) -or -not $ready.StartedAt) {
        throw 'The Windows runtime has no valid readiness record for the saved plan.'
    }
    $process = Get-Process -Id $ready.ProcessId -ErrorAction Stop
    try {
        $null = $process.Handle
        if ($process.Path -ine $plan.ExecutablePath -or $process.HasExited -or
            [math]::Abs(($process.StartTime.ToUniversalTime() - ([datetime]$ready.StartedAt).ToUniversalTime()).TotalMilliseconds) -ge 1) {
            throw 'The saved Windows process identity is stale.'
        }
        Assert-ODSPortalLemonadeListener $plan.Port $ready.ProcessId $plan.ExecutablePath
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$($plan.Port)/api/v1/health" -TimeoutSec 5 -ErrorAction Stop
        $loaded = @($health.all_models_loaded | Where-Object { $_.model_name -ceq $ready.ModelId })
        if ($loaded.Count -ne 1) { throw 'The managed Windows model is not loaded.' }
        $context = $loaded[0].recipe_options.ctx_size
        if (-not $context) { $context = $loaded[0].ctx_size }
        if (($context -isnot [int] -and $context -isnot [long]) -or $context -ne $plan.ContextSize) {
            throw 'The managed Windows model context is not verified.'
        }
        Assert-ODSPortalLemonadeListener $plan.Port $ready.ProcessId $plan.ExecutablePath
        if ($process.HasExited) { throw 'The managed Windows process exited during model verification.' }
        return [ordered]@{ status = 'verified'; modelId = [string]$ready.ModelId; contextLength = [long]$context }
    } finally { $process.Dispose() }
}

function Get-ODSPortalControlStatus($Configuration, [switch]$SkipObservation) {
    $result = [ordered]@{ ok = $true; managed = $true; running = $false; observation = $null
        modelStoreWindowsPath = $Configuration.Plan.ModelsDir; planPathWindows = $Configuration.PlanPath
        planDigest = $Configuration.PlanDigest; plan = $Configuration.Plan }
    if (-not $SkipObservation -and (Test-Path -LiteralPath $Configuration.ReadyPath -PathType Leaf)) {
        try {
            $result.observation = Get-ODSPortalManagedObservation $Configuration
            $result.running = $true
        } catch {
            # Runtime observation failure does not remove the proven binding;
            # stop/start remain available, but stale readiness is never identity.
            $result.runtimeError = $_.Exception.Message
        }
    }
    return $result
}

function Get-ODSPortalReadOnlyStatus($Request) {
    try {
        $before = Get-ODSPortalManagedConfiguration $Request
        $result = Get-ODSPortalControlStatus $before
        # Status must coexist with a slow load/rollback. Revalidate the atomic
        # plan snapshot and task after observation instead of holding its lock.
        $after = Get-ODSPortalManagedConfiguration $Request
        $oldAction = $before.Task.Actions[0]
        $newAction = $after.Task.Actions[0]
        if ($before.PlanDigest -cne $after.PlanDigest -or
            $oldAction.Execute -cne $newAction.Execute -or $oldAction.Arguments -cne $newAction.Arguments -or
            $oldAction.WorkingDirectory -cne $newAction.WorkingDirectory -or
            $before.Task.Principal.UserId -cne $after.Task.Principal.UserId) {
            $result = Get-ODSPortalControlStatus $after -SkipObservation
            $result.runtimeError = 'The managed runtime changed during observation; refresh its status.'
        }
        return $result
    } catch {
        if ($_.Exception.Data['code'] -eq 'unmanaged') {
            return [ordered]@{ ok = $true; managed = $false; running = $false; observation = $null; reason = $_.Exception.Message }
        }
        throw
    }
}

function Assert-ODSPortalControlCas($Request, $Configuration) {
    if ($Request.expectedPlanDigest -isnot [string] -or $Request.expectedPlanDigest -cnotmatch '^[0-9a-f]{64}$' -or
        $Request.expectedPlanDigest -cne $Configuration.PlanDigest) {
        Throw-ODSPortalControlError 'plan_conflict' 'The Windows startup plan changed; refresh its status before changing the model.'
    }
}

function Invoke-ODSPortalModelControl($Request) {
    if ($null -eq $Request -or $Request -is [array] -or $Request.action -cnotin @('status', 'activate', 'restore', 'stop', 'start') -or
        $Request.distro -isnot [string] -or $Request.installDir -isnot [string]) {
        Throw-ODSPortalControlError 'invalid_request' 'Supply an action, distro and installDir in one JSON object.'
    }
    Assert-ODSPortalWslBinding $Request.distro $Request.installDir
    if ($Request.action -ceq 'status') { return Get-ODSPortalReadOnlyStatus $Request }
    $mutex = [Threading.Mutex]::new($false, (Get-ODSPortalControlMutexName))
    $held = $false
    try {
        try { $held = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $held = $true }
        if (-not $held) { Throw-ODSPortalControlError 'busy' 'Another Windows model operation is still running.' }
        $configuration = Get-ODSPortalManagedConfiguration $Request
        Assert-ODSPortalControlCas $Request $configuration
        $plan = $configuration.Plan
        $next = [ordered]@{}
        foreach ($key in $plan.Keys) { $next[$key] = $plan[$key] }
        if ($Request.action -ceq 'activate') {
            $next.GgufFile = $Request.gguf
            $next.ContextSize = $Request.contextSize
        } elseif ($Request.action -ceq 'restore') {
            if ($null -eq $Request.plan -or $Request.plan -is [array]) {
                Throw-ODSPortalControlError 'invalid_plan' 'Restore requires the previous verified plan.'
            }
            foreach ($key in @('ExecutablePath', 'Port', 'ModelsDir', 'WslDistro', 'WslInstallDir')) {
                $different = if ($key -ceq 'WslDistro') { $Request.plan.$key -ine $plan[$key] } else { $Request.plan.$key -cne $plan[$key] }
                if ($different) { Throw-ODSPortalControlError 'invalid_plan' 'Restore cannot change the runtime executable, endpoint, model store or WSL binding.' }
            }
            $next.GgufFile = $Request.plan.GgufFile
            $next.ContextSize = $Request.plan.ContextSize
        }
        if ($Request.action -cne 'stop') { Assert-ODSPortalControlModel $next }
        if ($Request.action -ceq 'start') {
            $status = Get-ODSPortalControlStatus $configuration
            if ($status.running) {
                Set-ODSPortalLemonadeIntent 'running'
                Enable-ScheduledTask -TaskName $configuration.TaskName -TaskPath '\' -ErrorAction Stop | Out-Null
                return $status
            }
        }
        # Existing task ownership and held process handles prove every stop.
        # Its action is unchanged: the durable launcher reads runtime.json.
        Set-ODSPortalLemonadeIntent 'stopped'
        Disable-ScheduledTask -TaskName $configuration.TaskName -TaskPath '\' -ErrorAction Stop | Out-Null
        Stop-ODSPortalLemonade $plan.ExecutablePath
        if ((Get-ODSPortalTaskEngineId $configuration.TaskName) -gt 0 -or
            @(Get-NetTCPConnection -LocalPort $plan.Port -State Listen -ErrorAction SilentlyContinue).Count) {
            Throw-ODSPortalControlError 'stop_unverified' 'The managed runtime did not stop; no replacement was started.'
        }
        if (Test-Path -LiteralPath $configuration.ReadyPath) { Remove-Item -LiteralPath $configuration.ReadyPath -Force }
        if ($Request.action -ceq 'stop') { return Get-ODSPortalControlStatus $configuration }
        # Recheck after teardown; a concurrent installer is not covered by this
        # controller's mutex. A changed plan is never overwritten.
        if ((Get-ODSPortalPlanDigest $configuration.PlanPath) -cne $configuration.PlanDigest) {
            Throw-ODSPortalControlError 'plan_conflict' 'The Windows startup plan changed during teardown.'
        }
        if ($Request.action -cin @('activate', 'restore')) {
            Write-ODSPrivateEnvFile -Path $configuration.PlanPath -Content ($next | ConvertTo-Json -Compress)
        }
        $publishedDigest = Get-ODSPortalPlanDigest $configuration.PlanPath
        try {
            $updated = Get-ODSPortalManagedConfiguration $Request
            if ($updated.PlanDigest -cne $publishedDigest) { Throw-ODSPortalControlError 'plan_conflict' 'The Windows startup plan changed before launch.' }
            Set-ODSPortalLemonadeIntent 'running'
            Enable-ScheduledTask -TaskName $updated.TaskName -TaskPath '\' -ErrorAction Stop | Out-Null
            Start-ScheduledTask -TaskName $updated.TaskName -TaskPath '\' -ErrorAction Stop
            $null = Wait-ODSPortalLemonadeReady $updated
            $status = Get-ODSPortalControlStatus $updated
            if (-not $status.running) { Throw-ODSPortalControlError 'start_unverified' ('The managed Windows model did not verify: ' + $status.runtimeError) }
            return $status
        } catch {
            $_.Exception.Data['newPlanDigest'] = $publishedDigest
            throw
        }
    } finally {
        if ($held) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}
