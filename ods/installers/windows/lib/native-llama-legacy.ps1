# ============================================================================
# ODS Windows Installer -- native llama-server for the non-Portal installer
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: install-windows.ps1 and ods.ps1 run ggml-org's llama-server.exe on
#          this PC for an AMD GPU (WSL2 cannot drive it). This file:
#            - stages and qualifies the pinned runtime before any change,
#            - publishes a verified copy at <InstallDir>\llama-server, the
#              path ods.ps1, scripts/bootstrap-upgrade.sh and the host agent
#              launch (pin.json is verified before every ODS launch),
#            - keeps the API key file, launch options, log and readiness
#              record in %LOCALAPPDATA%\ODS\native-runtime (containers mount
#              the install folder, so private files stay outside it),
#            - launches from the current .env and proves the served model,
#            - registers the at-logon task ODSNativeLlamaRuntime (Limited),
#              which runs "ods.ps1 native-llm-start".
#
# Launch contract (Round F, section 5): --model, --alias <GGUF>, loopback,
# --parallel 1, --device, --metrics, --no-webui, --api-key-file, --log-file.
# .env tunables pass the native-llama-runtime.ps1 allow-list. A registered
# model-store profile keeps its own qualified runtime and arguments.
# Requires native-llama-runtime.ps1, native-llama-args.ps1 and the Task
# Scheduler principal helpers in backend-contract.ps1.
# ============================================================================

$script:ODSNativeLlamaLegacyTaskName = 'ODSNativeLlamaRuntime'

function Get-ODSNativeLlamaLegacyPath([string]$Name) {
    return (Join-Path (Get-ODSNativeRuntimeDir) $Name)
}

# --- Stage and qualify (before the installer changes anything) ---------------

function Initialize-ODSNativeLlamaLegacyRuntime {
    <#
    .SYNOPSIS
        Download (when needed), verify and qualify the pinned llama-server.
    .OUTPUTS
        Pin, Runtime and Device. Device is $null on the explicit CPU route:
        no usable Vulkan device on a fresh install. With an existing ODS AMD
        runtime that case stops setup instead, so nothing changes.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [string]$AdapterName = '',
        [bool]$HasExistingRuntime = $false
    )
    $pin = Get-ODSNativeLlamaPin -SourceRoot $SourceRoot
    if (-not (Test-Path -LiteralPath (Get-ODSNativeLlamaInstallDirectory $pin))) {
        $mib = [math]::Ceiling($pin.Size / 1MB)
        Write-AI "Downloading llama.cpp $($pin.ReleaseTag) (Vulkan) from github.com/ggml-org (about $mib MiB, SHA-256 pinned)..."
    }
    $runtime = Install-ODSNativeLlamaRuntime -Pin $pin
    $qualification = Test-ODSNativeLlamaQualification -ExecutablePath $runtime.ExecutablePath -ExpectedBuild $pin.Build -AdapterName $AdapterName
    if ($qualification.PolicyHint) { Write-AIWarn $qualification.PolicyHint }
    if (-not $qualification.Device) {
        $seen = @($qualification.Devices | ForEach-Object { "$($_.Name) $($_.Description)" }) -join '; '
        $found = ''
        if ($seen) { $found = " (it lists: $seen)" }
        $message = "llama.cpp found no usable Vulkan device for $AdapterName$found. Update the AMD Adrenalin driver from amd.com/support and restart Windows."
        if ($HasExistingRuntime) { throw "$message Your current ODS model runtime was not changed; rerun the installer after updating the driver." }
        Write-AIWarn "$message The model will run on the CPU (slower); rerun the installer afterwards to use the GPU."
    } else {
        Write-AISuccess "llama.cpp $($pin.ReleaseTag) qualified on $($qualification.Device.Name): $($qualification.Device.Description) ($($qualification.Device.TotalMiB) MiB)"
    }
    return [pscustomobject]@{ Pin = $pin; Runtime = $runtime; Device = $qualification.Device }
}

# --- Private launch options and key --------------------------------------------

function Write-ODSNativeLlamaLegacyOptions {
    <#
    .SYNOPSIS
        Record the qualified device and the pinned runtime identity. Device
        "none" is the explicit CPU route.
    #>
    param([Parameter(Mandatory = $true)]$Runtime, [AllowNull()]$Device)
    New-Item -ItemType Directory -Force -Path (Get-ODSNativeRuntimeDir) | Out-Null
    $deviceName = 'none'
    if ($Device) { $deviceName = [string]$Device.Name }
    $options = [ordered]@{
        schemaVersion = 1
        Device = $deviceName
        ApiKeyPath = (Get-ODSNativeLlamaLegacyPath 'api-key')
        LogPath = (Get-ODSNativeLlamaLegacyPath 'llama-server.log')
        ReleaseTag = [string]$Runtime.ReleaseTag
        ZipSha256 = [string]$Runtime.ZipSha256
    }
    Write-ODSPrivateEnvFile -Path (Get-ODSNativeLlamaLegacyPath 'runtime-options.json') -Content ($options | ConvertTo-Json -Compress)
    return [pscustomobject]$options
}

function Read-ODSNativeLlamaLegacyOptions {
    $path = Get-ODSNativeLlamaLegacyPath 'runtime-options.json'
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw 'The native llama-server launch options are missing; rerun the ODS installer.'
    }
    $options = Read-ODSNativeLlamaJson $path
    if ($options.schemaVersion -ne 1 -or [string]$options.Device -cnotmatch '^(Vulkan[0-9]{1,2}|none)$' -or
        -not [IO.Path]::IsPathRooted([string]$options.ApiKeyPath) -or
        -not [IO.Path]::IsPathRooted([string]$options.LogPath) -or
        [string]$options.ReleaseTag -cnotmatch '^b[0-9]{3,6}$' -or
        [string]$options.ZipSha256 -cnotmatch '^[0-9a-f]{64}$') {
        throw 'The native llama-server launch options are invalid; rerun the ODS installer.'
    }
    return $options
}

function Sync-ODSNativeLlamaLegacyApiKey {
    <#
    .SYNOPSIS
        Keep the --api-key-file equal to LLAMA_SERVER_API_KEY in .env, the
        value LiteLLM, the model router and the host agent send. Returns it.
    #>
    param([Parameter(Mandatory = $true)][hashtable]$EnvMap, [Parameter(Mandatory = $true)][string]$Path)
    $key = [string]$EnvMap['LLAMA_SERVER_API_KEY']
    if ($key -cnotmatch '^[0-9a-f]{64}$') {
        throw 'LLAMA_SERVER_API_KEY in .env is missing or is not 64 hex characters; rerun the ODS installer.'
    }
    $expected = [Text.Encoding]::ASCII.GetBytes($key)
    if ((Test-Path -LiteralPath $Path -PathType Leaf) -and
        [Convert]::ToBase64String([IO.File]::ReadAllBytes($Path)) -ceq [Convert]::ToBase64String($expected)) {
        return $key
    }
    Write-ODSNativeLlamaApiKeyFile $Path $key
    return $key
}

# --- Launch arguments from .env -------------------------------------------------

function Get-ODSNativeLlamaLegacyEnvValue([hashtable]$EnvMap, [string]$Name) {
    if ($EnvMap.ContainsKey($Name)) { return ([string]$EnvMap[$Name]).Trim() }
    return ''
}

function Get-ODSNativeLlamaLegacyReasoning([hashtable]$EnvMap) {
    # LLAMA_REASONING (off, on, auto) and llama.cpp's matching
    # --reasoning-format for binaries without --reasoning.
    $mode = Get-ODSNativeLlamaLegacyEnvValue $EnvMap 'LLAMA_REASONING'
    if (-not $mode) { $mode = 'off' }
    # Modes are case-insensitive, as in Get-ODSNativeReasoningArgs; the
    # fallback format must be llama.cpp's lowercase name.
    if ($mode -in @('off', 'on', 'auto')) { $mode = $mode.ToLowerInvariant() }
    $format = $mode
    if ($mode -ceq 'off') { $format = 'none' } elseif ($mode -ceq 'on') { $format = 'deepseek' }
    return [pscustomobject]@{ Mode = $mode; Format = $format }
}

function Get-ODSNativeLlamaLegacyTuning {
    <#
    .SYNOPSIS
        The .env reasoning mode and tunables as llama-server arguments for
        this binary. LLAMA_PARALLEL is ignored: one slot keeps the /props
        context proof valid.
    #>
    param([Parameter(Mandatory = $true)][hashtable]$EnvMap, [Parameter(Mandatory = $true)][string]$Executable)
    $choice = Get-ODSNativeLlamaLegacyReasoning $EnvMap
    $mode = $choice.Mode
    $format = $choice.Format
    $reasoning = @(Get-ODSNativeReasoningArgs -Executable $Executable -Mode $mode -FallbackFormat $format)
    $extra = @()
    $warnings = @()
    foreach ($pair in @(
            @('LLAMA_ARG_FLASH_ATTN', '--flash-attn'), @('LLAMA_ARG_CACHE_TYPE_K', '--cache-type-k'),
            @('LLAMA_ARG_CACHE_TYPE_V', '--cache-type-v'), @('LLAMA_ARG_N_CPU_MOE', '--n-cpu-moe'),
            @('LLAMA_ARG_CTX_CHECKPOINTS', '--ctx-checkpoints'), @('LLAMA_ARG_CACHE_RAM', '--cache-ram'),
            @('LLAMA_ARG_SPEC_TYPE', '--spec-type'), @('LLAMA_ARG_SPEC_DRAFT_N_MAX', '--spec-draft-n-max'),
            @('LLAMA_ARG_SPEC_DRAFT_TYPE_K', '--spec-draft-type-k'), @('LLAMA_ARG_SPEC_DRAFT_TYPE_V', '--spec-draft-type-v'))) {
        $setting = Get-ODSNativeLlamaLegacyEnvValue $EnvMap $pair[0]
        if ($setting) { $extra += @($pair[1], $setting) }
    }
    $checkpoint = Get-ODSNativeCheckpointIntervalArgs -Executable $Executable -Value (Get-ODSNativeLlamaLegacyEnvValue $EnvMap 'LLAMA_ARG_CHECKPOINT_EVERY_NT')
    if ($checkpoint.Warning) { $warnings += $checkpoint.Warning }
    $extra += @($checkpoint.Arguments)
    $noCache = (Get-ODSNativeLlamaLegacyEnvValue $EnvMap 'LLAMA_ARG_NO_CACHE_PROMPT').ToLowerInvariant()
    if ($noCache -and $noCache -notin @('0', 'false', 'off', 'no')) { $extra += '--no-cache-prompt' }
    $parallel = Get-ODSNativeLlamaLegacyEnvValue $EnvMap 'LLAMA_PARALLEL'
    if ($parallel -and $parallel -ne '1') {
        $warnings += "LLAMA_PARALLEL=$parallel is ignored: the native llama-server runs one slot so its context can be proven."
    }
    return [pscustomobject]@{ Mode = $mode; Format = $format; ReasoningArguments = $reasoning; ExtraArguments = $extra; Warnings = $warnings }
}

function New-ODSNativeLlamaLegacyLaunch {
    <#
    .SYNOPSIS
        Executable, arguments and proof inputs for the .env selection.
    .PARAMETER Selection
        ods.ps1 Get-ODSNativeModelSelection output (modelPath and an optional
        registered model-store profile).
    #>
    param(
        [Parameter(Mandatory = $true)][hashtable]$EnvMap,
        [Parameter(Mandatory = $true)]$Selection,
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)]$Options,
        [Parameter(Mandatory = $true)][string]$PinnedExecutable
    )
    $modelPath = [string]$Selection.modelPath
    $gguf = Split-Path -Leaf $modelPath
    $contextText = Get-ODSNativeLlamaLegacyEnvValue $EnvMap 'CTX_SIZE'
    if (-not $contextText) { $contextText = Get-ODSNativeLlamaLegacyEnvValue $EnvMap 'MAX_CONTEXT' }
    if (-not $contextText -and $Selection.profile) { $contextText = [string]$Selection.profile.contextLength }
    [long]$contextSize = 0
    if (-not [long]::TryParse($contextText, [ref]$contextSize) -or $contextSize -lt 1) {
        throw 'CTX_SIZE in .env is not a positive number; rerun the ODS installer.'
    }
    $gpuLayers = Get-ODSNativeLlamaLegacyEnvValue $EnvMap 'N_GPU_LAYERS'
    if (-not $gpuLayers) { $gpuLayers = 'auto' }
    if ([string]$Options.Device -ceq 'none') { $gpuLayers = '0' }

    if ($Selection.profile) {
        # A registered model-store profile keeps its own qualified runtime and
        # argument list (it already runs one slot); ODS adds its listener,
        # alias, key and log. The profile's runtime is hash-verified by the
        # model-store resolver instead of pin.json, and is not probed here.
        $executable = [string]$Selection.profile.executable
        if ($gguf -cnotmatch '^[\x20-\x7e]{1,240}$') {
            throw 'llama.cpp on Windows needs an ASCII .gguf file name for its model alias.'
        }
        $reasoningFormat = (Get-ODSNativeLlamaLegacyReasoning $EnvMap).Format
        $arguments = @(
            '--model', (ConvertTo-ODSNativeLlamaArgumentPath $modelPath),
            '--alias', $gguf,
            '--host', '127.0.0.1',
            '--port', [string]$Port,
            '--ctx-size', [string]$contextSize,
            '--n-gpu-layers', $gpuLayers,
            '--metrics',
            '--no-webui',
            '--api-key-file', (ConvertTo-ODSNativeLlamaArgumentPath ([string]$Options.ApiKeyPath)),
            '--log-file', (ConvertTo-ODSNativeLlamaArgumentPath ([string]$Options.LogPath)),
            '--reasoning-format', $reasoningFormat
        )
        if ([string]$Selection.profile.backend -ceq 'vulkan') { $arguments += @('--device', [string]$Options.Device) }
        $arguments += @($Selection.profile.args | ForEach-Object { [string]$_ })
        return [pscustomobject]@{ ExecutablePath = $executable; Arguments = $arguments; GgufFile = $gguf
            ContextSize = $contextSize; ModelPaths = @($arguments[1], $modelPath); LogPath = [string]$Options.LogPath
            Pinned = $false; Warnings = @() }
    }

    $tuning = Get-ODSNativeLlamaLegacyTuning -EnvMap $EnvMap -Executable $PinnedExecutable
    $plan = [pscustomobject]@{ ExecutablePath = $PinnedExecutable; Port = $Port; ModelsDir = (Split-Path -Parent $modelPath)
        ContextSize = $contextSize; GgufFile = $gguf }
    $launchOptions = [pscustomobject]@{ schemaVersion = 1; Device = [string]$Options.Device; NGpuLayers = $gpuLayers
        ApiKeyPath = [string]$Options.ApiKeyPath; LogPath = [string]$Options.LogPath
        ReleaseTag = [string]$Options.ReleaseTag; ZipSha256 = [string]$Options.ZipSha256
        ReasoningArguments = $tuning.ReasoningArguments; ExtraArguments = $tuning.ExtraArguments }
    $arguments = @(New-ODSNativeLlamaLaunchArguments $plan $launchOptions)
    return [pscustomobject]@{ ExecutablePath = $PinnedExecutable; Arguments = $arguments; GgufFile = $gguf
        ContextSize = $contextSize; ModelPaths = @($arguments[1], $modelPath); LogPath = [string]$Options.LogPath
        Pinned = $true; Warnings = $tuning.Warnings }
}

# --- Launch and proof -----------------------------------------------------------

function Wait-ODSNativeLlamaLegacyPortFree([int]$Port, [int]$TimeoutSeconds = 15) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while (@(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue).Count) {
        if ((Get-Date) -ge $deadline) { return $false }
        Start-Sleep -Milliseconds 500
    }
    return $true
}

function Start-ODSNativeLlamaLegacyProcess {
    <#
    .SYNOPSIS
        Launch llama-server.exe detached, record its PID, prove the model and
        context, and write ready.json. A launch that fails its proof is
        stopped through the handle ODS holds, never by name or port.
    #>
    param(
        [Parameter(Mandatory = $true)]$Launch,
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$ApiKey,
        [Parameter(Mandatory = $true)][string]$PidFile,
        [int]$TimeoutSeconds = $script:ODSNativeLlamaStartupSeconds
    )
    $readyPath = Get-ODSNativeLlamaLegacyPath 'ready.json'
    if (Test-Path -LiteralPath $readyPath) { Remove-Item -LiteralPath $readyPath -Force }
    if (-not (Wait-ODSNativeLlamaLegacyPortFree -Port $Port)) {
        throw "Port $Port is already in use, so llama-server cannot start; no process was changed. Close the program using it, or set AMD_INFERENCE_PORT to a free port and rerun the installer."
    }
    if (Test-Path -LiteralPath $Launch.LogPath -PathType Leaf) {
        Move-Item -LiteralPath $Launch.LogPath -Destination ([string]$Launch.LogPath + '.1') -Force
    }
    $child = Start-Process -FilePath $Launch.ExecutablePath -ArgumentList (ConvertTo-ODSNativeLlamaArgumentString $Launch.Arguments) `
        -WorkingDirectory (Split-Path -Parent $Launch.ExecutablePath) -WindowStyle Hidden -PassThru
    try {
        $null = $child.Handle
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $PidFile) | Out-Null
        Set-Content -LiteralPath $PidFile -Value $child.Id -Encoding ASCII
        Wait-ODSNativeLlamaStartup -Port $Port -Process $child -TimeoutSeconds $TimeoutSeconds
        Assert-ODSNativeLlamaListener $Port $child.Id $Launch.ExecutablePath
        $proof = Get-ODSNativeLlamaModelProof -Port $Port -GgufFile $Launch.GgufFile -ModelPaths $Launch.ModelPaths `
            -ContextSize $Launch.ContextSize -ApiKey $ApiKey
        if (-not $proof.ContextVerified) { throw $proof.Message }
        $ready = [ordered]@{ ProcessId = $child.Id; StartedAt = $child.StartTime.ToUniversalTime().ToString('o')
            ExecutablePath = [string]$Launch.ExecutablePath; Port = $Port; ModelId = $proof.ModelId
            ContextSize = $Launch.ContextSize; RuntimeContext = $proof.RuntimeContext; ContextLength = $proof.ContextLength }
        Write-ODSPrivateEnvFile -Path $readyPath -Content ($ready | ConvertTo-Json -Compress)
        return [pscustomobject]@{ ProcessId = $child.Id; Proof = $proof }
    } catch {
        $failure = $_.Exception.Message
        if (-not $child.HasExited) {
            $child.Kill()
            # A large model can take well over five seconds to leave GPU memory.
            if (-not $child.WaitForExit(60000)) { throw "$failure The launched llama-server did not exit within 60 seconds of being stopped." }
        }
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        throw $failure
    } finally {
        $child.Dispose()
    }
}

# --- The at-logon task ----------------------------------------------------------

function Get-ODSNativeLlamaLegacyTaskArguments([string]$InstallDir) {
    # The installation is named explicitly: a scheduled task does not inherit
    # the installer's ODS_HOME.
    return ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $InstallDir 'ods.ps1') + '" native-llm-start "' + $InstallDir + '"')
}

function Get-ODSNativeLlamaLegacyTask {
    <#
    .SYNOPSIS
        ODSNativeLlamaRuntime and whether its action is one ODS wrote: the
        "ods.ps1 native-llm-start" launcher, or the earlier direct launch of
        <InstallDir>\llama-server\llama-server.exe. $null when absent.
    #>
    param([Parameter(Mandatory = $true)][string]$InstallDir)
    $lookupErrors = @()
    $tasks = @(Get-ScheduledTask -TaskName $script:ODSNativeLlamaLegacyTaskName -TaskPath '\' -ErrorAction SilentlyContinue -ErrorVariable lookupErrors)
    if (@($lookupErrors | Where-Object { $_.CategoryInfo.Category -ne 'ObjectNotFound' }).Count) {
        throw 'Cannot read the ODSNativeLlamaRuntime scheduled task; no process was changed.'
    }
    if (-not $tasks.Count) { return $null }
    $actions = @($tasks[0].Actions)
    $owned = $false
    if ($tasks.Count -eq 1 -and $actions.Count -eq 1) {
        $execute = [string]$actions[0].Execute
        $published = Join-Path (Join-Path $InstallDir 'llama-server') 'llama-server.exe'
        $owned = ($execute -ieq $published) -or
            ([IO.Path]::GetFileName($execute) -in @('powershell.exe', 'pwsh.exe') -and
                [string]$actions[0].Arguments -ceq (Get-ODSNativeLlamaLegacyTaskArguments $InstallDir))
    }
    return [pscustomobject]@{ Task = $tasks[0]; Owned = $owned }
}

function Register-ODSNativeLlamaLegacyTask {
    <#
    .SYNOPSIS
        Start the native llama-server at logon, at the user's own (Limited)
        integrity level so the host agent can stop it for model swaps.
    #>
    param([Parameter(Mandatory = $true)][string]$InstallDir)
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (Get-ODSNativeLlamaLegacyTaskArguments $InstallDir) -WorkingDirectory $InstallDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User (Resolve-ODSInteractiveScheduledTaskUser)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ODSInteractiveScheduledTaskPrincipal -RunLevel Limited
    Register-ScheduledTask -TaskName $script:ODSNativeLlamaLegacyTaskName -TaskPath '\' -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Force -ErrorAction Stop `
        -Description 'ODS: starts the native llama-server (llama.cpp, Vulkan) for the AMD GPU at logon' | Out-Null
}

function Wait-ODSNativeLlamaLegacyTask {
    <#
    .SYNOPSIS
        Wait for the launcher run started after PreviousRunTime to finish and
        return its exit code.
    #>
    param([datetime]$PreviousRunTime, [int]$TimeoutSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $task = Get-ScheduledTask -TaskName $script:ODSNativeLlamaLegacyTaskName -TaskPath '\' -ErrorAction Stop
        $info = Get-ScheduledTaskInfo -InputObject $task -ErrorAction Stop
        if ([string]$task.State -ne 'Running' -and $info.LastRunTime -ne $PreviousRunTime) { return [int]$info.LastTaskResult }
        Start-Sleep -Seconds 2
    }
    throw "llama-server did not finish starting within $TimeoutSeconds seconds."
}

function Get-ODSNativeLlamaLegacyFailure {
    # The last line native-llm-start recorded, for the installer's message.
    $log = Get-ODSNativeLlamaLegacyPath 'native-llm-start.log'
    if (Test-Path -LiteralPath $log -PathType Leaf) {
        $last = @(Get-Content -LiteralPath $log -Tail 1 -ErrorAction SilentlyContinue)
        if ($last.Count) { return [string]$last[0] }
    }
    return "see $(Get-ODSNativeLlamaLegacyPath 'llama-server.log')"
}

function Write-ODSNativeLlamaLegacyLog([string]$Message) {
    New-Item -ItemType Directory -Force -Path (Get-ODSNativeRuntimeDir) | Out-Null
    Add-Content -LiteralPath (Get-ODSNativeLlamaLegacyPath 'native-llm-start.log') -Encoding UTF8 `
        -Value ('{0:o} {1}' -f (Get-Date), $Message)
}

# --- Cutover (installer phase 8) -----------------------------------------------

function Get-ODSNativeLlamaLegacyProfileExecutable {
    <#
    .SYNOPSIS
        The registered model-store runtime of the active model, if any, read
        from data\model-stores.json without running anything.
    #>
    param([Parameter(Mandatory = $true)][string]$InstallDir, [Parameter(Mandatory = $true)][hashtable]$EnvMap)
    $registry = Join-Path (Join-Path $InstallDir 'data') 'model-stores.json'
    $storeId = Get-ODSNativeLlamaLegacyEnvValue $EnvMap 'ODS_ACTIVE_MODEL_STORE'
    $gguf = Get-ODSNativeLlamaLegacyEnvValue $EnvMap 'GGUF_FILE'
    if (-not $storeId -or $storeId -eq 'default' -or -not $gguf -or -not (Test-Path -LiteralPath $registry -PathType Leaf)) { return '' }
    $document = Read-ODSNativeLlamaJson $registry
    foreach ($store in @($document.stores)) {
        if ([string]$store.id -cne $storeId -or -not $store.profiles) { continue }
        $entry = $store.profiles.PSObject.Properties[$gguf]
        if ($entry -and [IO.Path]::IsPathRooted([string]$entry.Value.executable)) { return [string]$entry.Value.executable }
    }
    return ''
}

function Get-ODSNativeLlamaLegacyRegisteredExecutables {
    <#
    .SYNOPSIS
        Every runtime executable registered for this installation's model
        stores (data\model-stores.json), read without running anything. The
        server a model switch replaces may run any one of them.
    #>
    param([Parameter(Mandatory = $true)][string]$InstallDir)
    $registry = Join-Path (Join-Path $InstallDir 'data') 'model-stores.json'
    if (-not (Test-Path -LiteralPath $registry -PathType Leaf)) { return @() }
    $document = Read-ODSNativeLlamaJson $registry
    $paths = foreach ($store in @($document.stores)) {
        if (-not $store.profiles) { continue }
        foreach ($entry in $store.profiles.PSObject.Properties) {
            $executable = [string]$entry.Value.executable
            if ($executable -and [IO.Path]::IsPathRooted($executable)) { $executable }
        }
    }
    return @($paths | Select-Object -Unique)
}

function Stop-ODSNativeLlamaLegacyProcess {
    <#
    .SYNOPSIS
        Stop the llama-server ODS launched: the PID-file process or the port
        listener, only when its executable is one ODS launches. Anything else
        on the port is left running.
    .DESCRIPTION
        Throws when an owned process cannot be proven or does not exit; the
        PID record is then kept and nothing else changes, so callers can
        report that the running server keeps serving.
    .OUTPUTS
        The number of processes stopped.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$PidFile,
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string[]]$ExecutablePaths
    )
    $known = @($ExecutablePaths | Where-Object { $_ })
    $nodes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $isKnown = { param($Node) $path = [string]$Node.ExecutablePath; $path -and @($known | Where-Object { $_ -ieq $path }).Count }
    $roots = [Collections.Generic.List[object]]::new()
    $fromPidFile = $false
    if (Test-Path -LiteralPath $PidFile -PathType Leaf) {
        $raw = ([string](Get-Content -LiteralPath $PidFile -Raw -ErrorAction Stop)).Trim()
        if ($raw -match '^\d+$') {
            $match = @($nodes | Where-Object { $_.ProcessId -eq [int]$raw -and (& $isKnown $_) })
            if ($match.Count -eq 1) { $roots.Add($match[0]); $fromPidFile = $true }
        }
    }
    foreach ($listener in @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) {
        $match = @($nodes | Where-Object { $_.ProcessId -eq [int]$listener.OwningProcess -and (& $isKnown $_) })
        if ($match.Count -eq 1 -and -not @($roots | Where-Object { $_.ProcessId -eq $match[0].ProcessId }).Count) { $roots.Add($match[0]) }
    }
    if ($roots.Count) {
        $owned = Get-ODSPortalOwnedProcessTree @($roots) $nodes
        try {
            Stop-ODSPortalOwnedProcesses $owned.Handles
        } finally {
            foreach ($process in $owned.Handles) { $process.Dispose() }
        }
    }
    if ($fromPidFile) { Remove-Item -LiteralPath $PidFile -Force }
    return $roots.Count
}

function Stop-ODSNativeLlamaLegacyRuntime {
    <#
    .SYNOPSIS
        Stop the llama-server ODS's native runtime started: the PID-file
        process or the port listener, only when its executable is one ODS
        launches (the published copy, or the active model-store runtime).
        The task is disabled first so it cannot relaunch. Anything else is
        left running; the start that follows reports a busy port.
    .OUTPUTS
        The number of processes stopped.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$InstallDir,
        [Parameter(Mandatory = $true)][string]$PidFile,
        [Parameter(Mandatory = $true)][int]$Port,
        [string[]]$ExecutablePaths = @()
    )
    $known = @((Join-Path (Join-Path $InstallDir 'llama-server') 'llama-server.exe')) + @($ExecutablePaths | Where-Object { $_ })
    $task = Get-ODSNativeLlamaLegacyTask -InstallDir $InstallDir
    if ($task -and $task.Owned) {
        Disable-ScheduledTask -TaskName $script:ODSNativeLlamaLegacyTaskName -TaskPath '\' -ErrorAction Stop | Out-Null
        if ([string]$task.Task.State -eq 'Running') {
            Stop-ScheduledTask -TaskName $script:ODSNativeLlamaLegacyTaskName -TaskPath '\' -ErrorAction Stop
        }
    } elseif ($task) {
        Write-AIWarn 'The ODSNativeLlamaRuntime task does not run an ODS launcher; ODS replaces its action but stops no process it started.'
    }
    return (Stop-ODSNativeLlamaLegacyProcess -PidFile $PidFile -Port $Port -ExecutablePaths $known)
}

function Remove-ODSNativeLlamaLegacyRuntime {
    <#
    .SYNOPSIS
        A reinstall without the native AMD runtime (cloud, another GPU) stops
        ODS's llama-server and removes its logon task, so the old model does
        not start again at every logon. Returns $true when a task was removed.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$InstallDir,
        [Parameter(Mandatory = $true)][string]$PidFile,
        [Parameter(Mandatory = $true)][int]$Port
    )
    $task = Get-ODSNativeLlamaLegacyTask -InstallDir $InstallDir
    if (-not $task -or -not $task.Owned) { return $false }
    $null = Stop-ODSNativeLlamaLegacyRuntime -InstallDir $InstallDir -PidFile $PidFile -Port $Port
    Unregister-ScheduledTask -TaskName $script:ODSNativeLlamaLegacyTaskName -TaskPath '\' -Confirm:$false -ErrorAction Stop
    return $true
}

function Invoke-ODSNativeLlamaLegacyCutover {
    <#
    .SYNOPSIS
        Installer phase 8 on Windows AMD: retire ODS's own Lemonade runtime if
        there is one, replace ODS's llama-server, start it through the
        at-logon task and check what it proved. Returns ready.json.
    .DESCRIPTION
        .env was rendered for llama-server by phase 06. On failure the error
        names the cause; a retired Lemonade task stays registered but
        disabled, and nothing else of Lemonade is touched.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$InstallDir,
        [Parameter(Mandatory = $true)]$Stage,
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$PidFile,
        [int]$TimeoutSeconds = 1200
    )
    $envMap = Get-WindowsODSEnvMap -InstallDir $InstallDir
    $lemonade = Get-ODSLegacyLemonadeRuntime -InstallDir $InstallDir -Port $Port
    if ($lemonade -and -not $lemonade.Owned) {
        Write-AIWarn "A scheduled task named ODSLemonadeRuntime exists, but $($lemonade.Reason), so ODS left it unchanged."
        $lemonade = $null
    }
    $publishedDir = Join-Path $InstallDir 'llama-server'
    $previousDirectory = ''
    if (Test-Path -LiteralPath (Join-Path $publishedDir 'pin.json') -PathType Leaf) {
        $previousPin = Read-ODSNativeLlamaJson (Join-Path $publishedDir 'pin.json')
        if ([string]$previousPin.releaseTag -cmatch '^b[0-9]{3,6}$') {
            $previousDirectory = Join-Path (Get-ODSNativeLlamaRoot) ('{0}-win-vulkan-x64' -f $previousPin.releaseTag)
        }
    }
    $profileExecutable = Get-ODSNativeLlamaLegacyProfileExecutable -InstallDir $InstallDir -EnvMap $envMap
    $null = Stop-ODSNativeLlamaLegacyRuntime -InstallDir $InstallDir -PidFile $PidFile -Port $Port -ExecutablePaths @($profileExecutable)
    if ($lemonade) {
        Write-AI 'Stopping the Lemonade runtime ODS used before (its task is disabled; Lemonade itself is not changed)...'
        $stopped = Stop-ODSLegacyLemonadeRuntime -Runtime $lemonade -PidFile $PidFile -Port $Port
        if ($stopped.Left) { throw $stopped.Left }
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }
    $null = Publish-ODSNativeLlamaRuntimeCopy -Runtime $Stage.Runtime -Destination $publishedDir
    $options = Write-ODSNativeLlamaLegacyOptions -Runtime $Stage.Runtime -Device $Stage.Device
    $null = Sync-ODSNativeLlamaLegacyApiKey -EnvMap $envMap -Path ([string]$options.ApiKeyPath)
    $readyPath = Get-ODSNativeLlamaLegacyPath 'ready.json'
    if (Test-Path -LiteralPath $readyPath) { Remove-Item -LiteralPath $readyPath -Force }
    Register-ODSNativeLlamaLegacyTask -InstallDir $InstallDir
    $previousRun = (Get-ScheduledTaskInfo -TaskName $script:ODSNativeLlamaLegacyTaskName -TaskPath '\' -ErrorAction Stop).LastRunTime
    Start-ScheduledTask -TaskName $script:ODSNativeLlamaLegacyTaskName -TaskPath '\' -ErrorAction Stop
    $exitCode = Wait-ODSNativeLlamaLegacyTask -PreviousRunTime $previousRun -TimeoutSeconds $TimeoutSeconds
    if ($exitCode -ne 0 -or -not (Test-Path -LiteralPath $readyPath -PathType Leaf)) {
        $retired = ''
        if ($lemonade) { $retired = ' The former ODSLemonadeRuntime task stays registered but disabled.' }
        throw "llama-server did not start: $(Get-ODSNativeLlamaLegacyFailure).$retired"
    }
    $ready = Read-ODSNativeLlamaJson $readyPath
    $gguf = Get-ODSNativeLlamaLegacyEnvValue $envMap 'GGUF_FILE'
    if ([string]$ready.ModelId -cne $gguf -or [int]$ready.Port -ne $Port) {
        throw "llama-server reported '$($ready.ModelId)' on port $($ready.Port), not $gguf on port $Port."
    }
    Assert-ODSNativeLlamaListener $Port ([int]$ready.ProcessId) ([string]$ready.ExecutablePath)
    if ($lemonade) { Complete-ODSLegacyLemonadeRetirement -Runtime $lemonade -InstallDir $InstallDir }
    Remove-ODSNativeLlamaOldRuntimes -KeepDirectory $Stage.Runtime.Directory -PreviousDirectory $previousDirectory
    return $ready
}
