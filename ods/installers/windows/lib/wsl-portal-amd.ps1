# AMD GPUs for the Windows -> WSL Portal path.
#
# Docker Desktop passes only NVIDIA GPUs into WSL containers, and ROCm does not
# run inside them, so an AMD GPU is used by ggml-org's llama-server.exe (Vulkan)
# running natively on Windows as this user's task ODSLlamaServerRuntime-<SID>.
# It listens on 127.0.0.1 only and requires an API key. The Linux installer
# points LiteLLM, the model router and Pixel at it (--native-llm-*); containers
# reach Windows loopback via host.docker.internal.
# The task, cutover, options and port helpers are vendor-neutral: the CUDA
# build for NVIDIA GPUs on Windows on Arm uses them too (not routed yet).
# Requires wsl-portal-setup.ps1 (Confirm-ODSPortalPreparation) in scope.

. (Join-Path $PSScriptRoot 'detection.ps1')
. (Join-Path $PSScriptRoot 'tier-map.ps1')
. (Join-Path $PSScriptRoot 'backend-contract.ps1')
. (Join-Path $PSScriptRoot 'native-llama-args.ps1')
. (Join-Path $PSScriptRoot 'native-llama-runtime.ps1')
. (Join-Path $PSScriptRoot 'wsl-portal-lemonade-migration.ps1')

$script:ODSPortalRuntimeTaskPrefix = 'ODSLlamaServerRuntime-'
# New installs try the pinned 8080, then two quiet ports. Lemonade's own
# defaults (13305, 8000) are never taken: a user's Lemonade keeps them.
# Migrations and reruns keep the port the WSL side already uses.
$script:ODSPortalRuntimePortCandidates = @(8080, 18080, 28080)
$script:ODSPortalReadySeconds = 1020
# Setup waits this long for a model change the dashboard started (a switch
# can load for up to the readiness limit above).
$script:ODSPortalRuntimeChangeWaitSeconds = 1200
# The key reaches the Linux installer through this WSLENV-forwarded variable,
# never on a command line (contract: --native-llm-api-key-env).
$script:ODSPortalApiKeyVariable = 'ODS_NATIVE_LLM_API_KEY'
# memory-plan.json and start-override.json exist only on the CUDA route: the
# figures the launcher checks at every start, and the owner's Start anyway.
$script:ODSPortalRuntimeOwnedFiles = @('runtime.json', 'runtime-options.json', 'launch.ps1', 'native-llama-runtime.ps1',
    'private-file.ps1', 'api-key', 'intent.json', 'ready.json', 'process-ownership.json', 'memory-plan.json', 'start-override.json')
# The scheduled task's description, by llama.cpp backend. Vulkan is the AMD
# route and the default.
$script:ODSPortalRuntimeTaskDescriptions = @{
    vulkan = 'ODS: llama.cpp model server for the AMD GPU (127.0.0.1, API key). Starts at sign-in.'
    cuda = 'ODS: llama.cpp model server for the NVIDIA GPU (127.0.0.1, API key). Starts at sign-in.'
}
# The CUDA build's GPU check runs on the first free of these quiet ports.
$script:ODSPortalCheckPortCandidates = @(18090, 28090, 38090)
# Explains the variable's AMD name wherever setup asks for it.
$script:ODSPortalPortVariableNote = 'the AMD_INFERENCE_PORT environment variable (ODS uses that name for its Windows model server on every GPU)'

function Get-ODSPortalStateDir {
    # Kept as ODS\lemonade this round: the WSL side registered this plan path
    # and refuses a changed one without an explicit migration.
    return (Join-Path $env:LOCALAPPDATA 'ODS\lemonade')
}

function Get-ODSPortalRuntimeDir { return (Join-Path (Get-ODSPortalStateDir) 'portal-runtime') }

function Get-ODSPortalModelsDir { return (Join-Path (Get-ODSPortalStateDir) 'models') }

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
    try {
        $config = Resolve-CatalogModelRecommendation -TierConfig $config -Tier $tier -GpuInfo $gpu `
            -SystemRamGB $ramGB -SourceRoot $SourceRoot -MinContext $script:HERMES_MIN_CONTEXT
    } catch {
        # Low reported VRAM can mean a reserved UMA framebuffer or an older
        # discrete card. Without a catalog fit, keep Pixel on the CPU route
        # rather than infer usable GPU memory from an adapter name.
        if ($_.Exception.Message -notlike 'No catalog model fits the detected memory*' -or
            $gpu.VramMB -ge 4096) { throw }
        Write-Host "         $($gpu.Name) has no verified model fit with its reported memory. Pixel will use the CPU route; no GPU capacity was assumed."
        return $null
    }
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

function Get-ODSPortalModel($Plan) {
    # Downloads the planned GGUF once into the Windows models folder, verifying
    # the pinned SHA-256.
    $modelsDir = Get-ODSPortalModelsDir
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

function Get-ODSPortalUserSid([string]$UserId) {
    if (-not $UserId) { return [Security.Principal.WindowsIdentity]::GetCurrent().User.Value }
    if ($UserId -match '^S-1-') { return (New-Object Security.Principal.SecurityIdentifier($UserId)).Value }
    return (New-Object Security.Principal.NTAccount($UserId)).Translate([Security.Principal.SecurityIdentifier]).Value
}

function Get-ODSPortalRuntimeTaskName {
    return ($script:ODSPortalRuntimeTaskPrefix + (Get-ODSPortalUserSid))
}

function Get-ODSPortalRuntimeTask {
    # Only this user's llama.cpp task. An unreadable task bearing our SID fails
    # closed; Lemonade-era names are handled by the retire-only migration.
    $name = Get-ODSPortalRuntimeTaskName
    $lookupErrors = @()
    $tasks = @(Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction SilentlyContinue -ErrorVariable lookupErrors)
    if (@($lookupErrors | Where-Object { $_.CategoryInfo.Category -ne 'ObjectNotFound' }).Count) {
        throw 'Cannot inspect the Portal model runtime task; no process was changed.'
    }
    if (-not $tasks.Count) { return $null }
    if ($tasks.Count -ne 1 -or $tasks[0].TaskPath -ne '\' -or
        [string]::IsNullOrWhiteSpace([string]$tasks[0].Principal.UserId)) {
        throw 'The Portal model runtime task identity is ambiguous.'
    }
    if ((Get-ODSPortalUserSid $tasks[0].Principal.UserId) -ne (Get-ODSPortalUserSid)) {
        throw 'The Portal model runtime task belongs to a different Windows user.'
    }
    return $tasks[0]
}

function Get-ODSPortalTaskEngineId([string]$TaskName = (Get-ODSPortalRuntimeTaskName)) {
    $scheduler = New-Object -ComObject Schedule.Service
    $scheduler.Connect()
    $instances = @($scheduler.GetFolder('\').GetTask($TaskName).GetInstances(0))
    if ($instances.Count -gt 1) { throw 'More than one Portal runtime task instance is running; ownership is ambiguous.' }
    if ($instances.Count -eq 1) { return [int]$instances[0].EnginePID }
    return 0
}

function Get-ODSPortalLauncherArguments {
    return ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path (Get-ODSPortalRuntimeDir) 'launch.ps1') + '"')
}

function Get-ODSPortalRuntimeOwnedLaunch($Task) {
    # The llama.cpp task runs exactly the durable launcher beside its private
    # plan, and that plan names an ODS llama-server.exe and model store.
    $actions = @($Task.Actions)
    if ($actions.Count -ne 1) { throw 'The Portal model runtime task has an unrecognized action; no process was stopped.' }
    $action = $actions[0]
    $runtimeDir = Get-ODSPortalRuntimeDir
    $shells = @(Get-Command pwsh.exe, powershell.exe -CommandType Application -ErrorAction SilentlyContinue)
    $shell = @($shells | Where-Object { $_.Source -ieq $action.Execute -or $_.Name -ieq $action.Execute })
    if ($shell.Count -ne 1 -or $action.Arguments -cne (Get-ODSPortalLauncherArguments) -or $action.WorkingDirectory -ine $runtimeDir) {
        throw 'The Portal model runtime task does not use the managed durable launcher; no process was stopped.'
    }
    $taskName = if ($Task.TaskName) { $Task.TaskName } else { Get-ODSPortalRuntimeTaskName }
    $planPath = Join-Path $runtimeDir 'runtime.json'
    # Uninstall keeps the task registered; deleting ODS files afterwards
    # leaves it without the plan that proves which process it started.
    if (-not (Test-Path -LiteralPath $planPath -PathType Leaf)) {
        throw ("The Portal model runtime task $taskName has no saved runtime plan ($planPath), so ODS cannot prove " +
            "which process it started; no process was stopped. If you deleted ODS files, remove the task from normal " +
            "PowerShell, then rerun setup: Unregister-ScheduledTask -TaskPath '\' -TaskName '$taskName' -Confirm:`$false")
    }
    $plan = Read-ODSNativeLlamaJson $planPath
    if (-not [IO.Path]::IsPathRooted([string]$plan.ExecutablePath) -or
        [IO.Path]::GetFileName([string]$plan.ExecutablePath) -ine 'llama-server.exe' -or
        $plan.ModelsDir -ine (Get-ODSPortalModelsDir) -or
        ($plan.Port -isnot [int] -and $plan.Port -isnot [long])) {
        throw 'The saved Portal runtime plan does not match the managed llama-server; no process was stopped.'
    }
    return [pscustomobject]@{ TaskName = $taskName; Action = $action; ActionExe = $shell[0].Source
        ExecutablePath = [string]$plan.ExecutablePath; Port = [int]$plan.Port; Plan = $plan }
}

function Stop-ODSPortalTaskTree {
    <#
    .SYNOPSIS
        Stop only the process tree a validated ODS task started, proven by the
        task engine, the private ownership record or the readiness record.
    #>
    param($Task, [string]$TaskName, $Action, [string]$ActionExe, [string]$ExecutablePath, [int]$Port, [bool]$Durable)
    if ($Port -lt 1 -or $Port -gt 65535) { throw 'The existing Portal runtime task has an invalid port.' }
    # Every stop path, including uninstall, must disarm scheduled retries.
    # Publish first so even an already queued durable launcher exits cleanly.
    $armed = [string]$Task.State -ne 'Disabled'
    $progress = @{ Stopping = $false }
    try {
        Set-ODSPortalRuntimeIntent 'stopped'
        Disable-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction Stop | Out-Null
        Stop-ODSPortalDisarmedTaskTree @PSBoundParameters -Progress $progress
    } catch {
        $failure = $_
        # A refusal stopped nothing: sign-in startup goes back as it was, so a
        # refused rerun or Unload never leaves the model off after a restart.
        # Once stopping began the model may be down, and it stays disarmed as
        # the Unload or uninstall that asked for the stop expects.
        if ($armed -and -not $progress.Stopping) {
            try {
                Set-ODSPortalRuntimeIntent 'running'
                Enable-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction Stop | Out-Null
            } catch {
                throw ("$($failure.Exception.Message) Sign-in startup of the model could not be turned back on " +
                    "either: $($_.Exception.Message) Run Windows setup again to restore it.")
            }
        }
        throw $failure
    }
}

function Stop-ODSPortalDisarmedTaskTree {
    # $Progress.Stopping turns true just before the first task or process is
    # stopped; a failure before that point is a refusal that changed nothing.
    param($Task, [string]$TaskName, $Action, [string]$ActionExe, [string]$ExecutablePath, [int]$Port, [bool]$Durable,
        [hashtable]$Progress = @{})
    $runtimeDir = Get-ODSPortalRuntimeDir
    $readyPath = Join-Path $runtimeDir 'ready.json'
    $ownershipPath = Join-Path $runtimeDir 'process-ownership.json'

    $engineId = Get-ODSPortalTaskEngineId $TaskName
    $nodes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $root = $null
    $recovered = @()
    if ($engineId -gt 0) {
        # On current Windows the engine PID is the action itself. Older task
        # engines can parent it; require the exact action and a unique match.
        $commandLines = foreach ($exe in @($ActionExe, [string]$Action.Execute) | Select-Object -Unique) {
            '"' + $exe + '" ' + $Action.Arguments
            $exe + ' ' + $Action.Arguments
        }
        $candidates = @($nodes | Where-Object {
            $_.ExecutablePath -ieq $ActionExe -and $_.CommandLine -cin $commandLines -and
            ($_.ProcessId -eq $engineId -or $_.ParentProcessId -eq $engineId)
        })
        if ($candidates.Count -ne 1) { throw 'Cannot prove the running Portal runtime task process; no process was stopped.' }
        $root = $candidates[0]
        if ($root.ProcessId -ne $engineId) {
            $engines = @($nodes | Where-Object { $_.ProcessId -eq $engineId })
            if ($engines.Count -ne 1 -or $engines[0].CreationDate -isnot [datetime] -or
                $root.CreationDate.ToUniversalTime() -lt $engines[0].CreationDate.ToUniversalTime()) {
                throw 'The Portal task engine ancestry cannot be proved; no process was stopped.'
            }
        }
    } elseif ($Durable -and (Test-Path -LiteralPath $ownershipPath -PathType Leaf)) {
        # Startup failure is not readiness. Its private ownership record remains
        # usable after a partial cleanup or after the task wrapper has exited.
        $ownership = Get-Content -LiteralPath $ownershipPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($ownership.ExecutablePath -ine $ExecutablePath -or $ownership.Port -ne $Port -or
            -not @($ownership.Processes).Count) { throw 'The saved runtime ownership does not match its launch plan.' }
        foreach ($saved in $ownership.Processes) {
            $started = [datetime]$saved.StartedAt
            if ([int]$saved.ProcessId -lt 1 -or -not [IO.Path]::IsPathRooted([string]$saved.ExecutablePath)) {
                throw 'The saved runtime process ownership is invalid.'
            }
            if ($saved.ExitedAt) {
                $exited = [datetime]$saved.ExitedAt
                if ($exited.ToUniversalTime() -lt $started.ToUniversalTime()) { throw 'The saved runtime process lifetime is invalid.' }
                $recovered += [pscustomobject]@{ ProcessId = $saved.ProcessId; CreationDate = $started
                    ExecutablePath = $saved.ExecutablePath; ExitedAt = $exited }
                continue
            }
            $matchesById = @($nodes | Where-Object {
                $_.ProcessId -eq $saved.ProcessId -and $_.ExecutablePath -ieq $saved.ExecutablePath -and
                $_.CreationDate -is [datetime] -and
                [math]::Abs(($_.CreationDate.ToUniversalTime() - $started.ToUniversalTime()).TotalMilliseconds) -lt 1
            })
            if ($matchesById.Count -gt 1) { throw 'The saved runtime process ownership is ambiguous.' }
            # A missing or recycled PID is never a reason to stop its new owner.
            $recovered += $matchesById
        }
    } elseif ($Durable -and (Test-Path -LiteralPath $readyPath -PathType Leaf)) {
        $ready = Get-Content -LiteralPath $readyPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not $ready.Error -and $ready.ProcessId -and $ready.StartedAt) {
            $matchesById = @($nodes | Where-Object { $_.ProcessId -eq $ready.ProcessId })
            if ($matchesById.Count -eq 1 -and $matchesById[0].ExecutablePath -ieq $ExecutablePath -and
                $matchesById[0].CreationDate -is [datetime] -and
                [math]::Abs(($matchesById[0].CreationDate.ToUniversalTime() - ([datetime]$ready.StartedAt).ToUniversalTime()).TotalMilliseconds) -lt 1) {
                $root = $matchesById[0]
            } elseif ($matchesById.Count -or @($nodes | Where-Object { $_.ParentProcessId -eq $ready.ProcessId }).Count) {
                throw 'The saved runtime process identity is stale or has orphaned children; ownership cannot be proved.'
            }
        }
    }
    if (-not $root -and -not $recovered.Count) {
        if ($Task.State -notin @('Ready', 'Disabled') -or @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue).Count) {
            throw "Cannot prove ownership of the model runtime on port $Port. No process was stopped; close the previous ODS runtime explicitly before retrying."
        }
        return
    }

    # Hold process handles before stopping the task, so a recycled PID cannot
    # redirect a later Kill(). Children may live outside the runtime folder.
    $roots = if ($root) { @($root) } else { $recovered }
    $owned = Get-ODSPortalOwnedProcessTree $roots $nodes $ExecutablePath
    try {
        $currentTasks = @(Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction Stop)
        if ($currentTasks.Count -ne 1 -or $currentTasks[0].TaskPath -ne $Task.TaskPath -or
            $currentTasks[0].Principal.UserId -ne $Task.Principal.UserId -or @($currentTasks[0].Actions).Count -ne 1 -or
            $currentTasks[0].Actions[0].Execute -ne $Action.Execute -or $currentTasks[0].Actions[0].Arguments -cne $Action.Arguments -or
            $currentTasks[0].Actions[0].WorkingDirectory -ne $Action.WorkingDirectory -or (Get-ODSPortalTaskEngineId $TaskName) -ne $engineId) {
            throw 'The Portal runtime task changed during ownership verification; no process was stopped.'
        }
        $Progress.Stopping = $true
        if ($engineId -gt 0) { Stop-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction Stop }
        Stop-ODSPortalOwnedProcesses $owned.Handles
        if ($Durable -and (Test-Path -LiteralPath $ownershipPath)) { Remove-Item -LiteralPath $ownershipPath -Force }
    } finally {
        foreach ($process in $owned.Handles) { $process.Dispose() }
    }
}

function Stop-ODSPortalRuntime([string]$ExecutablePath = '') {
    # Stops the proven tree of this user's llama.cpp task; no task, no change.
    $task = Get-ODSPortalRuntimeTask
    if (-not $task) { return }
    $launch = Get-ODSPortalRuntimeOwnedLaunch $task
    if ($ExecutablePath -and $launch.ExecutablePath -ine $ExecutablePath) {
        throw 'The saved Portal runtime plan names a different llama-server; no process was stopped.'
    }
    Stop-ODSPortalTaskTree -Task $task -TaskName $launch.TaskName -Action $launch.Action -ActionExe $launch.ActionExe `
        -ExecutablePath $launch.ExecutablePath -Port $launch.Port -Durable $true
}

function Get-ODSPortalPortOwner([int]$Port) {
    # Process name listening on the port, or $null when it is free.
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { return $null }
    $process = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    if ($process) { return $process.ProcessName }
    return "process $($listener.OwningProcess)"
}

function Test-ODSPortalPortBindable([int]$Port) {
    # Excluded Hyper-V ports have no listener, yet bind fails with AccessDenied.
    # Probe the exact address llama-server uses; the real launch remains authoritative.
    $probe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $Port)
    $probe.ExclusiveAddressUse = $true
    try { $probe.Start(); return $true }
    catch [Net.Sockets.SocketException] {
        if ($_.Exception.SocketErrorCode -notin @('AccessDenied', 'AddressAlreadyInUse')) { throw }
        return $false
    } finally { $probe.Stop() }
}

function Assert-ODSPortalPortAvailable([int]$Port) {
    $owner = Get-ODSPortalPortOwner $Port
    if ($owner) { throw "Port $Port is used by '$owner'. Choose a free port with $($script:ODSPortalPortVariableNote), then rerun." }
    if (-not (Test-ODSPortalPortBindable $Port)) {
        throw "Port $Port cannot bind Windows loopback (it may be reserved after a restart). Choose a free port with $($script:ODSPortalPortVariableNote), then rerun."
    }
}

function Select-ODSPortalRuntimePort {
    if ($env:AMD_INFERENCE_PORT) {
        $port = [int]$env:AMD_INFERENCE_PORT
        if ($port -lt 1 -or $port -gt 65535) { throw 'AMD_INFERENCE_PORT must be between 1 and 65535.' }
        $owner = Get-ODSPortalPortOwner $port
        if ($owner) { throw "AMD_INFERENCE_PORT $port is already used by '$owner'. Choose a free port or remove AMD_INFERENCE_PORT, then rerun." }
        if (-not (Test-ODSPortalPortBindable $port)) { throw "AMD_INFERENCE_PORT $port cannot bind Windows loopback (it may be reserved). Choose a different port." }
        return $port
    }
    $taken = @()
    foreach ($port in $script:ODSPortalRuntimePortCandidates) {
        $owner = Get-ODSPortalPortOwner $port
        if (-not $owner -and (Test-ODSPortalPortBindable $port)) { return $port }
        $reason = if ($owner) { $owner } else { 'reserved or unavailable for binding' }
        $taken += "$port ($reason)"
    }
    throw "No free port for the llama.cpp model server; all are in use: $($taken -join ', '). Choose a free port with $($script:ODSPortalPortVariableNote), then rerun."
}

function Select-ODSPortalCheckPort {
    # The CUDA build's GPU check never takes the model server's ports.
    $taken = @()
    foreach ($port in $script:ODSPortalCheckPortCandidates) {
        $owner = Get-ODSPortalPortOwner $port
        if (-not $owner -and (Test-ODSPortalPortBindable $port)) { return $port }
        $reason = if ($owner) { $owner } else { 'reserved or unavailable for binding' }
        $taken += "$port ($reason)"
    }
    throw "No free port for the llama.cpp GPU check; all are in use: $($taken -join ', '). Close the programs using them, or restart Windows, then rerun setup."
}

function Set-ODSPortalRuntimeIntent([ValidateSet('running', 'stopped')][string]$State) {
    Write-ODSPrivateEnvFile -Path (Join-Path (Get-ODSPortalRuntimeDir) 'intent.json') -Content (@{ State = $State } | ConvertTo-Json -Compress)
}

function Get-ODSPortalControlMutexName {
    return ('Global\ODS-PortalModelControl-' + (Get-ODSPortalUserSid))
}

function Enter-ODSPortalRuntimeChange([hashtable]$Lock) {
    <#
    .SYNOPSIS
        Setup's turn to change the Windows model runtime. The dashboard's model
        controller holds the same mutex for every switch, Unload and Resume, so
        a rerun waits for one that is running instead of racing its stop.
        The caller's $Lock holds the mutex before any wait begins, so its
        finally (Exit-ODSPortalRuntimeChange) releases the lock even when
        Ctrl+C stops setup just as a wait takes it.
    #>
    $Lock.Mutex = [Threading.Mutex]::new($false, (Get-ODSPortalControlMutexName))
    $held = $false
    # An abandoned mutex (a controller that ended without releasing it) is
    # handed to this thread, so setup takes over.
    try { $held = $Lock.Mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $held = $true }
    if ($held) { return }
    Write-Host '         A model change started from the dashboard is still running; setup continues once it finishes.'
    # Short waits keep Ctrl+C working while setup waits.
    $deadline = [datetime]::UtcNow.AddSeconds($script:ODSPortalRuntimeChangeWaitSeconds)
    while (-not $held -and [datetime]::UtcNow -lt $deadline) {
        try { $held = $Lock.Mutex.WaitOne(2000) } catch [Threading.AbandonedMutexException] { $held = $true }
    }
    if (-not $held) {
        throw ('A model change started from the dashboard or with ods model is still running, so setup did not change ' +
            'the model runtime. Let it finish, or restart Windows, then run setup again.')
    }
}

function Exit-ODSPortalRuntimeChange([hashtable]$Lock) {
    if (-not $Lock.Mutex) { return }
    # Setup can stop before a wait took the lock, or just after. Only a lock
    # this thread holds is released; ReleaseMutex refuses any other
    # (ApplicationException), and that refusal needs no action.
    try { $Lock.Mutex.ReleaseMutex() } catch [ApplicationException] { }
    $Lock.Mutex.Dispose()
    $Lock.Mutex = $null
}

function Assert-ODSPortalWslBinding([string]$WslDistro, [string]$WslInstallDir) {
    if ($WslDistro -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$' -or $WslDistro -match '^docker-desktop' -or
        $WslInstallDir -notmatch '^/[^\x00-\x1f\x7f]+$' -or $WslInstallDir -match '(^|/)\.\.?(/|$)' -or
        $WslInstallDir.Contains('//') -or $WslInstallDir.EndsWith('/')) {
        throw 'An explicit WSL distribution and normalized absolute Linux installation directory are required.'
    }
}

function Get-ODSPortalApiKey {
    # Reruns keep the key the WSL side already stores; a new runtime gets one.
    $path = Join-Path (Get-ODSPortalRuntimeDir) 'api-key'
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        try { return (Read-ODSNativeLlamaApiKey $path) } catch { }
    }
    return (New-ODSNativeLlamaApiKey)
}

function New-ODSPortalRuntimeOptions($Runtime, $Qualification, [string]$Backend = 'vulkan', [string]$Architecture = 'x64') {
    if ($Backend -cnotin @('vulkan', 'cuda') -or $Architecture -cnotin @('x64', 'arm64')) {
        throw "Unknown llama.cpp runtime options: backend '$Backend', architecture '$Architecture'."
    }
    $runtimeDir = Get-ODSPortalRuntimeDir
    # Thinking off by default, as Docker's LLAMA_ARG_REASONING=off; the binary
    # is asked whether it has --reasoning before the flag is used.
    $reasoning = @(Get-ODSNativeReasoningArgs -Executable $Runtime.ExecutablePath -Mode 'off' -FallbackFormat 'none')
    $options = [ordered]@{
        schemaVersion = 1
        Device = [string]$Qualification.Device.Name
        NGpuLayers = 'auto'
        ApiKeyPath = (Join-Path $runtimeDir 'api-key')
        LogPath = (Join-Path $runtimeDir 'llama-server.log')
        ReleaseTag = [string]$Runtime.ReleaseTag
        ZipSha256 = [string]$Runtime.ZipSha256
        ReasoningArguments = $reasoning
        ExtraArguments = @()
        # Optional plan fields the launcher copied with these options reads.
        # The model controller plans a projector only when this lists it, so
        # an older launcher (setup not rerun since) is never handed one.
        PlanFeatures = @('MmprojFile')
    }
    if ($Backend -ceq 'cuda') {
        # Vulkan (AMD) options stay exactly as they were, with no Backend or
        # Architecture. CUDA puts every layer on the GPU; with the launcher's
        # --fit off, llama.cpp never moves part of the model to the CPU.
        $options.NGpuLayers = 'all'
        $options.Backend = 'cuda'
        $options.Architecture = $Architecture
    }
    return $options
}

function Get-ODSGgufSplitSiblings([string]$File) {
    <#
    .SYNOPSIS
        Every part of a split GGUF (name-00001-of-00003.gguf: all three), or
        the file alone (pure). llama.cpp loads the other parts from the first.
    #>
    $match = [regex]::Match($File, '^(?<stem>.+)-(?<index>[0-9]{5})-of-(?<count>[0-9]{5})\.gguf$')
    if (-not $match.Success) { return @($File) }
    $count = [int]$match.Groups['count'].Value
    $index = [int]$match.Groups['index'].Value
    if ($count -lt 1 -or $index -lt 1 -or $index -gt $count) { return @($File) }
    $stem = $match.Groups['stem'].Value
    return @(1..$count | ForEach-Object { '{0}-{1:00000}-of-{2:00000}.gguf' -f $stem, $_, $count })
}

function New-ODSPortalMemoryPlanRecord($Plan, $Need, [double]$WslGiB) {
    # memory-plan.json for a CUDA plan: what the launcher checks at each start.
    $record = [ordered]@{
        GgufFile = [string]$Plan.GgufFile
        ContextSize = [long]$Plan.ContextSize
        DeviceNeedGiB = [math]::Round([double]$Need.DeviceNeedGiB, 2)
        CommitNeedGiB = [math]::Round([double]$Need.CommitNeedGiB, 2)
        WslGiB = [math]::Round($WslGiB, 2)
        Source = [string]$Need.Source
    }
    if (-not (Test-ODSNativeMemoryPlanRecord ([pscustomobject]$record))) { throw "The memory plan for $($Plan.GgufFile) is invalid." }
    return $record
}

function New-ODSPortalRuntimeAction($Plan, $Options, [string]$ApiKey, [string]$WslDistro = '', [string]$WslInstallDir = '',
    # The CUDA route's memory-plan.json record (New-ODSPortalMemoryPlanRecord);
    # without one, no memory check runs (the AMD route).
    $MemoryPlan = $null) {
    # All dependencies live beside the private plan, never in a temporary
    # checkout. The private writer verifies owner-only Windows ACLs.
    if ($WslDistro -or $WslInstallDir) { Assert-ODSPortalWslBinding $WslDistro $WslInstallDir }
    $runtimeDir = Get-ODSPortalRuntimeDir
    New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
    foreach ($name in @('native-llama-runtime.ps1', 'private-file.ps1')) {
        Write-ODSPrivateFileBytes -Path (Join-Path $runtimeDir $name) -Bytes ([IO.File]::ReadAllBytes((Join-Path $PSScriptRoot $name)))
    }
    Write-ODSNativeLlamaApiKeyFile (Join-Path $runtimeDir 'api-key') $ApiKey
    Write-ODSPrivateEnvFile -Path (Join-Path $runtimeDir 'runtime-options.json') -Content ($Options | ConvertTo-Json -Depth 4 -Compress)
    # Exactly the key set the WSL bridge accepts; extra launch options stay in
    # runtime-options.json, whose PlanFeatures is all the controller reads.
    # ($plan below replaces the $Plan parameter: PowerShell names ignore case.)
    $projector = $Plan.MmprojFile
    $plan = [ordered]@{
        ExecutablePath = [string]$Plan.ExecutablePath
        Port = [int]$Plan.Port
        ModelsDir = [string]$Plan.ModelsDir
        ContextSize = [int]$Plan.ContextSize
        GgufFile = [string]$Plan.GgufFile
    }
    if ($WslDistro -or $WslInstallDir) {
        $plan.WslDistro = $WslDistro
        $plan.WslInstallDir = $WslInstallDir
    }
    if ($null -ne $projector) { $plan.MmprojFile = [string]$projector }
    Write-ODSPrivateEnvFile -Path (Join-Path $runtimeDir 'runtime.json') -Content ($plan | ConvertTo-Json -Compress)
    # A new plan never inherits an older plan's memory figures or Start anyway.
    $memoryPath = Join-Path $runtimeDir 'memory-plan.json'
    if ($null -ne $MemoryPlan) {
        Write-ODSPrivateEnvFile -Path $memoryPath -Content ($MemoryPlan | ConvertTo-Json -Compress)
    } elseif (Test-Path -LiteralPath $memoryPath) { Remove-Item -LiteralPath $memoryPath -Force }
    $overridePath = Join-Path $runtimeDir 'start-override.json'
    if (Test-Path -LiteralPath $overridePath) { Remove-Item -LiteralPath $overridePath -Force }
    Set-ODSPortalRuntimeIntent 'running'
    $readyPath = Join-Path $runtimeDir 'ready.json'
    if (Test-Path -LiteralPath $readyPath) { Remove-Item -LiteralPath $readyPath -Force }
    $launcher = @'
$ErrorActionPreference = 'Stop'
try {
    . (Join-Path $PSScriptRoot 'native-llama-runtime.ps1')
    if (-not (Test-ODSNativeLlamaWanted (Join-Path $PSScriptRoot 'intent.json'))) { exit 0 }
    $plan = Read-ODSNativeLlamaJson (Join-Path $PSScriptRoot 'runtime.json')
    $options = Read-ODSNativeLlamaJson (Join-Path $PSScriptRoot 'runtime-options.json')
    $code = Invoke-ODSNativeLlamaRuntime $plan $options (Join-Path $PSScriptRoot 'ready.json')
    exit $code
} catch {
    $exception = $_.Exception
    $message = $exception.Message
    Add-Content -LiteralPath (Join-Path $PSScriptRoot 'native-llama-launch.log') -Encoding UTF8 `
        -Value ("{0:o} Portal llama-server startup failed: {1}" -f (Get-Date), $message)
    $record = [ordered]@{ Error = ('llama-server startup failed: ' + $message) }
    if ($exception.Data['refusal']) {
        # A memory refusal (CUDA): its own text, its code and the plan it was
        # for, so the Models page can offer Start anyway for that plan.
        $record = [ordered]@{ Error = $message; Code = [string]$exception.Data['refusal']
            GgufFile = [string]$exception.Data['GgufFile']; ContextSize = [long]$exception.Data['ContextSize'] }
    }
    $failure = $record | ConvertTo-Json -Compress
    if (Get-Command Write-ODSPrivateEnvFile -ErrorAction SilentlyContinue) {
        Write-ODSPrivateEnvFile -Path (Join-Path $PSScriptRoot 'ready.json') -Content $failure
    } else {
        [IO.File]::WriteAllText((Join-Path $PSScriptRoot 'ready.json'), $failure)
    }
    exit 1
}
'@
    $launcherPath = Join-Path $runtimeDir 'launch.ps1'
    Write-ODSPrivateEnvFile -Path $launcherPath -Content $launcher
    $shell = Get-Command pwsh.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $shellPath = if ($shell) { $shell.Source } else { 'powershell.exe' }
    $action = New-ScheduledTaskAction -Execute $shellPath -Argument (Get-ODSPortalLauncherArguments) -WorkingDirectory $runtimeDir
    return [pscustomobject]@{ Action = $action; Plan = $plan; ReadyPath = $readyPath; Options = $Options }
}

function Get-ODSPortalLogExcerpt {
    # The last lines of the llama-server log, never a line about API keys.
    $log = Join-Path (Get-ODSPortalRuntimeDir) 'llama-server.log'
    if (-not (Test-Path -LiteralPath $log -PathType Leaf)) { return '' }
    $lines = @(Get-Content -LiteralPath $log -Tail 40 -ErrorAction SilentlyContinue | Where-Object {
        $_ -and $_ -notmatch '(?i)api[_-]?key|authorization|bearer'
    } | Select-Object -Last 8 | ForEach-Object { if ($_.Length -gt 240) { $_.Substring(0, 240) } else { $_ } })
    if (-not $lines.Count) { return '' }
    return "`n         llama-server.log:`n           " + ($lines -join "`n           ")
}

function Wait-ODSPortalRuntimeReady($Registration, [int]$Seconds = $script:ODSPortalReadySeconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $Registration.ReadyPath -PathType Leaf) {
            $ready = Get-Content -LiteralPath $Registration.ReadyPath -Raw -Encoding UTF8 | ConvertFrom-Json
            # A memory refusal ran no server, so the server log says nothing about it.
            if ($ready.Error -and $ready.Code) { throw ([string]$ready.Error) }
            if ($ready.Error) { throw ([string]$ready.Error + (Get-ODSPortalLogExcerpt)) }
            if ($ready.Port -ne $Registration.Plan.Port -or $ready.ContextSize -ne $Registration.Plan.ContextSize -or
                [string]$ready.ModelId -cne [string]$Registration.Plan.GgufFile) { throw 'The Portal llama-server ready record does not match its plan.' }
            Assert-ODSNativeLlamaListener $ready.Port $ready.ProcessId $Registration.Plan.ExecutablePath
            return [string]$ready.ModelId
        }
        Start-Sleep -Seconds 2
    }
    throw ("llama-server did not finish loading the model. Check $(Join-Path (Split-Path -Parent $Registration.ReadyPath) 'llama-server.log')." + (Get-ODSPortalLogExcerpt))
}

function Register-ODSPortalRuntimeTask($Registration, [string]$Description = '') {
    # One task for this Windows user: starts at sign-in (so the model survives a
    # restart). It binds 127.0.0.1 only. Started separately by the caller.
    # Without a description it is the AMD task, as before.
    if (-not $Description) { $Description = $script:ODSPortalRuntimeTaskDescriptions['vulkan'] }
    $taskName = Get-ODSPortalRuntimeTaskName
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User (Get-ODSPortalUserSid)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $taskName -TaskPath '\' -Action $Registration.Action -Trigger $trigger `
        -Settings $settings -Principal (New-ODSInteractiveScheduledTaskPrincipal -RunLevel Limited) `
        -Description $Description -Force | Out-Null
    return $taskName
}

function Get-ODSPortalExistingRuntime {
    <#
    .SYNOPSIS
        What ODS already runs for this user, validated before anything
        changes: the llama.cpp task, ODS-owned Lemonade tasks, or nothing.
    #>
    $task = Get-ODSPortalRuntimeTask
    $launches = @(Get-ODSPortalLemonadeLaunches)
    if ($task) {
        $launch = Get-ODSPortalRuntimeOwnedLaunch $task
        return [pscustomobject]@{ Kind = 'llama-server'; Task = $task; Launch = $launch
            WasEnabled = ([string]$task.State -ne 'Disabled'); Launches = $launches
            Selection = [pscustomobject]@{ Port = $launch.Port; GgufFile = [string]$launch.Plan.GgufFile; ContextSize = $launch.Plan.ContextSize
                MmprojFile = $launch.Plan.MmprojFile } }
    }
    if ($launches.Count) {
        $primary = $launches[0].Launch
        return [pscustomobject]@{ Kind = 'lemonade'; Task = $launches[0].Task; Launch = $primary; WasEnabled = $primary.WasEnabled
            Launches = $launches
            Selection = [pscustomobject]@{ Port = $primary.Port; GgufFile = $primary.GgufFile; ContextSize = $primary.ContextSize } }
    }
    return [pscustomobject]@{ Kind = 'none'; Task = $null; Launch = $null; WasEnabled = $false; Launches = @(); Selection = $null }
}

function Resolve-ODSPortalRuntimeSelection($Existing, $Plan) {
    # A rerun keeps the model and context the user selected (from the owned
    # plan, never from a Lemonade search) when that GGUF is still in the store.
    $saved = $Existing.Selection
    if ($saved -and $saved.GgufFile) {
        $name = [string]$saved.GgufFile
        $context = $saved.ContextSize
        $file = Join-Path (Get-ODSPortalModelsDir) $name
        $usable = $name -cmatch '^[\x20-\x7e]{1,240}$' -and $name -match '\.gguf$' -and $name -notmatch '[\\/:*?"<>|]' -and
            -not $name.StartsWith('.') -and $name -eq $name.Trim() -and
            ($context -is [int] -or $context -is [long]) -and $context -ge 4096 -and $context -le 262144 -and
            (Test-Path -LiteralPath $file -PathType Leaf)
        if ($usable) {
            if ($name -ne $Plan.GgufFile -or $context -ne $Plan.ContextSize) {
                Write-Host "         Keeping your selected model $name with $context tokens of context."
            }
            # Its vision projector stays with it while the file is still there.
            $projector = [string]$saved.MmprojFile
            $keepProjector = $projector -cmatch '^[\x20-\x7e]{1,240}$' -and $projector -match '\.gguf$' -and
                $projector -notmatch '[\\/:*?"<>|]' -and -not $projector.StartsWith('.') -and $projector -eq $projector.Trim() -and
                $projector -ine $name -and (Test-Path -LiteralPath (Join-Path (Get-ODSPortalModelsDir) $projector) -PathType Leaf)
            return [pscustomobject]@{ GgufFile = $name; ContextSize = [int]$context; Carried = $true
                MmprojFile = $(if ($keepProjector) { $projector } else { $null }) }
        }
        Write-Host "         The previously selected model $name cannot run on llama.cpp here; using the recommended $($Plan.GgufFile)."
    }
    return [pscustomobject]@{ GgufFile = [string]$Plan.GgufFile; ContextSize = [int]$Plan.ContextSize; Carried = $false; MmprojFile = $null }
}

function Backup-ODSPortalRuntimeDirectory([string]$Label) {
    # Byte-for-byte private copy of the plan and launcher files (not logs).
    $runtimeDir = Get-ODSPortalRuntimeDir
    if (-not (Test-Path -LiteralPath $runtimeDir -PathType Container)) { return '' }
    $backup = Join-Path (Get-ODSPortalStateDir) ("portal-runtime.$Label-" + (Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Path $backup | Out-Null
    try {
        foreach ($file in @(Get-ChildItem -LiteralPath $runtimeDir -File -Force)) {
            if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'The Portal runtime folder contains a link; nothing was changed.'
            }
            if ($file.Name -match '\.log(\.\d+)?$' -or $file.Name -like '.ods-private-env-*') { continue }
            Write-ODSPrivateFileBytes -Path (Join-Path $backup $file.Name) -Bytes ([IO.File]::ReadAllBytes($file.FullName))
        }
    } catch {
        # A partial backup is never left behind as if it were one.
        Remove-Item -LiteralPath $backup -Recurse -Force
        throw
    }
    return $backup
}

function Restore-ODSPortalRuntimeDirectory([string]$Backup) {
    # Previous private files return byte for byte. Files only the new runtime
    # wrote are removed; logs stay for diagnosis. Process identities are never
    # restored: they described processes that were already stopped.
    $runtimeDir = Get-ODSPortalRuntimeDir
    $restored = @{}
    if ($Backup) {
        foreach ($file in @(Get-ChildItem -LiteralPath $Backup -File -Force)) {
            if ($file.Name -in @('ready.json', 'process-ownership.json')) { continue }
            Write-ODSPrivateFileBytes -Path (Join-Path $runtimeDir $file.Name) -Bytes ([IO.File]::ReadAllBytes($file.FullName))
            $restored[$file.Name] = $true
        }
    }
    foreach ($name in $script:ODSPortalRuntimeOwnedFiles) {
        if ($restored.ContainsKey($name)) { continue }
        $path = Join-Path $runtimeDir $name
        if (Test-Path -LiteralPath $path -PathType Leaf) { Remove-Item -LiteralPath $path -Force }
    }
}

function Undo-ODSPortalRuntimeCutover($Existing, [string]$Backup, [bool]$Registered) {
    if ($Registered) {
        # Only the proven tree of the new task is stopped.
        Stop-ODSPortalRuntime
        if ($Existing.Kind -ne 'llama-server') {
            Unregister-ScheduledTask -TaskName (Get-ODSPortalRuntimeTaskName) -TaskPath '\' -Confirm:$false -ErrorAction Stop
        }
    }
    Restore-ODSPortalRuntimeDirectory $Backup
    if ($Existing.Kind -eq 'lemonade') {
        Restore-ODSPortalLemonadeLaunches $Existing.Launches
    } elseif ($Existing.Kind -eq 'llama-server') {
        if (@($Existing.Launches).Count) { Restore-ODSPortalLemonadeLaunches $Existing.Launches }
        if ($Existing.WasEnabled) {
            Set-ODSPortalRuntimeIntent 'running'
            Enable-ScheduledTask -TaskName $Existing.Launch.TaskName -TaskPath '\' -ErrorAction Stop | Out-Null
            Start-ScheduledTask -TaskName $Existing.Launch.TaskName -TaskPath '\' -ErrorAction Stop
        }
    }
}

function Get-ODSPortalRestoredRuntimeNote($Existing) {
    # What the rollback left running. A restored llama-server counts as
    # running again only once it proves its model, as a fresh start must; a
    # cause that persists (a port another program took) stops it too.
    if ($Existing.Kind -eq 'none') { return 'Nothing was left running.' }
    if ($Existing.Kind -eq 'lemonade') {
        if (@($Existing.Launches | Where-Object { $_.Launch.WasEnabled }).Count) {
            return 'The previous ODS runtime was restored and its task was started again.'
        }
        return 'The previous ODS runtime was restored; it was not running before, so it was left stopped.'
    }
    if (-not $Existing.WasEnabled) {
        return 'The previous ODS runtime was restored; it was not running before, so it was left stopped.'
    }
    try {
        $null = Wait-ODSPortalRuntimeReady ([pscustomobject]@{
            ReadyPath = (Join-Path (Get-ODSPortalRuntimeDir) 'ready.json'); Plan = $Existing.Launch.Plan })
        return 'The previous ODS runtime was restored and is running again.'
    } catch {
        return "The previous ODS runtime was restored but did not start again: $($_.Exception.Message) Fix that, then rerun setup."
    }
}

function Invoke-ODSPortalRuntimeCutover {
    <#
    .SYNOPSIS
        Replace the previous ODS runtime with the qualified llama.cpp one and
        prove it, or restore the previous ODS runtime and fail visibly.
    #>
    param($Existing, $Plan, $Options, [string]$ApiKey, [bool]$KeepPort, [string]$WslDistro = '', [string]$WslInstallDir = '',
        # Empty keeps the AMD task description.
        [string]$TaskDescription = '',
        # The CUDA route's memory-plan.json record. It is written with the new
        # plan after the backup, so a rollback restores the old pair.
        $MemoryPlan = $null)
    # One change at a time: a model switch, Unload or Resume from the
    # dashboard holds the same lock, so its stop never races this one.
    $lock = @{}
    try {
        Enter-ODSPortalRuntimeChange $lock
        # Setup read what ODS runs before it took the lock. A switch, Unload or
        # Resume that finished meanwhile is the owner's latest choice: stop
        # with nothing changed instead of undoing it.
        $current = Get-ODSPortalExistingRuntime
        if ($current.Kind -ne $Existing.Kind -or $current.WasEnabled -ne $Existing.WasEnabled -or
            ($current.Selection | ConvertTo-Json -Compress) -cne ($Existing.Selection | ConvertTo-Json -Compress)) {
            throw ('The model was changed from the dashboard while setup was running, so setup did not change the model ' +
                'runtime. Run setup again; it keeps that change.')
        }
        $label = if ($Existing.Kind -eq 'lemonade') { 'lemonade-backup' } else { 'backup' }
        $backup = if ($Existing.Kind -ne 'none') { Backup-ODSPortalRuntimeDirectory $label } else { '' }
        # Quiesce only proven ODS identities. A user's own Lemonade keeps running.
        $llamaStopped = $false
        try {
            if ($Existing.Kind -eq 'llama-server') { Stop-ODSPortalRuntime; $llamaStopped = $true }
            if (@($Existing.Launches).Count) { Stop-ODSPortalLemonadeLaunches $Existing.Launches }
        } catch {
            $failure = $_
            if ($llamaStopped) {
                # A former Lemonade task failed to stop after llama.cpp stopped:
                # put llama.cpp back as it was, as a failed start does. That
                # task's own stop already left it as its outcome requires.
                try {
                    Restore-ODSPortalRuntimeDirectory $backup
                    if ($Existing.WasEnabled) {
                        Set-ODSPortalRuntimeIntent 'running'
                        Enable-ScheduledTask -TaskName $Existing.Launch.TaskName -TaskPath '\' -ErrorAction Stop | Out-Null
                        Start-ScheduledTask -TaskName $Existing.Launch.TaskName -TaskPath '\' -ErrorAction Stop
                    }
                } catch {
                    throw "Setup could not stop a former ODS Lemonade task ($($failure.Exception.Message)), and restoring the previous llama.cpp runtime also failed: $($_.Exception.Message) Rerun setup; ODS changed only its own files under $(Get-ODSPortalStateDir), and its backup is kept there."
                }
                # A backup that cannot be removed must not hide the cause.
                if ($backup) { Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction SilentlyContinue }
                throw "$($failure.Exception.Message) $(Get-ODSPortalRestoredRuntimeNote $Existing)"
            }
            # No new runtime file was written, so the backup is redundant.
            # Failing to remove a private copy is safe to ignore; it must not
            # hide the refusal the owner needs to see.
            if ($backup) { Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction SilentlyContinue }
            throw $failure
        }
        $registered = $false
        try {
            if ($KeepPort) { Assert-ODSPortalPortAvailable $Plan.Port }
            $registration = New-ODSPortalRuntimeAction $Plan $Options $ApiKey $WslDistro $WslInstallDir $MemoryPlan
            $taskName = Register-ODSPortalRuntimeTask $registration $TaskDescription
            $registered = $true
            Start-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction Stop
            $null = Wait-ODSPortalRuntimeReady $registration
        } catch {
            $failure = $_.Exception.Message
            try {
                Undo-ODSPortalRuntimeCutover $Existing $backup $registered
            } catch {
                throw "The llama.cpp runtime did not start ($failure), and restoring the previous runtime also failed: $($_.Exception.Message) Rerun setup; ODS changed only its own files under $(Get-ODSPortalStateDir), and its backup is kept there."
            }
            # The previous files are back in place; the backup is now redundant,
            # and one that cannot be removed must not hide the cause.
            if ($backup) { Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction SilentlyContinue }
            throw "The llama.cpp runtime did not start: $failure $(Get-ODSPortalRestoredRuntimeNote $Existing)"
        }
        if (@($Existing.Launches).Count) { Complete-ODSPortalLemonadeRetirement $Existing.Launches }
        if ($backup -and $Existing.Kind -eq 'llama-server') { Remove-Item -LiteralPath $backup -Recurse -Force }
        return $registration
    } finally {
        Exit-ODSPortalRuntimeChange $lock
    }
}

function Get-ODSPortalAmdMemoryShare($Plan, $Device, $Selection, [string]$ModelsDir = (Get-ODSPortalModelsDir)) {
    <#
    .SYNOPSIS
        How much of Windows' own memory (its commit) this AMD model takes, for
        the WSL memory budget (wsl-memory-budget.ps1).
    .DESCRIPTION
        A discrete GPU keeps the model in its own memory: SharesSystemMemory
        is $false. On a unified-memory APU (Strix Halo) the firmware's
        carve-out (the BIOS UMA frame buffer, Vulkan's device-local memory)
        is not Windows memory; what does not fit it, llama.cpp keeps in
        system memory that Windows also lends WSL. That part counts: the
        model's files plus 2 GiB for its context and buffers, less the
        carve-out. (On an RTX Spark every GPU allocation counts instead.)
    #>
    if ([string]$Plan.MemoryType -cne 'unified') {
        return [pscustomobject]@{ SharesSystemMemory = $false; EngineCommitGiB = 0.0; Engine = '' }
    }
    $files = @(Get-ODSGgufSplitSiblings ([string]$Selection.GgufFile))
    if ($Selection.MmprojFile) { $files += [string]$Selection.MmprojFile }
    $bytes = [double]0
    foreach ($name in $files) {
        $path = Join-Path $ModelsDir $name
        if (Test-Path -LiteralPath $path -PathType Leaf) { $bytes += (Get-Item -LiteralPath $path -Force).Length }
    }
    $deviceGiB = if ($Device -and [double]$Device.TotalMiB -gt 0) { [double]$Device.TotalMiB / 1024 } else { 0.0 }
    $commit = [math]::Max(0.0, $bytes / 1GB + 2 - $deviceGiB)
    return [pscustomobject]@{ SharesSystemMemory = $true; EngineCommitGiB = [math]::Round($commit, 2)
        Engine = "llama.cpp (Vulkan) with $($Selection.GgufFile)" }
}

function Write-ODSPortalFitWarning($Device, [string]$ModelPath) {
    if (-not (Test-Path -LiteralPath $ModelPath -PathType Leaf) -or $Device.TotalMiB -le 0) { return }
    $modelMiB = [long][math]::Ceiling((Get-Item -LiteralPath $ModelPath).Length / 1MB)
    if ($modelMiB -gt $Device.TotalMiB) {
        Write-Host "         The model needs about $modelMiB MiB but Vulkan reports $($Device.TotalMiB) MiB on $($Device.Description). llama.cpp keeps the rest in system memory, which is slower; a larger UMA frame buffer in the BIOS or a smaller model avoids that."
    }
}

function Invoke-ODSPortalCudaQualification {
    <#
    .SYNOPSIS
        Qualify an installed CUDA llama.cpp runtime on this NVIDIA GPU before
        any large download: the pinned build, its CUDA device, then the GPU
        check with a small model already in the store. Nothing routes here yet.
    .OUTPUTS
        Passed, Class ('' when passed; device when llama.cpp lists no CUDA GPU;
        otherwise the GPU check's class), Reason, Qualification and Check.
        Throws, with Data['class'], when llama-server.exe cannot run at all.
    #>
    param(
        [Parameter(Mandatory = $true)]$Pin,
        [Parameter(Mandatory = $true)]$Runtime,
        [Parameter(Mandatory = $true)][string]$AdapterName,
        [Parameter(Mandatory = $true)][string]$ComputeCap,
        [Parameter(Mandatory = $true)][string]$TestModelPath,
        $CodeIntegrity = $null
    )
    if ([string]$Pin.Backend -cne 'cuda') { throw "The GPU check is for CUDA llama.cpp builds, not '$($Pin.Flavor)'." }
    if ($null -eq $CodeIntegrity) { $CodeIntegrity = Get-ODSNativeLlamaCodeIntegrityState }
    $qualification = Test-ODSNativeLlamaQualification -ExecutablePath $Runtime.ExecutablePath -ExpectedBuild $Pin.Build `
        -AdapterName $AdapterName -CodeIntegrity $CodeIntegrity -Backend cuda -Architecture $Pin.Architecture
    if (-not $qualification.Device) {
        $seen = @($qualification.Devices | ForEach-Object { "$($_.Name) $($_.Description)" }) -join '; '
        if (-not $seen) { $seen = 'no devices' }
        return [pscustomobject]@{ Passed = $false; Class = 'device'; Qualification = $qualification; Check = $null
            Reason = ("llama.cpp found no NVIDIA CUDA GPU (it lists: $seen). The NVIDIA NPU is not used by ODS. " +
                'Update the NVIDIA driver with the NVIDIA App or from nvidia.com, restart Windows and rerun setup.') }
    }
    # The check's log sits beside the runtime it qualifies (an unpinned log
    # never invalidates a runtime) and goes with it when it is removed.
    $reasoning = @(Get-ODSNativeReasoningArgs -Executable $Runtime.ExecutablePath -Mode 'off' -FallbackFormat 'none')
    $check = Invoke-ODSNativeLlamaGenerationProbe -ExecutablePath $Runtime.ExecutablePath -ModelPath $TestModelPath `
        -Device $qualification.Device.Name -Port (Select-ODSPortalCheckPort) -LogPath (Join-Path $Runtime.Directory 'gpu-check.log') `
        -ComputeCap $ComputeCap -ArchsByCompute $Pin.RequiredCudaArchs -ReasoningArguments $reasoning `
        -Architecture $Pin.Architecture -CodeIntegrity $CodeIntegrity
    return [pscustomobject]@{ Passed = $check.Passed; Class = $check.Class; Reason = $check.Reason
        Qualification = $qualification; Check = $check }
}

function Initialize-ODSPortalAmdRuntime($Plan, [string]$SourceRoot, [bool]$NonInteractive, [string]$WslDistro = '', [string]$WslInstallDir = '') {
    <#
    .SYNOPSIS
        Stage, qualify and cut over to llama.cpp, then return the Linux
        installer arguments and the environment that carries the API key, or
        $null when the model stays on the CPU route.
    #>
    # Validate what ODS already runs before anything changes.
    $existing = Get-ODSPortalExistingRuntime
    $pin = Get-ODSNativeLlamaPin -SourceRoot $SourceRoot
    $runtime = $null
    $installDir = Get-ODSNativeLlamaInstallDirectory $pin
    if (Test-Path -LiteralPath $installDir) {
        try { $runtime = Test-ODSNativeLlamaInstall -Directory $installDir -ExpectedZipSha256 $pin.Sha256 -ExpectedReleaseTag $pin.ReleaseTag } catch { $runtime = $null }
    }
    if (-not $runtime) {
        $mib = [math]::Ceiling($pin.Size / 1MB)
        if ($existing.Kind -eq 'none') {
            if (-not (Confirm-ODSPortalPreparation "Download llama.cpp $($pin.ReleaseTag) (Vulkan) from github.com/ggml-org (about $mib MiB, SHA-256 pinned) to run the AI model on your AMD GPU? It runs only on this computer (127.0.0.1)." $NonInteractive)) {
                Write-Host '         Continuing without the GPU: the model will run on the CPU (slower).'
                return $null
            }
        } else {
            $previous = if ($existing.Kind -eq 'lemonade') { 'Lemonade Server' } else { 'its previous llama.cpp build' }
            Write-Host "         ODS now runs the model with llama.cpp $($pin.ReleaseTag) (Vulkan) from github.com/ggml-org instead of $previous. Downloading it (about $mib MiB, SHA-256 pinned)."
        }
        # Staging happens while the previous runtime keeps serving.
        $runtime = Install-ODSNativeLlamaRuntime -Pin $pin
    }
    $qualification = Test-ODSNativeLlamaQualification -ExecutablePath $runtime.ExecutablePath -ExpectedBuild $pin.Build -AdapterName $Plan.GpuName
    if ($qualification.PolicyHint) { Write-Host "         $($qualification.PolicyHint)" }
    if (-not $qualification.Device) {
        $seen = @($qualification.Devices | ForEach-Object { "$($_.Name) $($_.Description)" }) -join '; '
        $found = if ($seen) { " (it lists: $seen)" } else { '' }
        $message = "llama.cpp found no usable Vulkan device for $($Plan.GpuName)$found. Update the AMD Adrenalin driver from amd.com/support and restart Windows."
        if ($existing.Kind -ne 'none') { throw "$message Your current ODS runtime was not changed; rerun setup after updating the driver." }
        Write-Host "         $message Continuing on the CPU route (slower); rerun setup afterwards to use the GPU."
        return $null
    }
    Write-Host "         llama.cpp $($pin.ReleaseTag) qualified on $($qualification.Device.Name): $($qualification.Device.Description) ($($qualification.Device.TotalMiB) MiB)."
    $selection = Resolve-ODSPortalRuntimeSelection $existing $Plan
    if (-not $selection.Carried) { $null = Get-ODSPortalModel $Plan }
    Write-ODSPortalFitWarning $qualification.Device (Join-Path (Get-ODSPortalModelsDir) $selection.GgufFile)
    $keepPort = $false
    if ($env:AMD_INFERENCE_PORT -and $existing.Kind -ne 'none') {
        $port = [int]$env:AMD_INFERENCE_PORT
        if ($port -lt 1 -or $port -gt 65535) { throw 'AMD_INFERENCE_PORT must be between 1 and 65535.' }
        $keepPort = $true
    } elseif ($existing.Kind -ne 'none' -and $existing.Selection.Port -ge 1 -and $existing.Selection.Port -le 65535) {
        # The WSL side already reaches this port; it is checked once freed.
        $port = [int]$existing.Selection.Port
        $keepPort = $true
    } else {
        $port = Select-ODSPortalRuntimePort
    }
    $apiKey = Get-ODSPortalApiKey
    $options = New-ODSPortalRuntimeOptions $runtime $qualification
    $runtimePlan = [ordered]@{ ExecutablePath = $runtime.ExecutablePath; Port = $port; ModelsDir = (Get-ODSPortalModelsDir)
        ContextSize = $selection.ContextSize; GgufFile = $selection.GgufFile }
    if ($selection.MmprojFile) { $runtimePlan.MmprojFile = $selection.MmprojFile }
    Write-Host "         Starting llama.cpp on 127.0.0.1:$port with $($selection.GgufFile)..."
    $null = Invoke-ODSPortalRuntimeCutover -Existing $existing -Plan $runtimePlan -Options $options -ApiKey $apiKey `
        -KeepPort $keepPort -WslDistro $WslDistro -WslInstallDir $WslInstallDir
    $previousDirectory = if ($existing.Kind -eq 'llama-server') { Split-Path -Parent $existing.Launch.ExecutablePath } else { '' }
    Remove-ODSNativeLlamaOldRuntimes -KeepDirectory $runtime.Directory -PreviousDirectory $previousDirectory
    Write-Host "         GPU model ready: $($selection.GgufFile) ($($selection.ContextSize) tokens of context)."
    return [pscustomobject]@{
        # Whether the model shares Windows' memory with WSL (setup sizes WSL's limit).
        Memory = (Get-ODSPortalAmdMemoryShare $Plan $qualification.Device $selection)
        Arguments = @(
            '--native-llm-url', "http://localhost:$port",
            '--native-llm-host-transport', 'model-router',
            '--native-llm-model', $selection.GgufFile,
            '--native-llm-context-size', [string]$selection.ContextSize,
            '--native-llm-gpu-name', $Plan.GpuName,
            '--native-llm-gpu-vram-mb', [string]$Plan.VramMB,
            '--native-llm-api-key-env', $script:ODSPortalApiKeyVariable
        )
        Environment = @{ $script:ODSPortalApiKeyVariable = $apiKey }
    }
}
