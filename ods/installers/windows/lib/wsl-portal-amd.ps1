# AMD GPUs for the Windows -> WSL Portal path.
#
# Docker Desktop passes only NVIDIA GPUs into WSL containers, and ROCm does not
# run inside them, so an AMD GPU is used by Lemonade Server running natively on
# Windows (Vulkan), exactly as the native Windows installer does. The Linux
# installer already supports that layout: --lemonade-url points LiteLLM and
# Pixel at it, and containers reach Windows loopback via host.docker.internal.
# Requires wsl-portal-setup.ps1 (Confirm-ODSPortalPreparation) in scope.

. (Join-Path $PSScriptRoot 'detection.ps1')
. (Join-Path $PSScriptRoot 'tier-map.ps1')
. (Join-Path $PSScriptRoot 'backend-contract.ps1')
. (Join-Path $PSScriptRoot 'env-generator.ps1')

$script:ODSPortalLemonadeTaskName = 'ODSLemonadeRuntime'
$script:ODSPortalLemonadeHealthSeconds = 60
# The first load also downloads Lemonade's llama.cpp Vulkan runtime.
$script:ODSPortalLemonadeLoadSeconds = 900
# Tried in order when AMD_INFERENCE_PORT is unset: the pinned port, Lemonade's
# own defaults, then two quiet ports. 8080 is often taken by other programs.
$script:ODSPortalLemonadePortCandidates = @(8080, 13305, 8000, 18080, 28080)

function Get-ODSPortalStateDir {
    return (Join-Path $env:LOCALAPPDATA 'ODS\lemonade')
}

function Get-ODSPortalAmdPlan([string]$SourceRoot) {
    # $null means "no AMD GPU route": NVIDIA and CPU-only machines keep the
    # in-WSL llama-server path.
    $gpu = Get-GpuInfo
    if ($gpu.Backend -ne 'amd') { return $null }
    $ramGB = Get-SystemRamGB
    $tier = [string](ConvertTo-TierFromGpu -GpuInfo $gpu -SystemRamGB $ramGB)
    if ($tier -eq '0') {
        Write-Host "         $($gpu.Name) has too little graphics memory for a local model; ODS will run the model on the CPU."
        return $null
    }
    if (-not $env:MODEL_PROFILE) { $env:MODEL_PROFILE = 'qwen' }
    $config = Resolve-TierConfig -Tier $tier
    $config = Resolve-CatalogModelRecommendation -TierConfig $config -Tier $tier -GpuInfo $gpu `
        -SystemRamGB $ramGB -SourceRoot $SourceRoot -MinContext $script:HERMES_MIN_CONTEXT
    if (-not $config.GgufFile -or -not $config.GgufUrl -or -not $config.MaxContext) {
        throw "No model is defined for AMD tier $tier."
    }
    # The Linux installer accepts tiers 1-4; larger AMD classes use its top tier.
    $linuxTier = if ($tier -match '^[1-4]$') { $tier } else { '4' }
    return [pscustomobject]@{
        GpuName = [string]$gpu.Name
        VramMB = [int]$gpu.VramMB
        MemoryType = [string]$gpu.MemoryType
        Tier = $tier
        LinuxTier = $linuxTier
        Model = [string]$config.LlmModel
        GgufFile = [string]$config.GgufFile
        GgufUrl = [string]$config.GgufUrl
        GgufSha256 = [string]$config.GgufSha256
        ContextSize = [int]$config.MaxContext
    }
}

function Install-ODSPortalLemonade([string]$SourceRoot, [bool]$NonInteractive) {
    # Returns the Lemonade executable path, or $null when the user declines.
    $runtime = Get-ODSAmdLemonadeRuntime -RootPath $SourceRoot
    $exe = Resolve-ODSLemonadeExe -ExecutableName $runtime.windows_executable
    if ($exe) {
        Write-Host "         Lemonade Server found: $exe"
        return $exe
    }
    if (-not (Confirm-ODSPortalPreparation "Install Lemonade Server $($runtime.windows_version) to run the AI model on your AMD GPU? It installs for your Windows user and runs only on this computer (127.0.0.1)." $NonInteractive)) {
        return $null
    }
    $msi = Join-Path $env:TEMP $runtime.windows_msi_file
    $url = "https://github.com/lemonade-sdk/lemonade/releases/download/v$($runtime.windows_version)/$($runtime.windows_msi_file)"
    Write-Host "         Downloading $url"
    & curl.exe --fail --location --silent --show-error --output $msi $url
    if ($LASTEXITCODE -ne 0) { throw "Could not download Lemonade Server (curl exit $LASTEXITCODE): $url" }
    $installDir = Get-ODSLemonadeUserInstallDir
    $log = Join-Path (Get-ODSPortalStateDir) 'lemonade-msi-install.log'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $log) | Out-Null
    Write-Host '         Installing Lemonade Server...'
    $msiArgs = "/i `"$msi`" /quiet /norestart INSTALLDIR=`"$installDir`" /L*V `"$log`""
    $process = Start-Process -FilePath msiexec.exe -ArgumentList $msiArgs -Wait -PassThru
    if ($process.ExitCode -notin @(0, 3010)) { throw "Lemonade Server setup failed (msiexec exit $($process.ExitCode)). Log: $log" }
    $exe = Resolve-ODSLemonadeExe -ExecutableName $runtime.windows_executable
    if (-not $exe) { throw "Lemonade Server setup finished but $($runtime.windows_executable) was not found under $installDir. Log: $log" }
    return $exe
}

function Get-ODSPortalLemonadeModel($Plan) {
    # Downloads the planned GGUF once into the Windows models folder Lemonade
    # serves, verifying the pinned SHA-256.
    $modelsDir = Join-Path (Get-ODSPortalStateDir) 'models'
    New-Item -ItemType Directory -Force -Path $modelsDir | Out-Null
    $target = Join-Path $modelsDir $Plan.GgufFile
    if (Test-Path -LiteralPath $target -PathType Leaf) {
        if (-not $Plan.GgufSha256 -or (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -eq $Plan.GgufSha256) {
            Write-Host "         Model already downloaded: $($Plan.GgufFile)"
            return $modelsDir
        }
        Write-Host "         $($Plan.GgufFile) does not match its checksum; downloading it again."
    }
    $partial = "$target.partial"
    Write-Host "         Downloading $($Plan.GgufFile) for your GPU. This is the large download."
    & curl.exe --fail --location --continue-at - --output $partial $Plan.GgufUrl
    if ($LASTEXITCODE -ne 0) { throw "Model download failed (curl exit $LASTEXITCODE). Rerun the same command to resume it." }
    if ($Plan.GgufSha256) {
        $actual = (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash
        if ($actual -ne $Plan.GgufSha256) {
            Remove-Item -LiteralPath $partial
            throw "The downloaded model does not match its checksum ($actual, expected $($Plan.GgufSha256)). Rerun the same command."
        }
    }
    Move-Item -LiteralPath $partial -Destination $target -Force
    return $modelsDir
}

function Test-ODSPortalLemonadeHealth([int]$Port) {
    # HttpWebRequest raises WebException for refused, timed-out and non-2xx
    # requests on both Windows PowerShell 5.1 and PowerShell 7.
    $request = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:$Port/api/v1/health")
    $request.Timeout = 3000
    try {
        $response = $request.GetResponse()
        $code = [int]$response.StatusCode
        $response.Close()
        return $code -eq 200
    } catch [System.Net.WebException] {
        return $false
    }
}

function Wait-ODSPortalLemonadeHealth([int]$Port, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-ODSPortalLemonadeHealth $Port) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Get-ODSPortalUserSid([string]$UserId) {
    if (-not $UserId) { return [Security.Principal.WindowsIdentity]::GetCurrent().User.Value }
    if ($UserId -match '^S-1-') { return (New-Object Security.Principal.SecurityIdentifier($UserId)).Value }
    return (New-Object Security.Principal.NTAccount($UserId)).Translate([Security.Principal.SecurityIdentifier]).Value
}

function Get-ODSPortalTaskEngineId {
    $scheduler = New-Object -ComObject Schedule.Service
    $scheduler.Connect()
    $instances = @($scheduler.GetFolder('\').GetTask($script:ODSPortalLemonadeTaskName).GetInstances(0))
    if ($instances.Count -gt 1) { throw 'More than one Portal Lemonade task instance is running; ownership is ambiguous.' }
    if ($instances.Count -eq 1) { return [int]$instances[0].EnginePID }
    return 0
}

function Stop-ODSPortalLemonade([string]$ExecutablePath) {
    # Capture the task's actual process tree before Task Scheduler removes its
    # root. Matching an installation directory does not prove process ownership.
    $lookupErrors = @()
    $tasks = @(Get-ScheduledTask -TaskName $script:ODSPortalLemonadeTaskName -ErrorAction SilentlyContinue -ErrorVariable lookupErrors)
    if (@($lookupErrors | Where-Object { $_.CategoryInfo.Category -ne 'ObjectNotFound' }).Count) {
        throw 'Cannot inspect the existing Portal Lemonade task; no process was stopped.'
    }
    if ($tasks.Count -eq 0) { return } # First install preserves user-started servers.
    if ($tasks.Count -ne 1 -or $tasks[0].TaskPath -ne '\' -or
        [string]::IsNullOrWhiteSpace([string]$tasks[0].Principal.UserId) -or
        (Get-ODSPortalUserSid $tasks[0].Principal.UserId) -ne (Get-ODSPortalUserSid)) {
        throw 'The existing Portal Lemonade task is not uniquely owned by the current Windows user.'
    }
    $task = $tasks[0]
    $actions = @($task.Actions)
    if ($actions.Count -ne 1) { throw 'The Portal Lemonade task has an unrecognized action; no process was stopped.' }
    $action = $actions[0]
    $runtimeDir = Join-Path (Get-ODSPortalStateDir) 'portal-runtime'
    $readyPath = Join-Path $runtimeDir 'ready.json'
    $actionExe = [string]$action.Execute
    $durable = $false
    if ($actionExe -ieq $ExecutablePath) {
        $pattern = '^serve --port (\d+) --host 127\.0\.0\.1 --no-tray --llamacpp vulkan --extra-models-dir "([^"]+)"(?: --ctx-size \d+)?$'
        if ($action.Arguments -notmatch $pattern -or
            $Matches[2] -ine (Join-Path (Get-ODSPortalStateDir) 'models') -or
            $action.WorkingDirectory -ine (Split-Path -Parent $ExecutablePath)) {
            throw 'The existing Lemonade task does not match the ODS launch contract; no process was stopped.'
        }
        $port = [int]$Matches[1]
    } else {
        $shells = @(Get-Command pwsh.exe, powershell.exe -CommandType Application -ErrorAction SilentlyContinue)
        $shell = @($shells | Where-Object { $_.Source -ieq $actionExe -or $_.Name -ieq $actionExe })
        $expectedArgs = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $runtimeDir 'launch.ps1') + '"'
        $legacyPath = Join-Path (Get-ODSPortalStateDir) 'lemonade-launch.task.ps1'
        $legacyArgs = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $legacyPath + '"'
        if ($shell.Count -ne 1 -or $action.Arguments -cnotin @($expectedArgs, $legacyArgs)) {
            throw 'The existing Lemonade task launcher is not recognized; no process was stopped.'
        }
        $actionExe = $shell[0].Source
        if ($action.Arguments -ceq $expectedArgs) {
            $plan = Get-Content -LiteralPath (Join-Path $runtimeDir 'runtime.json') -Raw -ErrorAction Stop | ConvertFrom-Json
            if ($action.WorkingDirectory -ine $runtimeDir -or $plan.ExecutablePath -ine $ExecutablePath -or
                $plan.ModelsDir -ine (Join-Path (Get-ODSPortalStateDir) 'models')) {
                throw 'The saved Portal Lemonade plan belongs to a different runtime; no process was stopped.'
            }
            $port = [int]$plan.Port
            $durable = $true
        } else {
            # Migrate the former 10.7 wrapper by reading constant assignments,
            # never by dot-sourcing or evaluating its PowerShell contents.
            $tokens = $null; $parseErrors = $null
            $ast = [Management.Automation.Language.Parser]::ParseFile($legacyPath, [ref]$tokens, [ref]$parseErrors)
            $values = @{}
            foreach ($assignment in $ast.FindAll({ param($node)
                $node -is [Management.Automation.Language.AssignmentStatementAst] -and
                $node.Left -is [Management.Automation.Language.VariableExpressionAst] -and
                $node.Left.VariablePath.UserPath -in @('exe', 'argumentString', 'workingDirectory')
            }, $true)) {
                $name = $assignment.Left.VariablePath.UserPath
                if ($assignment.Parent -ne $ast.EndBlock -or $values.ContainsKey($name) -or
                    $assignment.Operator -ne [Management.Automation.Language.TokenKind]::Equals -or
                    $assignment.Right -isnot [Management.Automation.Language.CommandExpressionAst] -or
                    $assignment.Right.Expression -isnot [Management.Automation.Language.StringConstantExpressionAst]) {
                    throw 'The former Lemonade launcher has nonconstant or ambiguous settings; no process was stopped.'
                }
                $values[$name] = $assignment.Right.Expression.Value
            }
            if ($parseErrors.Count -or $values.Count -ne 3 -or $values.exe -ine $ExecutablePath -or
                $values.workingDirectory -ine (Split-Path -Parent $ExecutablePath) -or
                $action.WorkingDirectory -ine $values.workingDirectory -or
                $values.argumentString -notmatch '^--port (\d+) --host 127\.0\.0\.1$') {
                throw 'The former Lemonade launcher does not match the ODS loopback contract; no process was stopped.'
            }
            $port = [int]$Matches[1]
        }
    }
    if ($port -lt 1 -or $port -gt 65535) { throw 'The existing Portal Lemonade task has an invalid port.' }

    $engineId = Get-ODSPortalTaskEngineId
    $nodes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $root = $null
    if ($engineId -gt 0) {
        # On current Windows the engine PID is the action itself. Older task
        # engines can parent it; require the exact action and a unique match.
        $commandLines = foreach ($exe in @($actionExe, [string]$action.Execute) | Select-Object -Unique) {
            '"' + $exe + '" ' + $action.Arguments
            $exe + ' ' + $action.Arguments
        }
        $candidates = @($nodes | Where-Object {
            $_.ExecutablePath -ieq $actionExe -and $_.CommandLine -cin $commandLines -and
            ($_.ProcessId -eq $engineId -or $_.ParentProcessId -eq $engineId)
        })
        if ($candidates.Count -ne 1) { throw 'Cannot prove the running Portal Lemonade task process; no process was stopped.' }
        $root = $candidates[0]
        if ($root.ProcessId -ne $engineId) {
            $engines = @($nodes | Where-Object { $_.ProcessId -eq $engineId })
            if ($engines.Count -ne 1 -or $engines[0].CreationDate -isnot [datetime] -or
                $root.CreationDate -lt $engines[0].CreationDate) {
                throw 'The Portal task engine ancestry cannot be proved; no process was stopped.'
            }
        }
    } elseif ($durable -and (Test-Path -LiteralPath $readyPath -PathType Leaf)) {
        $ready = Get-Content -LiteralPath $readyPath -Raw | ConvertFrom-Json
        if (-not $ready.Error -and $ready.ProcessId -and $ready.StartedAt) {
            $matchesById = @($nodes | Where-Object { $_.ProcessId -eq $ready.ProcessId })
            if ($matchesById.Count -eq 1 -and $matchesById[0].ExecutablePath -ieq $ExecutablePath -and
                $matchesById[0].CreationDate -is [datetime] -and
                [math]::Abs(($matchesById[0].CreationDate.ToUniversalTime() - ([datetime]$ready.StartedAt).ToUniversalTime()).TotalMilliseconds) -lt 1) {
                $root = $matchesById[0]
            } elseif ($matchesById.Count -or @($nodes | Where-Object { $_.ParentProcessId -eq $ready.ProcessId }).Count) {
                throw 'The saved Lemonade process identity is stale or has orphaned children; ownership cannot be proved.'
            }
        }
    }
    if (-not $root) {
        if ($task.State -notin @('Ready', 'Disabled') -or @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).Count) {
            throw "Cannot prove ownership of Lemonade on port $port. No process was stopped; close the previous ODS runtime explicitly before retrying."
        }
        return
    }

    $tree = [Collections.Generic.List[object]]::new()
    $tree.Add($root)
    for ($index = 0; $index -lt $tree.Count; $index++) {
        $parent = $tree[$index]
        if ($parent.CreationDate -isnot [datetime] -or -not $parent.ExecutablePath) {
            throw 'Cannot read the owned Lemonade process identity; no process was stopped.'
        }
        foreach ($node in @($nodes | Where-Object { $_.ParentProcessId -eq $parent.ProcessId })) {
            if ($node.CreationDate -isnot [datetime] -or $node.CreationDate -lt $parent.CreationDate -or
                $node.ProcessId -in @($tree | ForEach-Object { $_.ProcessId })) {
                throw 'The Lemonade process ancestry is stale or ambiguous; no process was stopped.'
            }
            $tree.Add($node)
        }
    }
    # Hold process handles before stopping the task, so a recycled PID cannot
    # redirect a later Kill(). Children may live outside Lemonade's bin folder.
    $handles = [Collections.Generic.List[object]]::new()
    try {
        foreach ($node in $tree) {
            $process = Get-Process -Id $node.ProcessId -ErrorAction Stop
            $handles.Add($process)
            $null = $process.Handle
            if ($process.Path -ine $node.ExecutablePath -or
                [math]::Abs(($process.StartTime.ToUniversalTime() - $node.CreationDate.ToUniversalTime()).TotalMilliseconds) -ge 1) {
                throw 'A Lemonade process changed during ownership verification; no process was stopped.'
            }
        }
        $currentTasks = @(Get-ScheduledTask -TaskName $script:ODSPortalLemonadeTaskName -ErrorAction Stop)
        if ($currentTasks.Count -ne 1 -or $currentTasks[0].TaskPath -ne $task.TaskPath -or
            $currentTasks[0].Principal.UserId -ne $task.Principal.UserId -or @($currentTasks[0].Actions).Count -ne 1 -or
            $currentTasks[0].Actions[0].Execute -ne $action.Execute -or $currentTasks[0].Actions[0].Arguments -cne $action.Arguments -or
            $currentTasks[0].Actions[0].WorkingDirectory -ne $action.WorkingDirectory -or (Get-ODSPortalTaskEngineId) -ne $engineId) {
            throw 'The Portal Lemonade task changed during ownership verification; no process was stopped.'
        }
        if ($engineId -gt 0) { Stop-ScheduledTask -TaskName $script:ODSPortalLemonadeTaskName -TaskPath '\' -ErrorAction Stop }
        # Parents first prevent the router from launching replacement children.
        foreach ($process in $handles) {
            if (-not $process.HasExited) {
                try { $process.Kill() } catch [InvalidOperationException] { if (-not $process.HasExited) { throw } }
            }
        }
        foreach ($process in $handles) {
            if (-not $process.WaitForExit(5000)) { throw 'An owned Lemonade process did not exit within five seconds.' }
        }
    } finally {
        foreach ($process in $handles) { $process.Dispose() }
    }
}

function Get-ODSPortalPortOwner([int]$Port) {
    # Process name listening on the port, or $null when it is free.
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { return $null }
    $process = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    if ($process) { return $process.ProcessName }
    return "process $($listener.OwningProcess)"
}

function Select-ODSPortalLemonadePort {
    if ($env:AMD_INFERENCE_PORT) {
        $port = [int]$env:AMD_INFERENCE_PORT
        $owner = Get-ODSPortalPortOwner $port
        if ($owner) { throw "AMD_INFERENCE_PORT $port is already used by '$owner'. Choose a free port or remove AMD_INFERENCE_PORT, then rerun." }
        return $port
    }
    $taken = @()
    foreach ($port in $script:ODSPortalLemonadePortCandidates) {
        $owner = Get-ODSPortalPortOwner $port
        if (-not $owner) { return $port }
        $taken += "$port ($owner)"
    }
    throw "No free port for Lemonade Server; all are in use: $($taken -join ', '). Set AMD_INFERENCE_PORT to a free port, then rerun."
}

function Assert-ODSPortalLemonadeListener([int]$Port, [int]$ProcessId, [string]$ExecutablePath) {
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    $process = Get-Process -Id $ProcessId -ErrorAction Stop
    if ($listeners.Count -ne 1 -or $listeners[0].LocalAddress -ne '127.0.0.1' -or
        -not ([string]$process.Path).Equals($ExecutablePath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "The Lemonade listener on port $Port does not belong to the launched loopback process."
    }
    # Some versions launch lemonade-router.exe as a child. Prove its ancestry
    # and exact directory; a similarly named process or directory is not ours.
    $owner = [int]$listeners[0].OwningProcess
    $directory = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($ExecutablePath))
    for ($depth = 0; $depth -lt 8; $depth++) {
        if ($owner -eq $ProcessId) { return }
        $nodes = @(Get-CimInstance Win32_Process -Filter "ProcessId=$owner" -ErrorAction Stop)
        if ($nodes.Count -ne 1) { break }
        $node = $nodes[0]
        $path = [string]$node.ExecutablePath
        if (-not [IO.Path]::IsPathRooted($path) -or
            -not ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($path))).Equals($directory, [StringComparison]::OrdinalIgnoreCase) -or
            [IO.Path]::GetFileName($path) -notin @('lemonade-router.exe', 'lemonade-server.exe', 'LemonadeServer.exe') -or
            $node.CreationDate -isnot [datetime] -or
            $node.CreationDate.ToUniversalTime() -lt $process.StartTime.ToUniversalTime()) { break }
        $owner = [int]$node.ParentProcessId
    }
    throw "The Lemonade listener on port $Port is not an owned child of the launched process."
}

function Invoke-ODSPortalLemonadeRuntime($Plan, [string]$ReadyPath) {
    # This function and its helpers are copied into the durable task launcher.
    # The task owns configuration/loading; the installer only awaits its proof.
    if (-not [IO.Path]::IsPathRooted([string]$Plan.ExecutablePath) -or
        -not [IO.Path]::IsPathRooted([string]$Plan.ModelsDir) -or
        [IO.Path]::GetFileName([string]$Plan.GgufFile) -ne [string]$Plan.GgufFile -or
        [string]::IsNullOrWhiteSpace([string]$Plan.GgufFile) -or
        [int]$Plan.ContextSize -lt 1 -or [int]$Plan.ContextSize -gt 10000000) {
        throw 'The saved Portal Lemonade plan is invalid.'
    }
    $contract = Get-ODSLemonadeLaunchContract -ExecutablePath $Plan.ExecutablePath `
        -Port $Plan.Port -ModelsDir $Plan.ModelsDir -ContextSize $Plan.ContextSize
    if ($contract.BindAddress -ne '127.0.0.1') {
        throw 'The Portal runtime launcher requires Lemonade on loopback.'
    }
    if (Test-Path -LiteralPath $ReadyPath) { Remove-Item -LiteralPath $ReadyPath -Force }
    if (@(Get-NetTCPConnection -LocalPort $Plan.Port -State Listen -ErrorAction SilentlyContinue).Count) {
        throw "Lemonade port $($Plan.Port) is already occupied; no existing process was changed."
    }
    $child = $null
    try {
        $child = Start-Process -FilePath $contract.ExecutablePath -ArgumentList $contract.ArgumentString `
            -WorkingDirectory (Split-Path -Parent $contract.ExecutablePath) -WindowStyle Hidden -PassThru
        if (-not (Wait-ODSPortalLemonadeHealth $Plan.Port 60)) {
            throw 'The Portal Lemonade process did not become healthy within 60 seconds.'
        }
        Assert-ODSPortalLemonadeListener $Plan.Port $child.Id $contract.ExecutablePath
        if ($contract.Modern) {
            $null = Set-ODSLemonadeModernRuntimeConfig -Port $Plan.Port -ModelsDir $Plan.ModelsDir -ContextSize $Plan.ContextSize
        }
        $modelId = Resolve-ODSLemonadeModelId -Port $Plan.Port -GgufFile $Plan.GgufFile -VersionOverride ([string]$contract.Version)
        Assert-ODSPortalLemonadeListener $Plan.Port $child.Id $contract.ExecutablePath
        Set-ODSLemonadeLoadedModel -Port $Plan.Port -ModelId $modelId -ContextSize $Plan.ContextSize -TimeoutSec 900
        Assert-ODSPortalLemonadeListener $Plan.Port $child.Id $contract.ExecutablePath
        $ready = @{ ProcessId = $child.Id; StartedAt = $child.StartTime.ToUniversalTime().ToString('o'); Port = $Plan.Port; ModelId = $modelId; ContextSize = $Plan.ContextSize }
        Write-ODSPrivateEnvFile -Path $ReadyPath -Content ($ready | ConvertTo-Json -Compress)
        $child.WaitForExit()
        return $child.ExitCode
    } catch {
        if ($child -and -not $child.HasExited) { $child.Kill(); $child.WaitForExit() }
        throw
    }
}

function New-ODSPortalLemonadeRuntimeAction($Contract, [string]$GgufFile) {
    # All dependencies live beside the private plan, never in a temporary
    # checkout. Existing private-file writer verifies owner-only Windows ACLs.
    $runtimeDir = Join-Path (Get-ODSPortalStateDir) 'portal-runtime'
    New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
    foreach ($name in @('backend-contract.ps1', 'env-generator.ps1')) {
        Write-ODSPrivateEnvFile -Path (Join-Path $runtimeDir $name) `
            -Content (Get-Content -LiteralPath (Join-Path $PSScriptRoot $name) -Raw)
    }
    $plan = [ordered]@{
        ExecutablePath = $Contract.ExecutablePath
        Port = $Contract.Port
        ModelsDir = $Contract.ModelsDir
        ContextSize = $Contract.ContextSize
        GgufFile = $GgufFile
    }
    Write-ODSPrivateEnvFile -Path (Join-Path $runtimeDir 'runtime.json') -Content ($plan | ConvertTo-Json -Compress)
    $readyPath = Join-Path $runtimeDir 'ready.json'
    if (Test-Path -LiteralPath $readyPath) { Remove-Item -LiteralPath $readyPath -Force }
    $definitions = foreach ($name in @('Test-ODSPortalLemonadeHealth', 'Wait-ODSPortalLemonadeHealth',
            'Assert-ODSPortalLemonadeListener', 'Invoke-ODSPortalLemonadeRuntime')) {
        "function $name {`n$((Get-Command $name -CommandType Function).Definition)`n}"
    }
    $launcher = @'
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'backend-contract.ps1')
. (Join-Path $PSScriptRoot 'env-generator.ps1')
__PORTAL_FUNCTIONS__
try {
    $plan = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime.json') -Raw | ConvertFrom-Json
    $code = Invoke-ODSPortalLemonadeRuntime $plan (Join-Path $PSScriptRoot 'ready.json')
    exit $code
} catch {
    Add-Content -LiteralPath (Join-Path $PSScriptRoot 'lemonade-launch.log') -Encoding UTF8 `
        -Value ("{0:o} Portal Lemonade startup failed: {1}" -f (Get-Date), $_.Exception.Message)
    $failure = @{ Error = 'Lemonade startup failed; check portal-runtime/lemonade-launch.log.' }
    Write-ODSPrivateEnvFile -Path (Join-Path $PSScriptRoot 'ready.json') -Content ($failure | ConvertTo-Json -Compress)
    exit 1
}
'@
    $launcherPath = Join-Path $runtimeDir 'launch.ps1'
    Write-ODSPrivateEnvFile -Path $launcherPath -Content $launcher.Replace('__PORTAL_FUNCTIONS__', ($definitions -join "`n"))
    $shell = Get-Command pwsh.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $shellPath = if ($shell) { $shell.Source } else { 'powershell.exe' }
    $action = New-ScheduledTaskAction -Execute $shellPath `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcherPath`"" -WorkingDirectory $runtimeDir
    return [pscustomobject]@{ Action = $action; Plan = $plan; ReadyPath = $readyPath }
}

function Wait-ODSPortalLemonadeReady($Registration, [int]$Seconds = 1020) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $Registration.ReadyPath -PathType Leaf) {
            $ready = Get-Content -LiteralPath $Registration.ReadyPath -Raw | ConvertFrom-Json
            if ($ready.Error) { throw [string]$ready.Error }
            if ($ready.Port -ne $Registration.Plan.Port -or $ready.ContextSize -ne $Registration.Plan.ContextSize -or
                [string]::IsNullOrWhiteSpace([string]$ready.ModelId)) { throw 'The Portal Lemonade ready record does not match its plan.' }
            Assert-ODSPortalLemonadeListener $ready.Port $ready.ProcessId $Registration.Plan.ExecutablePath
            return [string]$ready.ModelId
        }
        Start-Sleep -Seconds 2
    }
    throw "Lemonade did not finish restoring its model. Check $(Join-Path (Split-Path -Parent $Registration.ReadyPath) 'lemonade-launch.log')."
}

function Register-ODSPortalLemonadeTask($Contract, [string]$GgufFile = '') {
    # One task for this Windows user: starts at sign-in (so the model survives a
    # restart) and now. It binds 127.0.0.1 only.
    $registration = New-ODSPortalLemonadeRuntimeAction $Contract $GgufFile
    $action = $registration.Action
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName $script:ODSPortalLemonadeTaskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal (New-ODSInteractiveScheduledTaskPrincipal -RunLevel Limited) `
        -Description 'ODS: Lemonade Server for the AMD GPU (127.0.0.1). Starts at sign-in.' -Force | Out-Null
    Start-ScheduledTask -TaskName $script:ODSPortalLemonadeTaskName
    return $registration
}

function Initialize-ODSPortalAmdLemonade($Plan, [string]$SourceRoot, [bool]$NonInteractive) {
    # Returns the Linux installer arguments for the Windows Lemonade route, or
    # an empty array when the user keeps the CPU route.
    $exe = Install-ODSPortalLemonade $SourceRoot $NonInteractive
    if (-not $exe) {
        Write-Host '         Continuing without the GPU: the model will run on the CPU (slower).'
        return @()
    }
    $modelsDir = Get-ODSPortalLemonadeModel $Plan
    Stop-ODSPortalLemonade $exe
    $port = Select-ODSPortalLemonadePort
    $contract = Get-ODSLemonadeLaunchContract -ExecutablePath $exe -Port $port -ModelsDir $modelsDir -ContextSize $Plan.ContextSize
    Write-Host "         Starting Lemonade Server $($contract.Version) on 127.0.0.1:$port..."
    $registration = Register-ODSPortalLemonadeTask $contract $Plan.GgufFile
    Write-Host '         Restoring the configured GPU model (the first load also downloads the GPU runtime)...'
    $modelId = Wait-ODSPortalLemonadeReady $registration
    Write-Host "         GPU model ready: $modelId ($($Plan.ContextSize) tokens of context)."
    return @(
        '--lemonade-url', "http://localhost:$port",
        '--lemonade-host-transport', 'model-router',
        '--lemonade-model', $modelId,
        '--lemonade-gpu-name', $Plan.GpuName,
        '--lemonade-gpu-vram-mb', [string]$Plan.VramMB
    )
}
