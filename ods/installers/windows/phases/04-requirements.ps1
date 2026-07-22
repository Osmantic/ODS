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
        [int]$LemonadeDefaultPort = 8080,
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
        $defaultPort = $LemonadeDefaultPort
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

function Get-WindowsODSLemonadeProcesses {
    <#
    .SYNOPSIS
        Return native Lemonade processes that can reserve ODS host ports.
    #>
    $knownNames = @("LemonadeServer.exe", "lemonade-server.exe", "lemonade-router.exe")
    try {
        return @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            ($knownNames -contains $_.Name) -or
            ($_.ExecutablePath -and (
                $_.ExecutablePath -match '\\lemonade_server\\bin\\' -or
                $_.ExecutablePath -match '\\Lemonade Server\\bin\\' -or
                $_.ExecutablePath -match '\\\.cache\\lemonade\\bin\\'
            ))
        } | Select-Object ProcessId, Name, ExecutablePath, CommandLine)
    } catch {
        return @()
    }
}

function Test-WindowsODSLemonadeOwnsPort {
    <#
    .SYNOPSIS
        Return true when a listening port belongs to a known Lemonade process.
    #>
    param(
        [hashtable]$PortResult,
        [object[]]$LemonadeProcesses = @()
    )

    if (-not $PortResult -or -not $PortResult.InUse -or [int]$PortResult.ProcessId -le 0) {
        return $false
    }

    $listenerPid = [int]$PortResult.ProcessId
    return [bool]@($LemonadeProcesses | Where-Object {
        $_.ProcessId -and [int]$_.ProcessId -eq $listenerPid
    }).Count
}

function Stop-WindowsODSLemonadePortConflicts {
    <#
    .SYNOPSIS
        Stop native Lemonade when this install is not using Lemonade inference.
    #>
    param(
        [switch]$UseNativeLemonade,
        [switch]$NonInteractive,
        [switch]$Force
    )

    if ($UseNativeLemonade) { return }

    $_lemonadeProcesses = @(Get-WindowsODSLemonadeProcesses)
    if ($_lemonadeProcesses.Count -eq 0) { return }

    $_pidList = ($_lemonadeProcesses | ForEach-Object { "$($_.Name) PID $($_.ProcessId)" }) -join ", "
    Write-AIWarn "Native Lemonade is running but this install uses Docker-backed inference."
    Write-AI "  Lemonade can reserve localhost ports used by ODS services, including Whisper STT."
    Write-AI "  Detected: $_pidList"

    $_shouldStop = $true
    if (-not $NonInteractive -and -not $Force) {
        $_choice = Read-Host "  Stop native Lemonade for this ODS session? [Y/n]"
        $_shouldStop = ($_choice -notmatch "^[nN]")
    }

    if (-not $_shouldStop) {
        Write-AIWarn "Native Lemonade left running. Docker service readiness may fail on localhost ports."
        return
    }

    foreach ($_proc in $_lemonadeProcesses) {
        if ($_proc.ProcessId -gt 0) {
            Stop-Process -Id ([int]$_proc.ProcessId) -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Seconds 2

    $_remaining = @(Get-WindowsODSLemonadeProcesses)
    if ($_remaining.Count -gt 0) {
        Write-AIWarn "Could not fully stop native Lemonade. Port conflicts may remain."
    } else {
        Write-AISuccess "Native Lemonade stopped for Docker-backed install"
    }
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
    Write-AI "  .\install.ps1 -InstallDir $_installDirHint"
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

# Native Lemonade legitimately belongs to Windows AMD/Lemonade installs. On
# Docker-backed NVIDIA/CPU installs it can shadow localhost ports such as 9000
# and make healthy Docker services look dead from the Windows host.
$_usesNativeLemonade = ($gpuInfo.Backend -eq "amd" -and -not $cloudMode)
Stop-WindowsODSLemonadePortConflicts `
    -UseNativeLemonade:$_usesNativeLemonade `
    -NonInteractive:$nonInteractive `
    -Force:$force

# ── Port conflict detection ───────────────────────────────────────────────────
# Build list of ports to check based on enabled features.
# Default service ports match .env.example; overridden ports are not checked here.
$_portsToCheck = [ordered]@{
    "Open WebUI (chat)"   = 3000
    "Dashboard"           = 3001
    "Dashboard API"       = 3002
}
$_llmPortToCheck = Resolve-WindowsLlmPreflightPort `
    -GpuBackend ([string]$gpuInfo.Backend) `
    -CloudMode:$cloudMode `
    -LemonadeDefaultPort ([int]$script:LEMONADE_PORT) `
    -InstallDir $installDir
if ($_llmPortToCheck -gt 0) {
    $_llmServiceLabel = $(if ($gpuInfo.Backend -eq "amd") { "Lemonade (LLM)" } else { "llama-server (LLM)" })
    $_portsToCheck[$_llmServiceLabel] = $_llmPortToCheck
}
if ($enableRecommended) {
    $_portsToCheck["LiteLLM (API gateway)"] = 4000
    $_portsToCheck["SearXNG (search)"] = 8888
    $_portsToCheck["Token Spy (usage monitor)"] = 3005
}
if ($enableVoice) {
    $_whisperPortToCheck = $(if ($gpuInfo.Backend -eq "amd" -and -not $cloudMode) { 9100 } else { 9000 })
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
if ($enableOpenClaw) {
    $_portsToCheck["OpenClaw (agents)"] = 7860
}
if ($enableHermes -or $enableOpenClaw) {
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

$_portConflicts = @()
$_managedLemonadeProcesses = @()
if ($_usesNativeLemonade) {
    $_managedLemonadeProcesses = @(Get-WindowsODSLemonadeProcesses)
}
foreach ($svc in $_portsToCheck.Keys) {
    $port   = $_portsToCheck[$svc]
    $result = Test-WindowsPortInUse -Port $port
    if ($result.InUse) {
        if ($svc -eq "Lemonade (LLM)" -and
            (Test-WindowsODSLemonadeOwnsPort `
                -PortResult $result `
                -LemonadeProcesses $_managedLemonadeProcesses)) {
            Write-AI "  Port $port is already owned by the managed Lemonade runtime; it will be reused."
            continue
        }
        $_portConflicts += "  Port $port ($svc) in use by: $($result.ProcessName) (PID $($result.ProcessId))"
        $requirementsMet = $false
    }
}

if ($_portConflicts.Count -gt 0) {
    Write-AIWarn "Port conflicts detected:"
    $_portConflicts | ForEach-Object { Write-Host $_ -ForegroundColor Yellow }
    Write-AI "  Stop the conflicting processes, or override ports via environment variables."
    Write-AI "  Example: set WEBUI_PORT=9090 before running the installer."
    Write-AI "  See .env.example for all configurable ports."
} else {
    Write-AISuccess "No port conflicts detected"
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
