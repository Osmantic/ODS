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
. (Join-Path $PSScriptRoot 'managed-lemonade.ps1')
. (Join-Path $PSScriptRoot 'env-generator.ps1')

$script:ODSPortalLemonadeLegacyTaskName = 'ODSLemonadeRuntime'
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

function Install-ODSPortalLemonade([string]$SourceRoot, [bool]$NonInteractive) {
    # Returns the Lemonade executable path, or $null when the user declines.
    $runtime = Get-ODSAmdLemonadeRuntime -RootPath $SourceRoot
    $managed = $runtime.windows_managed
    if (-not $managed) { throw 'AMD contract has no pinned managed Windows Lemonade release.' }
    Assert-ODSManagedLemonadeRoot
    $releaseDir = Get-ODSManagedLemonadePath $managed
    if (Test-Path -LiteralPath $releaseDir) {
        $exe = Assert-ODSManagedLemonadeRelease $managed $releaseDir
        Assert-ODSPortalLemonadeVersion (Get-ODSLemonadeExecutableVersion $exe)
        Write-Host "         ODS-managed Lemonade Server found: $exe"
        return $exe
    }
    if (-not (Confirm-ODSPortalPreparation "Install ODS-managed Lemonade Server $($managed.windows_version) to run the AI model on your AMD GPU? It runs only on this computer (127.0.0.1)." $NonInteractive)) {
        return $null
    }
    $exe = Install-ODSManagedLemonade -Runtime $managed
    Assert-ODSPortalLemonadeVersion (Get-ODSLemonadeExecutableVersion $exe)
    return $exe
}

function Assert-ODSPortalLemonadeVersion([version]$Version) {
    if ((($Version -lt [version]'10.0.0') -or ($Version -ge [version]'11.0.0')) -and
        $Version.ToString(3) -cne '2026.40.0') {
        throw "Lemonade $Version is outside the supported Portal runtime contract (10.x or pinned 2026.40.0). The existing runtime was not changed."
    }
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

function Get-ODSPortalLemonadeTaskName {
    return ('ODSLemonadeRuntime-' + (Get-ODSPortalUserSid))
}

function Get-ODSPortalLemonadeTask {
    $currentName = Get-ODSPortalLemonadeTaskName
    foreach ($name in @($currentName, $script:ODSPortalLemonadeLegacyTaskName)) {
        $lookupErrors = @()
        $tasks = @(Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction SilentlyContinue -ErrorVariable lookupErrors)
        $unexpected = @($lookupErrors | Where-Object { $_.CategoryInfo.Category -ne 'ObjectNotFound' })
        if ($unexpected.Count) {
            if ($name -eq $script:ODSPortalLemonadeLegacyTaskName -and
                -not @($unexpected | Where-Object { $_.CategoryInfo.Category -notin @('PermissionDenied', 'SecurityError') }).Count) {
                continue # An unreadable legacy task is not ours to adopt.
            }
            throw 'Cannot inspect the Portal Lemonade task; no process was changed.'
        }
        if (-not $tasks.Count) { continue }
        if ($tasks.Count -ne 1 -or $tasks[0].TaskPath -ne '\' -or
            [string]::IsNullOrWhiteSpace([string]$tasks[0].Principal.UserId)) {
            throw 'The Portal Lemonade task identity is ambiguous.'
        }
        if ((Get-ODSPortalUserSid $tasks[0].Principal.UserId) -ne (Get-ODSPortalUserSid)) {
            if ($name -eq $currentName) { throw 'The Portal Lemonade task belongs to a different Windows user.' }
            continue # Another user's legacy task is never adopted or changed.
        }
        return $tasks[0]
    }
    return $null
}

function Get-ODSPortalTaskEngineId([string]$TaskName = (Get-ODSPortalLemonadeTaskName)) {
    $scheduler = New-Object -ComObject Schedule.Service
    $scheduler.Connect()
    $instances = @($scheduler.GetFolder('\').GetTask($TaskName).GetInstances(0))
    if ($instances.Count -gt 1) { throw 'More than one Portal Lemonade task instance is running; ownership is ambiguous.' }
    if ($instances.Count -eq 1) { return [int]$instances[0].EnginePID }
    return 0
}

function Get-ODSPortalOwnedProcessTree([object[]]$Roots, [object[]]$Nodes) {
    $tree = [Collections.Generic.List[object]]::new()
    foreach ($root in $Roots) { $tree.Add($root) }
    for ($index = 0; $index -lt $tree.Count; $index++) {
        $parent = $tree[$index]
        if ($parent.CreationDate -isnot [datetime] -or -not $parent.ExecutablePath) {
            throw "Cannot read the owned Lemonade process identity (PID $($parent.ProcessId)); no process was stopped."
        }
        foreach ($node in @($Nodes | Where-Object { $_.ParentProcessId -eq $parent.ProcessId })) {
            if ($node.CreationDate -isnot [datetime] -or $node.CreationDate.ToUniversalTime() -lt $parent.CreationDate.ToUniversalTime()) {
                throw 'The Lemonade process ancestry is stale or ambiguous; no process was stopped.'
            }
            # A retained process handle supplies the original root's exit time.
            # Children born after that exit belong to a reused PID, not to us.
            if ($parent.ExitedAt -and $node.CreationDate.ToUniversalTime() -gt $parent.ExitedAt.ToUniversalTime()) { continue }
            $known = @($tree | Where-Object { $_.ProcessId -eq $node.ProcessId })
            if ($known.Count) {
                if ($known.Count -ne 1 -or $known[0].CreationDate.ToUniversalTime() -ne $node.CreationDate.ToUniversalTime() -or
                    $known[0].ExecutablePath -ine $node.ExecutablePath -or
                    $node.ProcessId -in @($tree | Select-Object -First ($index + 1) | ForEach-Object { $_.ProcessId })) {
                    throw 'The Lemonade process ancestry is stale or ambiguous; no process was stopped.'
                }
                continue
            }
            $tree.Add($node)
        }
    }
    $handles = [Collections.Generic.List[object]]::new()
    try {
        foreach ($node in $tree) {
            if ($node.ExitedAt) { continue }
            $process = Get-Process -Id $node.ProcessId -ErrorAction Stop
            $handles.Add($process)
            $null = $process.Handle
            if ($process.Path -ine $node.ExecutablePath -or
                [math]::Abs(($process.StartTime.ToUniversalTime() - $node.CreationDate.ToUniversalTime()).TotalMilliseconds) -ge 1) {
                throw 'A Lemonade process changed during ownership verification; no process was stopped.'
            }
        }
        return [pscustomobject]@{ Nodes = $tree; Handles = $handles }
    } catch {
        foreach ($process in $handles) { $process.Dispose() }
        throw
    }
}

function Stop-ODSPortalOwnedProcesses($Handles) {
    # Parents first prevent the router from launching replacement children.
    foreach ($process in $Handles) {
        if (-not $process.HasExited) {
            try { $process.Kill() } catch {
                $cause = $_.Exception
                while ($cause -is [System.Management.Automation.RuntimeException] -and $cause.InnerException) {
                    $cause = $cause.InnerException
                }
                if ($cause -isnot [InvalidOperationException] -and
                    $cause -isnot [System.ComponentModel.Win32Exception]) { throw }
                # Task Scheduler may have exited this held process after HasExited
                # but before Kill. Accept that race only if the same handle proves exit.
                if (-not $process.WaitForExit(1000)) {
                    throw "Could not stop owned Lemonade process $($process.Id): $($cause.Message)"
                }
            }
        }
    }
    foreach ($process in $Handles) {
        if (-not $process.WaitForExit(5000)) { throw 'An owned Lemonade process did not exit within five seconds.' }
    }
}

function Write-ODSPortalProcessOwnership([string]$Path, $Plan, $Nodes) {
    $records = @($Nodes | ForEach-Object {
        @{ ProcessId = $_.ProcessId; ExecutablePath = $_.ExecutablePath
            StartedAt = $_.CreationDate.ToUniversalTime().ToString('o')
            ExitedAt = if ($_.ExitedAt) { $_.ExitedAt.ToUniversalTime().ToString('o') } else { $null } }
    })
    $ownership = @{ ExecutablePath = $Plan.ExecutablePath; Port = $Plan.Port; Processes = $records }
    Write-ODSPrivateEnvFile -Path $Path -Content ($ownership | ConvertTo-Json -Depth 4 -Compress)
}

function Stop-ODSPortalLemonade([string]$ExecutablePath) {
    # Capture the task's actual process tree before Task Scheduler removes its
    # root. Matching an installation directory does not prove process ownership.
    $tasks = @(Get-ODSPortalLemonadeTask | Where-Object { $null -ne $_ })
    if ($tasks.Count -eq 0) { return } # First install preserves user-started servers.
    if ($tasks.Count -ne 1 -or $tasks[0].TaskPath -ne '\' -or
        [string]::IsNullOrWhiteSpace([string]$tasks[0].Principal.UserId) -or
        (Get-ODSPortalUserSid $tasks[0].Principal.UserId) -ne (Get-ODSPortalUserSid)) {
        throw 'The existing Portal Lemonade task is not uniquely owned by the current Windows user.'
    }
    $task = $tasks[0]
    $taskName = if ($task.TaskName) { $task.TaskName } else { Get-ODSPortalLemonadeTaskName }
    $actions = @($task.Actions)
    if ($actions.Count -ne 1) { throw 'The Portal Lemonade task has an unrecognized action; no process was stopped.' }
    $action = $actions[0]
    $runtimeDir = Join-Path (Get-ODSPortalStateDir) 'portal-runtime'
    $readyPath = Join-Path $runtimeDir 'ready.json'
    $ownershipPath = Join-Path $runtimeDir 'process-ownership.json'
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
            $plan = Get-Content -LiteralPath (Join-Path $runtimeDir 'runtime.json') -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json
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

    # Every stop path, including uninstall, must disarm scheduled retries.
    # Publish first so even an already queued durable launcher exits cleanly.
    Set-ODSPortalLemonadeIntent 'stopped'
    Disable-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction Stop | Out-Null

    $engineId = Get-ODSPortalTaskEngineId $taskName
    $nodes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $root = $null
    $recovered = @()
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
                $root.CreationDate.ToUniversalTime() -lt $engines[0].CreationDate.ToUniversalTime()) {
                throw 'The Portal task engine ancestry cannot be proved; no process was stopped.'
            }
        }
    } elseif ($durable -and (Test-Path -LiteralPath $ownershipPath -PathType Leaf)) {
        # Startup failure is not readiness. Its private ownership record remains
        # usable after a partial cleanup or after the task wrapper has exited.
        $ownership = Get-Content -LiteralPath $ownershipPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($ownership.ExecutablePath -ine $ExecutablePath -or $ownership.Port -ne $port -or
            -not @($ownership.Processes).Count) { throw 'The saved Lemonade ownership does not match its launch plan.' }
        foreach ($saved in $ownership.Processes) {
            $started = [datetime]$saved.StartedAt
            if ([int]$saved.ProcessId -lt 1 -or -not [IO.Path]::IsPathRooted([string]$saved.ExecutablePath)) {
                throw 'The saved Lemonade process ownership is invalid.'
            }
            if ($saved.ExitedAt) {
                $exited = [datetime]$saved.ExitedAt
                if ($exited.ToUniversalTime() -lt $started.ToUniversalTime()) { throw 'The saved Lemonade process lifetime is invalid.' }
                $recovered += [pscustomobject]@{ ProcessId = $saved.ProcessId; CreationDate = $started
                    ExecutablePath = $saved.ExecutablePath; ExitedAt = $exited }
                continue
            }
            $matchesById = @($nodes | Where-Object {
                $_.ProcessId -eq $saved.ProcessId -and $_.ExecutablePath -ieq $saved.ExecutablePath -and
                $_.CreationDate -is [datetime] -and
                [math]::Abs(($_.CreationDate.ToUniversalTime() - $started.ToUniversalTime()).TotalMilliseconds) -lt 1
            })
            if ($matchesById.Count -gt 1) { throw 'The saved Lemonade process ownership is ambiguous.' }
            # A missing or recycled PID is never a reason to stop its new owner.
            $recovered += $matchesById
        }
    } elseif ($durable -and (Test-Path -LiteralPath $readyPath -PathType Leaf)) {
        $ready = Get-Content -LiteralPath $readyPath -Raw -Encoding UTF8 | ConvertFrom-Json
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
    if (-not $root -and -not $recovered.Count) {
        if ($task.State -notin @('Ready', 'Disabled') -or @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).Count) {
            throw "Cannot prove ownership of Lemonade on port $port. No process was stopped; close the previous ODS runtime explicitly before retrying."
        }
        return
    }

    # Hold process handles before stopping the task, so a recycled PID cannot
    # redirect a later Kill(). Children may live outside Lemonade's bin folder.
    $roots = if ($root) { @($root) } else { $recovered }
    $owned = Get-ODSPortalOwnedProcessTree $roots $nodes
    try {
        $currentTasks = @(Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction Stop)
        if ($currentTasks.Count -ne 1 -or $currentTasks[0].TaskPath -ne $task.TaskPath -or
            $currentTasks[0].Principal.UserId -ne $task.Principal.UserId -or @($currentTasks[0].Actions).Count -ne 1 -or
            $currentTasks[0].Actions[0].Execute -ne $action.Execute -or $currentTasks[0].Actions[0].Arguments -cne $action.Arguments -or
            $currentTasks[0].Actions[0].WorkingDirectory -ne $action.WorkingDirectory -or (Get-ODSPortalTaskEngineId $taskName) -ne $engineId) {
            throw 'The Portal Lemonade task changed during ownership verification; no process was stopped.'
        }
        if ($engineId -gt 0) { Stop-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction Stop }
        Stop-ODSPortalOwnedProcesses $owned.Handles
        if ($durable -and (Test-Path -LiteralPath $ownershipPath)) { Remove-Item -LiteralPath $ownershipPath -Force }
    } finally {
        foreach ($process in $owned.Handles) { $process.Dispose() }
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
        if ($port -lt 1 -or $port -gt 65535) { throw 'AMD_INFERENCE_PORT must be between 1 and 65535.' }
        $owner = Get-ODSPortalPortOwner $port
        if ($owner) { throw "AMD_INFERENCE_PORT $port is already used by '$owner'. Choose a free port or remove AMD_INFERENCE_PORT, then rerun." }
        if (-not (Test-ODSPortalPortBindable $port)) { throw "AMD_INFERENCE_PORT $port cannot bind Windows loopback (it may be reserved). Choose a different port." }
        return $port
    }
    $taken = @()
    foreach ($port in $script:ODSPortalLemonadePortCandidates) {
        $owner = Get-ODSPortalPortOwner $port
        if (-not $owner -and (Test-ODSPortalPortBindable $port)) { return $port }
        $reason = if ($owner) { $owner } else { 'reserved or unavailable for binding' }
        $taken += "$port ($reason)"
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

function Test-ODSPortalPortBindable([int]$Port) {
    # Excluded Hyper-V ports have no listener, yet bind fails with AccessDenied.
    # Probe the exact address Lemonade uses; the real launch remains authoritative.
    $probe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $Port)
    $probe.ExclusiveAddressUse = $true
    try { $probe.Start(); return $true }
    catch [Net.Sockets.SocketException] {
        if ($_.Exception.SocketErrorCode -notin @('AccessDenied', 'AddressAlreadyInUse')) { throw }
        return $false
    } finally { $probe.Stop() }
}

function Set-ODSPortalLemonadeIntent([ValidateSet('running', 'stopped')][string]$State) {
    $path = Join-Path (Join-Path (Get-ODSPortalStateDir) 'portal-runtime') 'intent.json'
    Write-ODSPrivateEnvFile -Path $path -Content (@{ State = $State } | ConvertTo-Json -Compress)
}

function Test-ODSPortalLemonadeWanted([string]$Path) {
    # Legacy plans predate intent. New explicit stops always publish it first.
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $true }
    $intent = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    if ($intent.State -cnotin @('running', 'stopped')) { throw 'The saved Lemonade run intent is invalid.' }
    return $intent.State -ceq 'running'
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
    Assert-ODSPortalLemonadeVersion $contract.Version
    if ($contract.BindAddress -ne '127.0.0.1') {
        throw 'The Portal runtime launcher requires Lemonade on loopback.'
    }
    if (Test-Path -LiteralPath $ReadyPath) { Remove-Item -LiteralPath $ReadyPath -Force }
    if (@(Get-NetTCPConnection -LocalPort $Plan.Port -State Listen -ErrorAction SilentlyContinue).Count) {
        throw "Lemonade port $($Plan.Port) is already occupied; no existing process was changed."
    }
    $child = $null
    $identity = $null
    $ownershipPath = Join-Path (Split-Path -Parent $ReadyPath) 'process-ownership.json'
    try {
        $child = Start-Process -FilePath $contract.ExecutablePath -ArgumentList $contract.ArgumentString `
            -WorkingDirectory (Split-Path -Parent $contract.ExecutablePath) -WindowStyle Hidden -PassThru
        $null = $child.Handle
        $identity = [pscustomobject]@{ ProcessId = $child.Id; CreationDate = $child.StartTime
            ExecutablePath = $contract.ExecutablePath; ExitedAt = $null }
        Write-ODSPortalProcessOwnership $ownershipPath $Plan @($identity)
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
        if ($child.ExitCode -ne 0) { throw "The Portal Lemonade process exited with code $($child.ExitCode)." }
        return $child.ExitCode
    } catch {
        $startupFailure = $_.Exception.Message
        if ($identity) {
            if ($child.HasExited) { $identity.ExitedAt = $child.ExitTime }
            try {
                try {
                    if (Test-Path -LiteralPath $ReadyPath) { Remove-Item -LiteralPath $ReadyPath -Force }
                    Write-ODSPortalProcessOwnership $ownershipPath $Plan @($identity)
                } finally {
                    # Cleanup must still run when disk space or ACLs prevent
                    # writing the failure record or removing stale readiness.
                    try {
                        $owned = Get-ODSPortalOwnedProcessTree @($identity) @(Get-CimInstance Win32_Process -ErrorAction Stop)
                    } catch {
                        # This retained handle cannot point at a recycled PID.
                        # An unproved descendant is deliberately left untouched.
                        if (-not $child.HasExited) {
                            $child.Kill()
                            if (-not $child.WaitForExit(5000)) { throw 'The launched Lemonade process did not exit within five seconds.' }
                        }
                        $identity.ExitedAt = $child.ExitTime
                        Write-ODSPortalProcessOwnership $ownershipPath $Plan @($identity)
                        throw
                    }
                    try {
                        # Preserve every child identity before stopping its
                        # parent, so interrupted cleanup can resume on rerun.
                        try { Write-ODSPortalProcessOwnership $ownershipPath $Plan $owned.Nodes }
                        finally { Stop-ODSPortalOwnedProcesses $owned.Handles }
                        Remove-Item -LiteralPath $ownershipPath -Force
                    } finally {
                        foreach ($process in $owned.Handles) { $process.Dispose() }
                    }
                }
            } catch {
                throw "Lemonade startup failed: $startupFailure Cleanup could not be completed or recorded: $($_.Exception.Message)"
            }
        }
        throw
    } finally {
        if ($child) { $child.Dispose() }
    }
}

function Assert-ODSPortalWslBinding([string]$WslDistro, [string]$WslInstallDir) {
    if ($WslDistro -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$' -or $WslDistro -match '^docker-desktop' -or
        $WslInstallDir -notmatch '^/[^\x00-\x1f\x7f]+$' -or $WslInstallDir -match '(^|/)\.\.?(/|$)' -or
        $WslInstallDir.Contains('//') -or $WslInstallDir.EndsWith('/')) {
        throw 'An explicit WSL distribution and normalized absolute Linux installation directory are required.'
    }
}

function New-ODSPortalLemonadeRuntimeAction($Contract, [string]$GgufFile, [string]$WslDistro = '', [string]$WslInstallDir = '') {
    # All dependencies live beside the private plan, never in a temporary
    # checkout. Existing private-file writer verifies owner-only Windows ACLs.
    if ($WslDistro -or $WslInstallDir) { Assert-ODSPortalWslBinding $WslDistro $WslInstallDir }
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
    if ($WslDistro -or $WslInstallDir) {
        $plan.WslDistro = $WslDistro
        $plan.WslInstallDir = $WslInstallDir
    }
    Write-ODSPrivateEnvFile -Path (Join-Path $runtimeDir 'runtime.json') -Content ($plan | ConvertTo-Json -Compress)
    Set-ODSPortalLemonadeIntent 'running'
    $readyPath = Join-Path $runtimeDir 'ready.json'
    if (Test-Path -LiteralPath $readyPath) { Remove-Item -LiteralPath $readyPath -Force }
    $definitions = foreach ($name in @('Test-ODSPortalLemonadeHealth', 'Wait-ODSPortalLemonadeHealth',
            'Get-ODSPortalOwnedProcessTree', 'Stop-ODSPortalOwnedProcesses', 'Write-ODSPortalProcessOwnership',
            'Assert-ODSPortalLemonadeListener', 'Assert-ODSPortalLemonadeVersion', 'Test-ODSPortalLemonadeWanted', 'Invoke-ODSPortalLemonadeRuntime')) {
        "function $name {`n$((Get-Command $name -CommandType Function).Definition)`n}"
    }
    $launcher = @'
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'backend-contract.ps1')
. (Join-Path $PSScriptRoot 'env-generator.ps1')
__PORTAL_FUNCTIONS__
try {
    if (-not (Test-ODSPortalLemonadeWanted (Join-Path $PSScriptRoot 'intent.json'))) { exit 0 }
    $plan = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime.json') -Raw -Encoding UTF8 | ConvertFrom-Json
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
            $ready = Get-Content -LiteralPath $Registration.ReadyPath -Raw -Encoding UTF8 | ConvertFrom-Json
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

function Get-ODSPortalLemonadeUpgradeJournalPath {
    return (Join-Path (Get-ODSPortalStateDir) 'portal-runtime-upgrade.json')
}

function Get-ODSPortalTaskXmlWithoutEnabled([string]$Xml) {
    $document = [xml]$Xml
    foreach ($node in @($document.SelectNodes('/*[local-name()="Task"]/*[local-name()="Settings"]/*[local-name()="Enabled"]'))) {
        $null = $node.ParentNode.RemoveChild($node)
    }
    return $document.OuterXml
}

function Get-ODSPortalTaskEnabled($Task, [string]$Xml) {
    if ($null -ne $Task.Settings -and $null -ne $Task.Settings.Enabled) {
        return [bool]$Task.Settings.Enabled
    }
    $document = [xml]$Xml
    $node = $document.SelectSingleNode('/*[local-name()="Task"]/*[local-name()="Settings"]/*[local-name()="Enabled"]')
    if ($node) { return $node.InnerText -ieq 'true' }
    return $true # Task Scheduler's omitted Enabled field defaults to true.
}

function Get-ODSPortalDurableTaskExecutable($Task) {
    # Only the current user's known durable launcher can be migrated. The
    # former direct and generated wrappers remain stoppable for uninstall, but
    # cannot be upgraded without a recoverable saved plan.
    if (-not $Task) { return $null }
    $runtimeDir = Join-Path (Get-ODSPortalStateDir) 'portal-runtime'
    $action = @($Task.Actions)
    $expected = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' +
        (Join-Path $runtimeDir 'launch.ps1') + '"'
    if ($action.Count -ne 1 -or $action[0].Arguments -cne $expected -or
        $action[0].WorkingDirectory -ine $runtimeDir -or
        $Task.TaskName -cne (Get-ODSPortalLemonadeTaskName)) {
        throw 'The existing Portal Lemonade task uses a legacy or ambiguous launcher; it was not changed.'
    }
    $planPath = Join-Path $runtimeDir 'runtime.json'
    $plan = Get-Content -LiteralPath $planPath -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    $exe = [string]$plan.ExecutablePath
    if (-not [IO.Path]::IsPathRooted($exe) -or -not (Test-Path -LiteralPath $exe -PathType Leaf)) {
        throw 'The existing Portal Lemonade plan has no available executable; it was not changed.'
    }
    return $exe
}

function Save-ODSPortalLemonadeUpgrade($Task, [string]$OldExecutable, [string]$NewExecutable) {
    $path = Get-ODSPortalLemonadeUpgradeJournalPath
    if (Test-Path -LiteralPath $path) { throw 'A Portal Lemonade upgrade already needs recovery.' }
    $runtimeDir = Join-Path (Get-ODSPortalStateDir) 'portal-runtime'
    $files = @()
    foreach ($name in @('runtime.json', 'launch.ps1', 'backend-contract.ps1', 'env-generator.ps1', 'intent.json')) {
        $file = Join-Path $runtimeDir $name
        $present = Test-Path -LiteralPath $file -PathType Leaf
        $files += @{ Name = $name; Present = $present
            Content = if ($present) { [string](Get-Content -LiteralPath $file -Raw -Encoding UTF8 -ErrorAction Stop) } else { $null } }
    }
    $xml = Export-ScheduledTask -TaskName $Task.TaskName -TaskPath '\' -ErrorAction Stop
    if ([string]::IsNullOrWhiteSpace([string]$xml)) { throw 'Could not snapshot the owned Lemonade task.' }
    $wasRunning = Test-ODSPortalLemonadeWanted (Join-Path $runtimeDir 'intent.json')
    $taskEnabled = Get-ODSPortalTaskEnabled $Task ([string]$xml)
    $journal = @{ Version = 1; Phase = 'prepared'; TaskName = [string]$Task.TaskName
        OldExecutable = $OldExecutable; NewExecutable = $NewExecutable
        TaskXml = [string]$xml; Files = $files; WasRunning = $wasRunning; TaskEnabled = $taskEnabled }
    Write-ODSPrivateEnvFile -Path $path -Content ($journal | ConvertTo-Json -Depth 6 -Compress)
    return $journal
}

function Restore-ODSPortalLemonadeUpgrade {
    $path = Get-ODSPortalLemonadeUpgradeJournalPath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return }
    $journal = Get-Content -LiteralPath $path -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    if ($journal.Version -ne 1 -or $journal.Phase -cnotin @('prepared', 'stopped', 'recovering') -or
        [string]::IsNullOrWhiteSpace([string]$journal.TaskXml) -or
        [string]::IsNullOrWhiteSpace([string]$journal.TaskName) -or
        $journal.TaskName -cne (Get-ODSPortalLemonadeTaskName) -or
        -not [IO.Path]::IsPathRooted([string]$journal.OldExecutable) -or
        -not [IO.Path]::IsPathRooted([string]$journal.NewExecutable) -or
        -not (Test-Path -LiteralPath ([string]$journal.OldExecutable) -PathType Leaf) -or
        -not (Test-Path -LiteralPath ([string]$journal.NewExecutable) -PathType Leaf)) {
        throw 'The Portal Lemonade upgrade journal is incomplete; no task was changed.'
    }
    $task = Get-ODSPortalLemonadeTask
    if (-not $task -or $task.TaskName -cne $journal.TaskName) {
        throw 'The Portal Lemonade upgrade task identity changed; recovery needs review.'
    }
    $currentXml = Export-ScheduledTask -TaskName $task.TaskName -TaskPath '\' -ErrorAction Stop
    $sameTask = (Get-ODSPortalTaskXmlWithoutEnabled $currentXml) -ceq
        (Get-ODSPortalTaskXmlWithoutEnabled ([string]$journal.TaskXml))
    $runtimeDir = Join-Path (Get-ODSPortalStateDir) 'portal-runtime'
    $allowedFiles = @('runtime.json', 'launch.ps1', 'backend-contract.ps1', 'env-generator.ps1', 'intent.json')
    $savedFiles = @($journal.Files)
    if ($savedFiles.Count -ne $allowedFiles.Count -or
        @($savedFiles | Select-Object -ExpandProperty Name -Unique).Count -ne $allowedFiles.Count -or
        @($savedFiles | Where-Object { [string]$_.Name -cnotin $allowedFiles }).Count) {
        throw 'The Portal Lemonade upgrade journal has an invalid file set; no task was changed.'
    }
    $savedPlan = @($savedFiles | Where-Object { $_.Name -ceq 'runtime.json' })[0]
    if (-not $savedPlan.Present) { throw 'The prior Portal Lemonade plan is missing from its journal.' }
    $currentPlanPath = Join-Path $runtimeDir 'runtime.json'
    $currentPlan = Get-Content -LiteralPath $currentPlanPath -Raw -Encoding UTF8 -ErrorAction Stop |
        ConvertFrom-Json -ErrorAction Stop
    $currentExe = [string]$currentPlan.ExecutablePath
    $oldExe = [string]$journal.OldExecutable
    $newExe = [string]$journal.NewExecutable
    if (-not [IO.Path]::IsPathRooted($currentExe) -or
        ($currentExe -ine $oldExe -and $currentExe -ine $newExe) -or
        (-not $sameTask -and $currentExe -ine $newExe)) {
        throw 'The Portal Lemonade task or plan changed outside this upgrade; recovery needs review.'
    }
    $filesUnchanged = $true
    foreach ($saved in $savedFiles) {
        $file = Join-Path $runtimeDir ([string]$saved.Name)
        $present = Test-Path -LiteralPath $file -PathType Leaf
        if ($present -ne [bool]$saved.Present -or
            ($present -and (Get-Content -LiteralPath $file -Raw -Encoding UTF8 -ErrorAction Stop) -cne
                [string]$saved.Content)) {
            $filesUnchanged = $false
            break
        }
    }
    if ($journal.Phase -ceq 'prepared' -and $sameTask -and $filesUnchanged -and
        ((Get-ODSPortalTaskEnabled $task $currentXml) -eq [bool]$journal.TaskEnabled)) {
        # Stop rejected before changing anything. A recovery attempt must not
        # disable or restart a task merely because staging wrote a journal.
        Remove-Item -LiteralPath $path -Force
        return
    }
    # A previous rollback may have restored the task and intent but failed to
    # prove model readiness. Preserve that proof obligation across retries.
    if ($journal.Phase -cne 'recovering') {
        $journal.Phase = 'recovering'
        Write-ODSPrivateEnvFile -Path $path -Content ($journal | ConvertTo-Json -Depth 6 -Compress)
    }
    if ($currentExe -ieq $newExe) {
        # The durable task action can be byte-identical while its runtime.json
        # now launches the new binary. Stop by exact plan/process ownership,
        # never by comparing task XML alone.
        Stop-ODSPortalLemonade $newExe
    } else {
        # A crash can occur after the original task was disabled but before
        # registration. Never stop it merely because a journal exists.
        Disable-ScheduledTask -TaskName $task.TaskName -TaskPath '\' -ErrorAction Stop | Out-Null
    }
    foreach ($saved in $savedFiles) {
        $file = Join-Path $runtimeDir ([string]$saved.Name)
        if ($saved.Present) {
            Write-ODSPrivateEnvFile -Path $file -Content ([string]$saved.Content)
        } elseif (Test-Path -LiteralPath $file -PathType Leaf) {
            Remove-Item -LiteralPath $file -Force
        }
    }
    foreach ($name in @('ready.json', 'process-ownership.json')) {
        $file = Join-Path $runtimeDir $name
        if (Test-Path -LiteralPath $file -PathType Leaf) { Remove-Item -LiteralPath $file -Force }
    }
    Register-ScheduledTask -TaskName $task.TaskName -TaskPath '\' -Xml ([string]$journal.TaskXml) -Force -ErrorAction Stop | Out-Null
    if ($journal.TaskEnabled -and $journal.WasRunning) {
        Enable-ScheduledTask -TaskName $task.TaskName -TaskPath '\' -ErrorAction Stop | Out-Null
        Start-ScheduledTask -TaskName $task.TaskName -TaskPath '\' -ErrorAction Stop
        # Keep the journal if recovery cannot prove the old model returned.
        $oldPlan = Get-Content -LiteralPath (Join-Path $runtimeDir 'runtime.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $oldRegistration = [pscustomobject]@{ ReadyPath = (Join-Path $runtimeDir 'ready.json'); Plan = $oldPlan }
        $null = Wait-ODSPortalLemonadeReady $oldRegistration
    } else {
        Disable-ScheduledTask -TaskName $task.TaskName -TaskPath '\' -ErrorAction Stop | Out-Null
    }
    Remove-Item -LiteralPath $path -Force
}

function Register-ODSPortalLemonadeTask($Contract, [string]$GgufFile = '', [string]$WslDistro = '', [string]$WslInstallDir = '', [bool]$PriorStopped = $false, [string]$ExpectedPreviousXml = '') {
    # One task for this Windows user: starts at sign-in (so the model survives a
    # restart) and now. It binds 127.0.0.1 only.
    $previous = Get-ODSPortalLemonadeTask
    $taskName = Get-ODSPortalLemonadeTaskName
    if ($PriorStopped -and -not $previous) {
        throw 'The owned Portal Lemonade task disappeared after stop; it was not replaced.'
    }
    if ($previous) {
        if ($PriorStopped) {
            $currentXml = Export-ScheduledTask -TaskName $previous.TaskName -TaskPath '\' -ErrorAction Stop
            if ([string]::IsNullOrWhiteSpace($ExpectedPreviousXml) -or
                (Get-ODSPortalTaskXmlWithoutEnabled $currentXml) -cne
                (Get-ODSPortalTaskXmlWithoutEnabled $ExpectedPreviousXml)) {
                throw 'The Portal Lemonade task changed after the owned runtime stopped; it was not replaced.'
            }
        }
        # Stop validates the old action, plan and held process identities before
        # replacing any file or adopting the same user's legacy task.
        if (-not $PriorStopped) { Stop-ODSPortalLemonade $Contract.ExecutablePath }
        $previousName = if ($previous.TaskName) { $previous.TaskName } else { $taskName }
    }
    $registration = New-ODSPortalLemonadeRuntimeAction $Contract $GgufFile $WslDistro $WslInstallDir
    $action = $registration.Action
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User (Get-ODSPortalUserSid)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $taskName -TaskPath '\' -Action $action -Trigger $trigger `
        -Settings $settings -Principal (New-ODSInteractiveScheduledTaskPrincipal -RunLevel Limited) `
        -Description 'ODS: Lemonade Server for the AMD GPU (127.0.0.1). Starts at sign-in.' -Force | Out-Null
    if ($previous -and $previousName -ne $taskName) {
        Unregister-ScheduledTask -TaskName $previousName -TaskPath '\' -Confirm:$false -ErrorAction Stop
    }
    Start-ScheduledTask -TaskName $taskName -TaskPath '\'
    return $registration
}

function Initialize-ODSPortalAmdLemonade($Plan, [string]$SourceRoot, [bool]$NonInteractive, [string]$WslDistro = '', [string]$WslInstallDir = '') {
    # Returns the Linux installer arguments for the Windows Lemonade route, or
    # an empty array when the user keeps the CPU route.
    Restore-ODSPortalLemonadeUpgrade
    $exe = Install-ODSPortalLemonade $SourceRoot $NonInteractive
    if (-not $exe) {
        Write-Host '         Continuing without the GPU: the model will run on the CPU (slower).'
        return @()
    }
    $modelsDir = Get-ODSPortalLemonadeModel $Plan
    $previous = Get-ODSPortalLemonadeTask
    $journal = $null
    if ($previous) {
        $oldExe = Get-ODSPortalDurableTaskExecutable $previous
        $journal = Save-ODSPortalLemonadeUpgrade $previous $oldExe $exe
    }
    try {
        if ($previous) {
            Stop-ODSPortalLemonade $oldExe
            $journal.Phase = 'stopped'
            Write-ODSPrivateEnvFile -Path (Get-ODSPortalLemonadeUpgradeJournalPath) -Content ($journal | ConvertTo-Json -Depth 6 -Compress)
        }
        $port = Select-ODSPortalLemonadePort
        $contract = Get-ODSLemonadeLaunchContract -ExecutablePath $exe -Port $port -ModelsDir $modelsDir -ContextSize $Plan.ContextSize
        Write-Host "         Starting Lemonade Server $($contract.Version) on 127.0.0.1:$port..."
        $expectedXml = if ($journal) { [string]$journal.TaskXml } else { '' }
        $registration = Register-ODSPortalLemonadeTask $contract $Plan.GgufFile $WslDistro $WslInstallDir ([bool]$previous) $expectedXml
        Write-Host '         Restoring the configured GPU model (the first load also downloads the GPU runtime)...'
        $modelId = Wait-ODSPortalLemonadeReady $registration
        if ($journal) { Remove-Item -LiteralPath (Get-ODSPortalLemonadeUpgradeJournalPath) -Force }
    } catch {
        $failure = $_.Exception.Message
        if ($journal) {
            try { Restore-ODSPortalLemonadeUpgrade }
            catch { throw "Lemonade upgrade failed: $failure Recovery needs review: $($_.Exception.Message)" }
        }
        throw
    }
    Write-Host "         GPU model ready: $modelId ($($Plan.ContextSize) tokens of context)."
    return @(
        '--lemonade-url', "http://localhost:$port",
        '--lemonade-host-transport', 'model-router',
        '--lemonade-model', $modelId,
        '--lemonade-gpu-name', $Plan.GpuName,
        '--lemonade-gpu-vram-mb', [string]$Plan.VramMB
    )
}
