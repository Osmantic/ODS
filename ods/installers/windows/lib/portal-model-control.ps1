# Finite, user-owned Windows llama.cpp control for a bound WSL installation.
# The entrypoint reads JSON; this library keeps requests testable without tasks.
# Protocol (unchanged from the Lemonade era): status|activate|restore|stop|start
# with a plan digest compare-and-swap. The observation is
# {status:"verified", modelId:<GGUF>, contextLength:<n_ctx>}, reporting the
# requested size when llama.cpp aligned it upward by fewer than 256 cells.
# On the Arm64 CUDA route a switch is refused before anything
# is stopped when Windows cannot lend the new model its memory, and status
# carries the launcher's own refusal (runtimeError, startRefusal); the next
# Resume of a refused plan is Start anyway. Vulkan (AMD) runs no memory check.
. (Join-Path $PSScriptRoot 'wsl-portal-amd.ps1')
. (Join-Path $PSScriptRoot 'wsl-memory-config.ps1')

$script:ODSPortalControlPrivateFiles = @('runtime.json', 'runtime-options.json', 'launch.ps1',
    'native-llama-runtime.ps1', 'private-file.ps1', 'api-key')
# The ODS source tree this controller ships in (config/model-library.json).
$script:ODSPortalControlSourceRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
# An imported model's memory need is its files plus this much (context, buffers).
$script:ODSPortalImportedModelExtraGiB = 2
$script:ODSPortalControlTextLimit = 1024

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
    if ($Plan.GgufFile -cnotmatch '^[\x20-\x7e]+$' -or $Plan.GgufFile.StartsWith('.')) {
        # llama.cpp on Windows reads argv in the ANSI code page but serves the
        # alias as UTF-8, so a non-ASCII name could never prove its identity.
        Throw-ODSPortalControlError 'invalid_model' 'llama.cpp on Windows needs an ASCII GGUF file name; rename the model file.'
    }
    $file = Join-Path $Plan.ModelsDir $Plan.GgufFile
    if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or
        ((Get-Item -LiteralPath $file -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        ((Get-Item -LiteralPath $Plan.ModelsDir -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        Throw-ODSPortalControlError 'model_missing' 'The selected GGUF must exist as a regular file in the managed Windows model store.'
    }
    if ($Plan.Contains('MmprojFile')) {
        # A vision projector follows the model's own ASCII basename rule.
        $projector = $Plan.MmprojFile
        if ($projector -isnot [string] -or $projector -cnotmatch '^[\x20-\x7e]{1,240}$' -or $projector -notmatch '\.gguf$' -or
            $projector -match '[\\/:*?"<>|]' -or $projector -ne $projector.Trim() -or $projector.StartsWith('.') -or
            $projector -ieq $Plan.GgufFile) {
            Throw-ODSPortalControlError 'invalid_model' 'Select a vision projector by its ASCII GGUF basename, beside the model.'
        }
        $file = Join-Path $Plan.ModelsDir $projector
        if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or
            ((Get-Item -LiteralPath $file -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            Throw-ODSPortalControlError 'model_missing' 'The vision projector must exist as a regular file in the managed Windows model store.'
        }
    }
}

function Get-ODSPortalManagedConfiguration($Request) {
    $runtimeDir = Get-ODSPortalRuntimeDir
    $path = Join-Path $runtimeDir 'runtime.json'
    try { $tasks = @(Get-ODSPortalRuntimeTask | Where-Object { $null -ne $_ }) }
    catch { Throw-ODSPortalControlError 'unmanaged' $_.Exception.Message }
    if ($tasks.Count -ne 1 -or $tasks[0].TaskPath -ne '\' -or
        [string]::IsNullOrWhiteSpace([string]$tasks[0].Principal.UserId) -or
        (Get-ODSPortalUserSid $tasks[0].Principal.UserId) -ne (Get-ODSPortalUserSid) -or
        @($tasks[0].Actions).Count -ne 1) {
        Throw-ODSPortalControlError 'unmanaged' 'No unique Portal llama.cpp task belongs to the current Windows user.'
    }
    $action = $tasks[0].Actions[0]
    $shells = @(Get-Command pwsh.exe, powershell.exe -CommandType Application -ErrorAction SilentlyContinue)
    $shell = @($shells | Where-Object { $_.Source -ieq $action.Execute -or $_.Name -ieq $action.Execute })
    if ($shell.Count -ne 1 -or $action.Arguments -cne (Get-ODSPortalLauncherArguments) -or $action.WorkingDirectory -ine $runtimeDir) {
        Throw-ODSPortalControlError 'unmanaged' 'The Portal llama.cpp task does not use the managed durable launcher.'
    }
    foreach ($name in $script:ODSPortalControlPrivateFiles) {
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
        [IO.Path]::GetFileName($saved.ExecutablePath) -ine 'llama-server.exe' -or
        -not (Test-Path -LiteralPath $saved.ExecutablePath -PathType Leaf) -or
        $saved.ModelsDir -isnot [string] -or $saved.ModelsDir -ine (Get-ODSPortalModelsDir) -or
        ($saved.Port -isnot [int] -and $saved.Port -isnot [long]) -or $saved.Port -lt 1 -or $saved.Port -gt 65535) {
        Throw-ODSPortalControlError 'unmanaged' 'The saved Windows runtime plan does not match the managed loopback llama-server and model store.'
    }
    # Project only public launch fields; never return arbitrary plan properties.
    $plan = [ordered]@{}
    foreach ($name in @('ExecutablePath', 'Port', 'ModelsDir', 'ContextSize', 'GgufFile', 'WslDistro', 'WslInstallDir')) {
        $plan[$name] = $saved.$name
    }
    if ($null -ne $saved.MmprojFile) { $plan.MmprojFile = $saved.MmprojFile }
    # Optional plan fields the installed launcher declared it reads; a launcher
    # from before vision support declares none and is never handed a projector.
    $options = [Text.UTF8Encoding]::new($false, $true).GetString(
        (Read-ODSPortalPlanBytes (Join-Path $runtimeDir 'runtime-options.json'))) | ConvertFrom-Json -ErrorAction Stop
    Assert-ODSNativeLlamaOptions $options
    $features = @(@($options.PlanFeatures) | Where-Object { $_ -is [string] -and $_ -cin @('MmprojFile') })
    $taskName = if ($tasks[0].TaskName) { $tasks[0].TaskName } else { Get-ODSPortalRuntimeTaskName }
    return [pscustomobject]@{ Task = $tasks[0]; TaskName = $taskName; ShellPath = $shell[0].Source; Plan = $plan
        PlanPath = $path; PlanDigest = $digest; ReadyPath = Join-Path $runtimeDir 'ready.json'
        ApiKeyPath = Join-Path $runtimeDir 'api-key'; Features = $features
        Backend = (Get-ODSNativeLlamaOptionsBackend $options)
        Architecture = (Get-ODSNativeLlamaOptionsArchitecture $options)
        MemoryPlanPath = Join-Path $runtimeDir 'memory-plan.json'; OverridePath = Join-Path $runtimeDir 'start-override.json' }
}

function Format-ODSPortalControlText([string]$Text) {
    # Status text crosses to WSL as bounded printable ASCII.
    $value = ([regex]::Replace([string]$Text, '\s+', ' ') -replace '[^\x20-\x7e]', '?').Trim()
    if ($value.Length -gt $script:ODSPortalControlTextLimit) { $value = $value.Substring(0, $script:ODSPortalControlTextLimit - 3) + '...' }
    return $value
}

function Read-ODSPortalLauncherFailure($Configuration) {
    <#
    .SYNOPSIS
        The launcher's own startup error from ready.json, or $null when
        ready.json holds no error. Code is the memory refusal (memory_refused,
        gpu_busy) when the record is for the saved plan, otherwise ''.
    #>
    # A damaged record is no launcher error: the observation then fails as before.
    $ready = Read-ODSNativeLlamaJsonOrNull $Configuration.ReadyPath
    if ($null -eq $ready -or -not $ready.Error) { return $null }
    $plan = $Configuration.Plan
    $code = ''
    if ([string]$ready.Code -cin $script:ODSNativeStartRefusalCodes -and [string]$ready.GgufFile -ceq [string]$plan.GgufFile -and
        ($ready.ContextSize -is [int] -or $ready.ContextSize -is [long]) -and [long]$ready.ContextSize -eq [long]$plan.ContextSize) {
        $code = [string]$ready.Code
    }
    return [pscustomobject]@{ Text = (Format-ODSPortalControlText ([string]$ready.Error)); Code = $code }
}

function Get-ODSPortalCatalogModel([string]$GgufFile, [string]$SourceRoot) {
    # The curated catalog entry whose first file is this GGUF, or $null.
    if (-not $SourceRoot) { return $null }
    $path = Join-Path (Join-Path $SourceRoot 'config') 'model-library.json'
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    $catalog = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    foreach ($model in @($catalog.models)) {
        if ((Test-CatalogModelSourceAllowed -Model $model) -and [string]$model.gguf_file -ceq $GgufFile) { return $model }
    }
    return $null
}

function Get-ODSPortalControlMemoryNeed($Plan, [string]$SourceRoot = $script:ODSPortalControlSourceRoot) {
    <#
    .SYNOPSIS
        GPU and commit memory a CUDA plan needs: the catalog estimate at the
        planned context for a curated model (on arm64, with the carve-out as
        GPU memory), else an imported model's files plus 2 GiB.
    #>
    $model = Get-ODSPortalCatalogModel ([string]$Plan.GgufFile) $SourceRoot
    if ($model) {
        $commit = Get-ODSNativeCommitState
        $gpu = Get-ODSNativeGpuMemoryState
        $gpuInfo = @{ Backend = 'nvidia'; MemoryType = 'discrete'; VramMB = $(if ($gpu) { [int]$gpu.TotalMiB } else { 0 }) }
        $ramGB = if ($commit) { [int][math]::Floor($commit.VisibleGiB) } else { 0 }
        $runtimeProfile = Get-CatalogRuntimeProfile -Model $model -GpuInfo $gpuInfo -SystemRamGB $ramGB -HostArchitecture 'arm64'
        $need = Get-CatalogModelMemoryNeed -Model $model -RuntimeProfile $runtimeProfile -ContextLength ([int]$Plan.ContextSize)
        return [pscustomobject]@{ DeviceNeedGiB = $need.DeviceNeedGiB; CommitNeedGiB = $need.CommitNeedGiB; Source = 'catalog' }
    }
    $files = @(Get-ODSGgufSplitSiblings ([string]$Plan.GgufFile))
    if ($Plan.Contains('MmprojFile')) { $files += [string]$Plan.MmprojFile }
    $bytes = [long]0
    foreach ($name in $files) {
        $path = Join-Path $Plan.ModelsDir $name
        if (Test-Path -LiteralPath $path -PathType Leaf) { $bytes += (Get-Item -LiteralPath $path -Force).Length }
    }
    $gib = [math]::Round($bytes / 1GB + $script:ODSPortalImportedModelExtraGiB, 2)
    return [pscustomobject]@{ DeviceNeedGiB = $gib; CommitNeedGiB = $gib; Source = 'files' }
}

function Get-ODSPortalRunningModelNeed($Configuration) {
    # The current model's saved need while its readiness record is valid;
    # zero otherwise. Stopping it gives this memory back.
    $none = [pscustomobject]@{ CommitNeedGiB = 0.0; DeviceNeedGiB = 0.0; Running = $false }
    if (-not (Test-Path -LiteralPath $Configuration.ReadyPath -PathType Leaf)) { return $none }
    $ready = Read-ODSNativeLlamaJsonOrNull $Configuration.ReadyPath
    $plan = $Configuration.Plan
    if ($null -eq $ready -or $ready.Error -or [string]$ready.ModelId -cne [string]$plan.GgufFile -or $ready.ContextSize -ne $plan.ContextSize) { return $none }
    $memory = Read-ODSNativeMemoryPlan $Configuration.MemoryPlanPath $plan
    if (-not $memory.Plan) { return [pscustomobject]@{ CommitNeedGiB = 0.0; DeviceNeedGiB = 0.0; Running = $true } }
    return [pscustomobject]@{ CommitNeedGiB = [double]$memory.Plan.CommitNeedGiB; DeviceNeedGiB = [double]$memory.Plan.DeviceNeedGiB; Running = $true }
}

function Assert-ODSPortalControlMemoryFit($Configuration, $Plan, $Need, [double]$WslGiB) {
    <#
    .SYNOPSIS
        Refuse a CUDA switch (insufficient_memory) before anything is stopped
        when Windows cannot lend the new model its memory once the current
        model stops: the commit headroom and the commit rule the launcher
        applies, then the GPU's free memory. The current model keeps running.
    #>
    $commit = Get-ODSNativeCommitState
    $running = Get-ODSPortalRunningModelNeed $Configuration
    $kept = if ($running.Running) { 'Your current model keeps running.' } else { 'Nothing was changed.' }
    $label = "$($Plan.GgufFile) at $([math]::Round([double]$Plan.ContextSize / 1024))K"
    if ($commit) {
        $headroom = $commit.CommitLimitGiB - $commit.CommittedGiB + $running.CommitNeedGiB
        $rule = Get-ODSNativeCommitFit -CommitNeedGiB $Need.CommitNeedGiB -CommitLimitGiB $commit.CommitLimitGiB `
            -OtherCommitGiB $script:ODSNativeWindowsReserveGiB -WslGiB $WslGiB
        $lendable = [math]::Min($headroom, $rule.CapacityGiB)
        if ($Need.CommitNeedGiB -gt $lendable + 1e-9) {
            Throw-ODSPortalControlError 'insufficient_memory' ("$label needs about $(Format-ODSNativeGiB $Need.CommitNeedGiB) GiB, but Windows can lend " +
                "about $(Format-ODSNativeGiB $lendable) GiB once the current model stops (memory limit $(Format-ODSNativeGiB $commit.CommitLimitGiB) GiB). " +
                "$kept Choose a smaller model or a shorter context, or rerun install.ps1 so ODS can offer a larger page file. $script:ODSNativeHelpLine")
        }
    }
    $gpu = Get-ODSNativeGpuMemoryState
    if ($gpu) {
        $free = ([double]$gpu.TotalMiB - [double]$gpu.UsedMiB) / 1024 + $running.DeviceNeedGiB
        if ($Need.DeviceNeedGiB -gt $free + 1e-9) {
            $name = ([string]$gpu.Name -replace '[^\x20-\x7e]', '?')
            Throw-ODSPortalControlError 'insufficient_memory' ("$label needs about $(Format-ODSNativeGiB $Need.DeviceNeedGiB) GiB of GPU memory, but " +
                "$name has about $(Format-ODSNativeGiB $free) GiB free once the current model stops. $kept Close other programs that use the GPU, " +
                "or choose a smaller model or a shorter context. $script:ODSNativeHelpLine")
        }
    }
}

function Get-ODSPortalControlWslGiB($Configuration) {
    # The WSL memory limit the current plan was sized with, else what WSL
    # uses now (.wslconfig, or half of visible RAM).
    $current = Read-ODSNativeMemoryPlan $Configuration.MemoryPlanPath $Configuration.Plan
    if ($current.Plan) { return [double]$current.Plan.WslGiB }
    $commit = Get-ODSNativeCommitState
    $visible = if ($commit) { $commit.VisibleGiB } else { 0 }
    return [double](Get-ODSWslEffectiveMemory $visible).GiB
}

function Get-ODSPortalManagedObservation($Configuration) {
    $plan = $Configuration.Plan
    $ready = Get-Content -LiteralPath $Configuration.ReadyPath -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    if ($ready.Error -or $ready.Port -ne $plan.Port -or $ready.ContextSize -ne $plan.ContextSize -or
        [string]$ready.ModelId -cne [string]$plan.GgufFile -or -not $ready.StartedAt) {
        throw 'The Windows runtime has no valid readiness record for the saved plan.'
    }
    $process = Get-Process -Id $ready.ProcessId -ErrorAction Stop
    try {
        $null = $process.Handle
        if ($process.Path -ine $plan.ExecutablePath -or $process.HasExited -or
            [math]::Abs(($process.StartTime.ToUniversalTime() - ([datetime]$ready.StartedAt).ToUniversalTime()).TotalMilliseconds) -ge 1) {
            throw 'The saved Windows process identity is stale.'
        }
        Assert-ODSNativeLlamaListener $plan.Port $ready.ProcessId $plan.ExecutablePath
        if ((Get-ODSNativeLlamaHealthState $plan.Port) -ne 'ready') { throw 'The managed Windows model server is not healthy.' }
        # The key never leaves this machine-local controller: it is read from
        # the private runtime folder and sent only to 127.0.0.1.
        $apiKey = Read-ODSNativeLlamaApiKey $Configuration.ApiKeyPath
        $proof = Get-ODSNativeLlamaModelProof -Port $plan.Port -GgufFile $plan.GgufFile `
            -ModelPaths @((Join-Path $plan.ModelsDir $plan.GgufFile), (ConvertTo-ODSNativeLlamaArgumentPath (Join-Path $plan.ModelsDir $plan.GgufFile))) `
            -ContextSize $plan.ContextSize -ApiKey $apiKey -Vision:($plan.Contains('MmprojFile'))
        Assert-ODSNativeLlamaListener $plan.Port $ready.ProcessId $plan.ExecutablePath
        if ($process.HasExited) { throw 'The managed Windows process exited during model verification.' }
        return [ordered]@{ status = 'verified'; modelId = [string]$proof.ModelId; contextLength = [long]$proof.ContextLength }
    } finally { $process.Dispose() }
}

function Get-ODSPortalControlStatus($Configuration, [switch]$SkipObservation) {
    $result = [ordered]@{ ok = $true; managed = $true; running = $false; observation = $null
        modelStoreWindowsPath = $Configuration.Plan.ModelsDir; planPathWindows = $Configuration.PlanPath
        planDigest = $Configuration.PlanDigest; plan = $Configuration.Plan; planFeatures = @($Configuration.Features) }
    if (-not $SkipObservation -and (Test-Path -LiteralPath $Configuration.ReadyPath -PathType Leaf)) {
        # The launcher's own error (an AMD launch failure, or a CUDA memory
        # refusal the owner can override with Start anyway) is reported as it
        # was written, not as a missing readiness record.
        $failure = Read-ODSPortalLauncherFailure $Configuration
        if ($failure) {
            $result.runtimeError = $failure.Text
            $result.runtimeErrorSource = 'launcher'
            if ($failure.Code) { $result.startRefusal = $failure.Code }
            return $result
        }
        try {
            $result.observation = Get-ODSPortalManagedObservation $Configuration
            $result.running = $true
        } catch {
            # Runtime observation failure does not remove the proven binding;
            # stop/start remain available, but stale readiness is never identity.
            $result.runtimeError = Format-ODSPortalControlText $_.Exception.Message
            $result.runtimeErrorSource = 'observation'
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
            $result.runtimeErrorSource = 'observation'
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
            # Every switch states its projector; a model without one clears it.
            $next.Remove('MmprojFile')
            if ($null -ne $Request.mmproj) {
                if ('MmprojFile' -cnotin $configuration.Features) {
                    Throw-ODSPortalControlError 'invalid_model' 'This Windows model runtime was set up before vision support. Run Windows setup again to load vision models with their projector.'
                }
                $next.MmprojFile = $Request.mmproj
            }
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
            $next.Remove('MmprojFile')
            if ($null -ne $Request.plan.MmprojFile) { $next.MmprojFile = $Request.plan.MmprojFile }
        }
        if ($Request.action -cne 'stop') { Assert-ODSPortalControlModel $next }
        $memoryPlan = $null
        if ($configuration.Backend -ceq 'cuda' -and $configuration.Architecture -ceq 'arm64' -and $Request.action -ceq 'activate') {
            # Only a switch is ever refused, and before anything is disarmed.
            $wslGiB = Get-ODSPortalControlWslGiB $configuration
            $need = Get-ODSPortalControlMemoryNeed $next
            Assert-ODSPortalControlMemoryFit $configuration $next $need $wslGiB
            $memoryPlan = New-ODSPortalMemoryPlanRecord $next $need $wslGiB
        } elseif ($configuration.Backend -ceq 'cuda' -and $configuration.Architecture -ceq 'arm64' -and $Request.action -ceq 'restore') {
            # restore (and start) is the host agent's rollback: never refused,
            # and never stopped by its sizing. Without figures the launcher
            # logs that it skipped its check (the older memory plan names
            # another model), and Start anyway still covers a short machine.
            try {
                $wslGiB = Get-ODSPortalControlWslGiB $configuration
                $memoryPlan = New-ODSPortalMemoryPlanRecord $next (Get-ODSPortalControlMemoryNeed $next) $wslGiB
            } catch { $memoryPlan = $null }
        }
        if ($Request.action -ceq 'start') {
            $status = Get-ODSPortalControlStatus $configuration
            if ($status.running) {
                Set-ODSPortalRuntimeIntent 'running'
                Enable-ScheduledTask -TaskName $configuration.TaskName -TaskPath '\' -ErrorAction Stop | Out-Null
                return $status
            }
        }
        # Existing task ownership and held process handles prove every stop.
        # Its action is unchanged: the durable launcher reads runtime.json, and
        # each model switch is a llama-server restart (no load API). The stop
        # disarms sign-in startup first and restores it when it refuses.
        Stop-ODSPortalRuntime $plan.ExecutablePath
        if ((Get-ODSPortalTaskEngineId $configuration.TaskName) -gt 0 -or
            @(Get-NetTCPConnection -LocalPort $plan.Port -State Listen -ErrorAction SilentlyContinue).Count) {
            Throw-ODSPortalControlError 'stop_unverified' 'The managed runtime did not stop; no replacement was started.'
        }
        # Starting a plan the launcher refused for memory is the owner's
        # Start anyway (the Models page names it so): read before removal.
        $override = $false
        if ($Request.action -cne 'stop' -and (Test-Path -LiteralPath $configuration.ReadyPath -PathType Leaf)) {
            $refused = Read-ODSPortalLauncherFailure $configuration
            $override = $refused -and $refused.Code -and [string]$next.GgufFile -ceq [string]$plan.GgufFile -and $next.ContextSize -eq $plan.ContextSize
        }
        if (Test-Path -LiteralPath $configuration.ReadyPath) { Remove-Item -LiteralPath $configuration.ReadyPath -Force }
        if ($Request.action -ceq 'stop') { return Get-ODSPortalControlStatus $configuration }
        # Recheck after teardown: setup holds this mutex only for its cutover,
        # and an older setup holds none. A changed plan is never overwritten.
        if ((Get-ODSPortalPlanDigest $configuration.PlanPath) -cne $configuration.PlanDigest) {
            Throw-ODSPortalControlError 'plan_conflict' 'The Windows startup plan changed during teardown.'
        }
        if ($Request.action -cin @('activate', 'restore')) {
            Write-ODSPrivateEnvFile -Path $configuration.PlanPath -Content ($next | ConvertTo-Json -Compress)
            if ($memoryPlan) { Write-ODSPrivateEnvFile -Path $configuration.MemoryPlanPath -Content ($memoryPlan | ConvertTo-Json -Compress) }
        }
        if ($override) {
            $record = [ordered]@{ GgufFile = [string]$next.GgufFile; ContextSize = [long]$next.ContextSize
                RequestedAt = [datetime]::UtcNow.ToString('o') }
            Write-ODSPrivateEnvFile -Path $configuration.OverridePath -Content ($record | ConvertTo-Json -Compress)
        }
        $publishedDigest = Get-ODSPortalPlanDigest $configuration.PlanPath
        try {
            $updated = Get-ODSPortalManagedConfiguration $Request
            if ($updated.PlanDigest -cne $publishedDigest) { Throw-ODSPortalControlError 'plan_conflict' 'The Windows startup plan changed before launch.' }
            Set-ODSPortalRuntimeIntent 'running'
            Enable-ScheduledTask -TaskName $updated.TaskName -TaskPath '\' -ErrorAction Stop | Out-Null
            Start-ScheduledTask -TaskName $updated.TaskName -TaskPath '\' -ErrorAction Stop
            $null = Wait-ODSPortalRuntimeReady $updated
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
