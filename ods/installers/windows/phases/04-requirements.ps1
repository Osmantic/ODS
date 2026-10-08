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

function Get-WindowsODSSelectedPortConflicts {
    param(
        [System.Collections.IDictionary]$PortsToCheck,
        [string]$NativeLlmService = "",
        [string]$InstallDir = ""
    )

    $conflicts = @()
    foreach ($service in $PortsToCheck.Keys) {
        $port = [int]$PortsToCheck[$service]
        $result = Test-WindowsPortInUse -Port $port
        if (-not $result.InUse) { continue }
        if ($NativeLlmService -and $service -eq $NativeLlmService -and
            (Test-WindowsODSNativeLlmOwnsPort -PortResult $result -InstallDir $InstallDir -Port $port)) {
            Write-AI "  Port $port is held by this installation's model runtime; setup replaces it."
            continue
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
# Default service ports match .env.example. Open WebUI uses the same persisted
# or process-level override that phase 06 will write to .env.
$_portsToCheck = [ordered]@{
    "Open WebUI (chat)"   = Resolve-WindowsODSPort `
        -Name "WEBUI_PORT" -DefaultPort 3000 -InstallDir $installDir
    "Dashboard"           = 3001
    "Dashboard API"       = 3002
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
    $_portsToCheck["LiteLLM (API gateway)"] = 4000
    $_portsToCheck["SearXNG (search)"] = 8888
    $_portsToCheck["Token Spy (usage monitor)"] = 3005
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
    $_portsToCheck["Kokoro (TTS)"]  = 8880
}
if ($enableWorkflows) {
    $_portsToCheck["n8n (workflows)"] = 5678
}
if ($enableRag) {
    $_portsToCheck["Qdrant (vector DB)"] = 6333
    $_portsToCheck["TEI (embeddings)"] = 8090
}
if ($enableHermes) {
    $_portsToCheck["Hermes auth proxy"] = 9120
}
if ($enableHermes) {
    $_portsToCheck["APE (agent policy engine)"] = 7890
}
if ($enableComfyui) {
    $_portsToCheck["ComfyUI (image generation)"] = 8188
}
if ($enableDeepResearch) {
    $_portsToCheck["Perplexica (deep research)"] = 3004
}
if ($enablePrivacyShield) {
    $_portsToCheck["Privacy Shield"] = 8085
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
