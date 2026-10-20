# ============================================================================
# ODS Windows Installer -- Phase 04: Requirements Check
# ============================================================================
# Part of: installers/windows/phases/
# Purpose: Tier-specific RAM / disk minimums, Windows port conflict detection,
#          Ollama port shadow check. Warns on unmet requirements; allows
#          continuation after user confirmation.
#
# Reads:
#   $selectedTier, $tierConfig    -- from phase 02
#   $gpuInfo, $systemRamGB        -- from phase 02
#   $enableVoice, $enableWorkflows, $enableRag  -- from phase 03
#   $installDir                   -- from orchestrator context
#   $force, $nonInteractive, $dryRun
#
# Writes:
#   $requirementsMet  -- bool: $false if any hard requirement is unmet
#
# Modder notes:
#   Adjust MIN_RAM_GB / MIN_DISK_GB per-tier tables here.
#   Add new service port checks by adding entries to $portsToCheck.
# ============================================================================

Write-Phase -Phase 4 -Total 13 -Name "REQUIREMENTS CHECK" -Estimate "~10 seconds"

$requirementsMet = $true

# ── Helper: check if a TCP port is listening ─────────────────────────────────
function Test-WindowsPortInUse {
    <#
    .SYNOPSIS
        Check whether a local TCP port is already listening.
    .OUTPUTS
        @{ InUse; ProcessName; ProcessId }
    #>
    param([int]$Port)

    # Get-NetTCPConnection is available on Windows 8+ / Server 2012+
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        if ($conn) {
            $proc = Get-Process -Id $conn[0].OwningProcess -ErrorAction SilentlyContinue
            return @{
                InUse       = $true
                ProcessName = $(if ($proc) { $proc.ProcessName } else { "unknown" })
                ProcessId   = $conn[0].OwningProcess
                Listeners   = @($conn | Select-Object LocalAddress, OwningProcess)
            }
        }
    } catch {
        # Get-NetTCPConnection unavailable (very old Windows) -- fall back to
        # netstat via cmd.exe which is always present.
        try {
            $netstatOut = & cmd.exe /c "netstat -ano" 2>$null |
                Where-Object { $_ -match "0\.0\.0\.0:$Port\s|127\.0\.0\.1:$Port\s" } |
                Select-Object -First 1
            if ($netstatOut) {
                # Extract PID from last column of netstat output
                $pid_ = ($netstatOut -split '\s+')[-1]
                $proc = Get-Process -Id $pid_ -ErrorAction SilentlyContinue
                return @{
                    InUse       = $true
                    ProcessName = $(if ($proc) { $proc.ProcessName } else { "pid $pid_" })
                    ProcessId   = [int]$pid_
                }
            }
        } catch { }
    }

    return @{ InUse = $false; ProcessName = ""; ProcessId = 0 }
}

function Resolve-WindowsLlmPreflightPort {
    <#
    .SYNOPSIS
        Resolve the host LLM port that the selected Windows backend will bind.
    .OUTPUTS
        Port number, or 0 when cloud mode does not bind a local inference port.
    #>
    param(
        [string]$GpuBackend,
        [switch]$CloudMode,
        [int]$NativeDefaultPort = 8080,
        [string]$InstallDir = ""
    )

    if ($CloudMode) { return 0 }

    $defaultPort = 11434
    $candidate = $null
    $persistedEnv = @{}
    if ($InstallDir -and (Get-Command Get-WindowsODSEnvMap -ErrorAction SilentlyContinue)) {
        $persistedEnv = Get-WindowsODSEnvMap -InstallDir $InstallDir
    }
    if ($GpuBackend -eq "amd") {
        $defaultPort = $NativeDefaultPort
        $candidate = $env:AMD_INFERENCE_PORT
        if (-not $candidate -and $persistedEnv.ContainsKey("AMD_INFERENCE_PORT")) {
            $candidate = $persistedEnv["AMD_INFERENCE_PORT"]
        }
    } elseif ($env:OLLAMA_PORT) {
        $candidate = $env:OLLAMA_PORT
    } elseif ($env:LLAMA_SERVER_PORT) {
        $candidate = $env:LLAMA_SERVER_PORT
    } elseif ($persistedEnv.ContainsKey("OLLAMA_PORT")) {
        $candidate = $persistedEnv["OLLAMA_PORT"]
    } elseif ($persistedEnv.ContainsKey("LLAMA_SERVER_PORT")) {
        $candidate = $persistedEnv["LLAMA_SERVER_PORT"]
    }

    if ($candidate) {
        $parsedPort = 0
        if ([int]::TryParse(([string]$candidate).Trim(), [ref]$parsedPort) -and
            $parsedPort -ge 1 -and $parsedPort -le 65535) {
            return $parsedPort
        }
    }

    return $defaultPort
}

function Test-WindowsODSNativeLlmOwnsPort {
    <#
    .SYNOPSIS
        True when the listener is ODS's own AMD model runtime, which phase 8
        replaces: this installation's published llama-server.exe or active
        model-store runtime, or the Lemonade tree of an ODS-owned
        ODSLemonadeRuntime task (exact executable and command line). Never
        inferred from a process name, folder or port.
    #>
    param(
        [hashtable]$PortResult,
        [string]$InstallDir,
        [int]$Port
    )

    if (-not $PortResult -or -not $PortResult.InUse -or [int]$PortResult.ProcessId -le 0 -or -not $InstallDir) {
        return $false
    }
    $nodes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $listener = @($nodes | Where-Object { $_.ProcessId -eq [int]$PortResult.ProcessId })
    if ($listener.Count -ne 1 -or -not $listener[0].ExecutablePath) { return $false }
    $runtimes = @(
        (Join-Path (Join-Path $InstallDir "llama-server") "llama-server.exe"),
        (Get-ODSNativeLlamaLegacyProfileExecutable -InstallDir $InstallDir -EnvMap (Get-WindowsODSEnvMap -InstallDir $InstallDir))
    ) | Where-Object { $_ }
    if (@($runtimes | Where-Object { $_ -ieq [string]$listener[0].ExecutablePath }).Count) { return $true }

    $lemonade = Get-ODSLegacyLemonadeRuntime -InstallDir $InstallDir -Port $Port
    if (-not $lemonade -or -not $lemonade.Owned) { return $false }
    # Lemonade's router can own the socket below the server process.
    $current = $listener[0]
    for ($depth = 0; $depth -lt 8 -and $current; $depth++) {
        if ([string]$current.ExecutablePath -ieq [string]$lemonade.ExecutablePath -and
            [string]$current.CommandLine -cin @($lemonade.CommandLines)) { return $true }
        $parent = @($nodes | Where-Object { $_.ProcessId -eq $current.ParentProcessId })
        $current = $(if ($parent.Count -eq 1) { $parent[0] } else { $null })
    }
    return $false
}

function Get-WindowsDockerPublishedPorts {
    # Only the local Desktop engine can explain a Windows Desktop listener.
    # Inspect every running container, including foreign projects, so a second
    # binding on another address cannot be hidden by an ODS label filter.
    try {
        if ($env:DOCKER_HOST) { return @() }
        $context = (& docker context show 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $context) { return @() }
        $endpoint = & docker context inspect $context --format '{{json .Endpoints.docker.Host}}' 2>$null
        if ($LASTEXITCODE -ne 0) { return @() }
        $endpoint = $endpoint | ConvertFrom-Json -ErrorAction Stop
        if ($endpoint -notin @('npipe:////./pipe/dockerDesktopLinuxEngine', 'npipe:////./pipe/docker_engine')) { return @() }
        $ids = @(& docker --context $context ps -q --no-trunc 2>$null)
        if ($LASTEXITCODE -ne 0 -or -not $ids.Count -or @($ids | Where-Object { $_ -notmatch '^[a-f0-9]{64}$' }).Count) { return @() }
        $raw = & docker --context $context container inspect @ids 2>$null
        if ($LASTEXITCODE -ne 0) { return @() }
        # PS5.1 does not enumerate JSON arrays on the pipeline. Assign first,
        # then count the contents, so a retained multi-service stack is valid.
        $containers = ConvertFrom-Json -InputObject ($raw -join "`n") -ErrorAction Stop
        if (@($containers).Count -ne $ids.Count -or @($containers.Id | Select-Object -Unique).Count -ne $ids.Count) { return @() }
        $result = @()
        foreach ($container in $containers) {
            if ($container.Id -notin $ids -or -not $container.State.Running) { return @() }
            $labels = $container.Config.Labels
            foreach ($port in $container.NetworkSettings.Ports.PSObject.Properties) {
                if ($port.Name -notmatch '/tcp$') { continue }
                foreach ($binding in @($port.Value)) {
                    if (-not $binding) { continue }
                    $result += [pscustomobject]@{
                        Id = [string]$container.Id
                        Project = [string]$labels.'com.docker.compose.project'
                        Service = [string]$labels.'com.docker.compose.service'
                        WorkingDir = [string]$labels.'com.docker.compose.project.working_dir'
                        HostPort = [string]$binding.HostPort
                        HostIp = [string]$binding.HostIp
                    }
                }
            }
        }
        return $result
    } catch { return @() }
}

function Test-WindowsODSComposeOwnsPort {
    param([hashtable]$PortResult, [string]$InstallDir, [int]$Port,
          [string]$ComposeService, [object[]]$PublishedPorts)
    try {
        if (-not $InstallDir -or -not $ComposeService -or -not $PortResult.InUse -or -not $PortResult.Listeners) { return $false }
        $expected = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\', '/')
        $bindings = @($PublishedPorts | Where-Object { $_.HostPort -ceq [string]$Port })
        if (-not $bindings.Count -or @($bindings.Id | Select-Object -Unique).Count -ne 1) { return $false }
        foreach ($binding in $bindings) {
            if ($binding.Project -cne 'ods' -or $binding.Service -cne $ComposeService -or
                -not [IO.Path]::IsPathRooted($binding.WorkingDir) -or
                ([IO.Path]::GetPathRoot($binding.WorkingDir)).Length -lt 3) { return $false }
            $actual = [IO.Path]::GetFullPath($binding.WorkingDir).TrimEnd('\', '/')
            if (-not [string]::Equals($actual, $expected, [StringComparison]::OrdinalIgnoreCase)) { return $false }
        }
        $backend = Join-Path $env:ProgramFiles 'Docker\Docker\resources\com.docker.backend.exe'
        foreach ($listener in $PortResult.Listeners) {
            if ([int]$listener.OwningProcess -le 0 -or $listener.LocalAddress -notin $bindings.HostIp) { return $false }
            $process = @(Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction Stop |
                Where-Object { $_.ProcessId -eq $listener.OwningProcess })
            if ($process.Count -ne 1 -or -not [string]::Equals([string]$process[0].ExecutablePath, $backend, [StringComparison]::OrdinalIgnoreCase)) { return $false }
        }
        return $true
    } catch { return $false }
}

function Get-WindowsODSSelectedPortConflicts {
    param(
        [System.Collections.IDictionary]$PortsToCheck,
        [string]$NativeLlmService = "",
        [string]$InstallDir = ""
    )

    $conflicts = @()
    $composeServices = @{
        'Open WebUI (chat)' = 'open-webui'; 'Dashboard' = 'dashboard'; 'Dashboard (remote)' = 'dashboard'
        'Dashboard API' = 'dashboard-api'; 'llama-server (LLM)' = 'llama-server'; 'LiteLLM (API gateway)' = 'litellm'
        'SearXNG (search)' = 'searxng'; 'Token Spy (usage monitor)' = 'token-spy'; 'Whisper (STT)' = 'whisper'
        'Kokoro (TTS)' = 'tts'; 'n8n (workflows)' = 'n8n'; 'Qdrant (gRPC)' = 'qdrant'; 'Qdrant (vector DB)' = 'qdrant'
        'TEI (embeddings)' = 'embeddings'; 'Hermes auth proxy' = 'hermes-proxy'; 'APE (agent policy engine)' = 'ape'
        'ComfyUI (image generation)' = 'comfyui'; 'Perplexica (deep research)' = 'perplexica'; 'Privacy Shield' = 'privacy-shield'
    }
    $publishedPorts = $null
    foreach ($service in $PortsToCheck.Keys) {
        $port = [int]$PortsToCheck[$service]
        $result = Test-WindowsPortInUse -Port $port
        if (-not $result.InUse) { continue }
        if ($NativeLlmService -and $service -eq $NativeLlmService -and
            (Test-WindowsODSNativeLlmOwnsPort -PortResult $result -InstallDir $InstallDir -Port $port)) {
            Write-AI "  Port $port is held by this installation's model runtime; setup replaces it."
            continue
        }
        if ($InstallDir -and $composeServices.ContainsKey([string]$service)) {
            if ($null -eq $publishedPorts) { $publishedPorts = @(Get-WindowsDockerPublishedPorts) }
            if (Test-WindowsODSComposeOwnsPort -PortResult $result -InstallDir $InstallDir -Port $port `
                    -ComposeService $composeServices[[string]$service] -PublishedPorts $publishedPorts) {
                Write-AI "  Port $port is held by this installation's Compose service; setup updates it."
                continue
            }
        }
        $conflicts += "  Port $port ($service) in use by: $($result.ProcessName) (PID $($result.ProcessId))"
    }
    return $conflicts
}

function Assert-WindowsODSSelectedPortAvailability {
    param(
        [string[]]$Conflicts = @(),
        [switch]$NonInteractive,
        [switch]$Force,
        [switch]$DryRun
    )

    if ($Conflicts.Count -eq 0) {
        Write-AISuccess "No port conflicts detected"
        return $true
    }
    Write-AIWarn "Port conflicts detected:"
    $Conflicts | ForEach-Object { Write-Host $_ -ForegroundColor Yellow }
    Write-AI "  Stop the conflicting processes, or override ports via environment variables."
    Write-AI '  Example: $env:WEBUI_PORT = "9090" before running the installer.'
    Write-AI "  See .env.example for all configurable ports."
    if ($NonInteractive -and -not $Force -and -not $DryRun) {
        Write-AIError "Non-interactive install cannot continue with occupied service ports."
        throw "ODS_INSTALL_ABORTED"
    }
    return $false
}

# ── Tier-specific RAM requirements ────────────────────────────────────────────
$_minRamGB = switch ($selectedTier) {
    "NV_ULTRA"   { 96 }
    "SH_LARGE"   { 96 }
    "SH_COMPACT" { 64 }
    "4"          { 64 }
    "3"          { 48 }
    "2"          { 32 }
    "1"          { 16 }
    "0"          {  4 }
    "CLOUD"      {  4 }
    default      { 16 }
}

# Hard floor: Docker Desktop + WSL2 + containers need at least 8 GB to function
if ($systemRamGB -lt 8) {
    Write-AIError "RAM: ${systemRamGB} GB detected. ODS requires at least 8 GB."
    Write-AIError "Docker Desktop + WSL2 + services need more memory than is available."
    Write-AI "  With ${systemRamGB} GB, Docker alone consumes most available RAM."
    $requirementsMet = $false
} elseif ($systemRamGB -lt $_minRamGB) {
    Write-AIWarn "RAM: ${systemRamGB} GB available, ${_minRamGB} GB recommended for Tier $selectedTier."
    Write-AI "  Performance may be limited. Consider a lower tier with: --Tier <N>"
    # Tier-specific RAM is a warning, not a hard blocker -- users may have trimmed WSL2 memory
} else {
    Write-AISuccess "RAM: ${systemRamGB} GB OK (>= ${_minRamGB} GB for Tier $selectedTier)"
}

# ── Tier-specific disk requirements ──────────────────────────────────────────
# These account for model file + Docker image layers + data volumes.
$_minDiskGB = switch ($selectedTier) {
    "NV_ULTRA"   { 100 }
    "SH_LARGE"   { 100 }
    "SH_COMPACT" {  50 }
    "4"          {  50 }
    "3"          {  35 }
    "2"          {  30 }
    "1"          {  25 }
    "0"          {  15 }
    "CLOUD"      {  10 }
    default      {  30 }
}

$_diskCheck = Test-DiskSpace -Path $installDir -RequiredGB $_minDiskGB
if (-not $_diskCheck.Sufficient) {
    Write-AIWarn "Disk: $($_diskCheck.FreeGB) GB free, ${_minDiskGB} GB required for Tier $selectedTier."
    Write-AI "  Install target checked: $installDir"
    $_installDirHint = "<path-with-enough-space>\ods"
    if ($sourceRoot -match "^([A-Za-z]):") {
        $_installDirHint = "$($Matches[1].ToUpperInvariant()):\ods"
    }
    Write-AI "  To use a different drive, rerun from the source checkout with:"
    Write-AI "  .\ods\installers\windows\install-windows.ps1 -InstallDir $_installDirHint"
    $requirementsMet = $false
} else {
    Write-AISuccess "Disk: $($_diskCheck.FreeGB) GB free OK (>= ${_minDiskGB} GB for Tier $selectedTier)"
}

# ── GPU requirement check ─────────────────────────────────────────────────────
if ($selectedTier -notin @("0", "CLOUD") -and $gpuInfo.Backend -eq "none") {
    Write-AIWarn "Tier $selectedTier normally requires a GPU but none was detected."
    Write-AI "  Inference will fall back to CPU (very slow for larger models)."
    Write-AI "  Consider --Cloud for API mode, or --Tier 0 for CPU-optimized inference."
}

# On AMD the model runs natively on Windows (llama-server.exe). Its port may
# already be held by this installation's own runtime, which phase 8 replaces;
# any other program there is a conflict. Nothing is stopped here.
$_usesNativeLlm = ($gpuInfo.Backend -eq "amd" -and -not $cloudMode)

# ── Port conflict detection ───────────────────────────────────────────────────
# Build list of ports to check based on enabled features.
# Resolve the same process override / retained .env / default as phase 06.
# The remote Dashboard listener is required even on loopback-only installs.
$_portsToCheck = [ordered]@{
    "Open WebUI (chat)"   = Resolve-WindowsODSPort `
        -Name "WEBUI_PORT" -DefaultPort 3000 -InstallDir $installDir
    "Dashboard"           = Resolve-WindowsODSPort `
        -Name "DASHBOARD_PORT" -RequireValidProcessOverride -DefaultPort 3001 -InstallDir $installDir
    "Dashboard (remote)"  = Resolve-WindowsODSPort `
        -Name "DASHBOARD_REMOTE_PORT" -RequireValidProcessOverride -DefaultPort 3011 -InstallDir $installDir
    "Dashboard API"       = Resolve-WindowsODSPort `
        -Name "DASHBOARD_API_PORT" -RequireValidProcessOverride -DefaultPort 3002 -InstallDir $installDir
}
$_llmPortToCheck = Resolve-WindowsLlmPreflightPort `
    -GpuBackend ([string]$gpuInfo.Backend) `
    -CloudMode:$cloudMode `
    -NativeDefaultPort ([int]$script:NATIVE_LLM_PORT) `
    -InstallDir $installDir
$_llmServiceLabel = "llama-server (LLM)"
if ($_llmPortToCheck -gt 0) {
    $_portsToCheck[$_llmServiceLabel] = $_llmPortToCheck
}
if ($enableRecommended) {
    $_portsToCheck["LiteLLM (API gateway)"] = Resolve-WindowsODSPort `
        -Name "LITELLM_PORT" -RequireValidProcessOverride -DefaultPort 4000 -InstallDir $installDir
    $_portsToCheck["SearXNG (search)"] = Resolve-WindowsODSPort `
        -Name "SEARXNG_PORT" -RequireValidProcessOverride -DefaultPort 8888 -InstallDir $installDir
    $_portsToCheck["Token Spy (usage monitor)"] = Resolve-WindowsODSPort `
        -Name "TOKEN_SPY_PORT" -RequireValidProcessOverride -DefaultPort 3005 -InstallDir $installDir
}
if ($enableVoice) {
    # Preflight the exact host port phase 06 / New-ODSEnv will write: honor the
    # process-level and persisted WHISPER_PORT override, then apply the same
    # Lemonade-router conflict move as Resolve-WindowsWhisperHostPort.
    $_whisperConfiguredPort = Resolve-WindowsODSPort `
        -Name "WHISPER_PORT" -DefaultPort 9000 -InstallDir $installDir
    $_whisperPortToCheck = [int](Resolve-WindowsWhisperHostPort `
        -ConfiguredPort ([string]$_whisperConfiguredPort))
    $_portsToCheck["Whisper (STT)"] = $_whisperPortToCheck
    $_portsToCheck["Kokoro (TTS)"]  = Resolve-WindowsODSPort `
        -Name "TTS_PORT" -RequireValidProcessOverride -DefaultPort 8880 -InstallDir $installDir
}
if ($enableWorkflows) {
    $_portsToCheck["n8n (workflows)"] = Resolve-WindowsODSPort `
        -Name "N8N_PORT" -RequireValidProcessOverride -DefaultPort 5678 -InstallDir $installDir
}
if ($enableRag) {
    $_portsToCheck["Qdrant (gRPC)"] = Resolve-WindowsODSPort `
        -Name "QDRANT_GRPC_PORT" -RequireValidProcessOverride -DefaultPort 6334 -InstallDir $installDir
    $_portsToCheck["Qdrant (vector DB)"] = Resolve-WindowsODSPort `
        -Name "QDRANT_PORT" -RequireValidProcessOverride -DefaultPort 6333 -InstallDir $installDir
    $_portsToCheck["TEI (embeddings)"] = Resolve-WindowsODSPort `
        -Name "EMBEDDINGS_PORT" -RequireValidProcessOverride -DefaultPort 8090 -InstallDir $installDir
}
if ($enableHermes) {
    $_portsToCheck["Hermes auth proxy"] = Resolve-WindowsODSPort `
        -Name "HERMES_PROXY_PORT" -RequireValidProcessOverride -DefaultPort 9120 -InstallDir $installDir
}
if ($enableHermes) {
    $_portsToCheck["APE (agent policy engine)"] = Resolve-WindowsODSPort `
        -Name "APE_PORT" -RequireValidProcessOverride -DefaultPort 7890 -InstallDir $installDir
}
if ($enableComfyui) {
    $_portsToCheck["ComfyUI (image generation)"] = Resolve-WindowsODSPort `
        -Name "COMFYUI_PORT" -RequireValidProcessOverride -DefaultPort 8188 -InstallDir $installDir
}
if ($enableDeepResearch) {
    $_portsToCheck["Perplexica (deep research)"] = Resolve-WindowsODSPort `
        -Name "PERPLEXICA_PORT" -RequireValidProcessOverride -DefaultPort 3004 -InstallDir $installDir
}
if ($enablePrivacyShield) {
    $_portsToCheck["Privacy Shield"] = Resolve-WindowsODSPort `
        -Name "SHIELD_PORT" -RequireValidProcessOverride -DefaultPort 8085 -InstallDir $installDir
}

$_portConflicts = @(Get-WindowsODSSelectedPortConflicts `
    -PortsToCheck $_portsToCheck `
    -NativeLlmService $(if ($_usesNativeLlm) { $_llmServiceLabel } else { "" }) `
    -InstallDir $installDir)
if (-not (Assert-WindowsODSSelectedPortAvailability `
    -Conflicts $_portConflicts -NonInteractive:$nonInteractive `
    -Force:$force -DryRun:$dryRun)) {
    $requirementsMet = $false
}

# ── Requirements gate ─────────────────────────────────────────────────────────
if (-not $requirementsMet) {
    Write-Host ""
    Write-AIWarn "Some requirements are not fully met (see warnings above)."
    if ($dryRun) {
        Write-AI "[DRY RUN] Would prompt to continue despite unmet requirements"
    } elseif ($nonInteractive -or $force) {
        Write-AIWarn "Continuing despite unmet requirements (--Force / --NonInteractive)."
    } else {
        $continueChoice = Read-Host "  Continue anyway? [y/N]"
        if ($continueChoice -notmatch "^[yY]") {
            Write-AI "Resolve the issues above and re-run the installer."
            throw "ODS_INSTALL_ABORTED"
        }
    }
} else {
    Write-AISuccess "All requirements met"
}
