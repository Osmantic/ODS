# ============================================================================
# Dream Server Windows Installer -- Phase 04: Requirements Check
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
    Write-AIError "RAM: ${systemRamGB} GB detected. Dream Server requires at least 8 GB."
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

# ── Port conflict detection ───────────────────────────────────────────────────
# Build list of ports to check based on enabled features.
# Default service ports match .env.example; overridden ports are not checked here.
$_portsToCheck = [ordered]@{
    "llama-server (LLM)"  = 11434
    "Open WebUI (chat)"   = 3000
    "Dashboard"           = 3001
    "Dashboard API"       = 3002
}
if ($enableRecommended) {
    $_portsToCheck["LiteLLM (API gateway)"] = 4000
    $_portsToCheck["SearXNG (search)"] = 8888
    $_portsToCheck["Token Spy (usage monitor)"] = 3005
}
if ($enableVoice) {
    $_portsToCheck["Whisper (STT)"] = 9000
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
foreach ($svc in $_portsToCheck.Keys) {
    $port   = $_portsToCheck[$svc]
    $result = Test-WindowsPortInUse -Port $port
    if ($result.InUse) {
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
            exit 1
        }
    }
} else {
    Write-AISuccess "All requirements met"
}
