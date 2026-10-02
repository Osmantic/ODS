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
        @{ InUse; ProcessName; ProcessId; Listeners }
    #>
    param([int]$Port)

    # Get-NetTCPConnection is available on Windows 8+ / Server 2012+
    try {
        $connections = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
        if ($connections.Count) {
            $listeners = @($connections | ForEach-Object {
                [pscustomobject]@{
                    LocalAddress = [string]$_.LocalAddress
                    LocalPort = [int]$_.LocalPort
                    ProcessId = [int]$_.OwningProcess
                }
            })
            $proc = Get-Process -Id $listeners[0].ProcessId -ErrorAction SilentlyContinue
            return @{
                InUse       = $true
                ProcessName = $(if ($proc) { $proc.ProcessName } else { "unknown" })
                ProcessId   = $listeners[0].ProcessId
                Listeners   = $listeners
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
                    # The legacy fallback does not enumerate every local bind.
                    # It can report a conflict but cannot prove reuse is safe.
                    Listeners   = @()
                }
            }
        } catch { }
    }

    return @{ InUse = $false; ProcessName = ""; ProcessId = 0; Listeners = @() }
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

    $listeners = @($PortResult.Listeners)
    if (-not $listeners.Count) { return $false }
    foreach ($listener in $listeners) {
        $listenerPid = [int]$listener.ProcessId
        if (-not $listenerPid -or -not @($LemonadeProcesses | Where-Object {
            $_.ProcessId -and [int]$_.ProcessId -eq $listenerPid
        }).Count) { return $false }
    }
    return $true
}

function Get-WindowsODSExpectedComposeService {
    param([string]$ServiceLabel)
    $services = @{
        'Open WebUI (chat)' = 'open-webui'
        'Dashboard' = 'dashboard'
        'Dashboard API' = 'dashboard-api'
        'llama-server (LLM)' = 'llama-server'
        'LiteLLM (API gateway)' = 'litellm'
        'SearXNG (search)' = 'searxng'
        'Token Spy (usage monitor)' = 'token-spy'
        'Whisper (STT)' = 'whisper'
        'Kokoro (TTS)' = 'tts'
        'n8n (workflows)' = 'n8n'
        'Qdrant (vector DB)' = 'qdrant'
        'TEI (embeddings)' = 'embeddings'
        'Hermes auth proxy' = 'hermes-proxy'
        'OpenClaw (agents)' = 'openclaw'
        'APE (agent policy engine)' = 'ape'
        'Perplexica (deep research)' = 'perplexica'
        'Privacy Shield' = 'privacy-shield'
    }
    return $services[$ServiceLabel]
}

function Get-WindowsODSComposePortBindings {
    param([string]$InstallDir)

    $unknown = @{ Verified = $false; Bindings = @(); ExpectedRoot = '' }
    if ([string]::IsNullOrWhiteSpace($InstallDir) -or
        -not [IO.Path]::IsPathRooted($InstallDir) -or
        -not (Get-Command docker -ErrorAction SilentlyContinue)) {
        return $unknown
    }
    try {
        $expected = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\', '/')
        # Inspect every running container. A second project may temporarily
        # advertise the same endpoint during a Docker Desktop port transfer.
        $ids = @(& docker ps --format '{{.ID}}' 2>$null)
        if ($LASTEXITCODE -ne 0) { return $unknown }
        $bindings = @()
        foreach ($id in $ids) {
            if ([string]::IsNullOrWhiteSpace([string]$id)) { continue }
            $raw = @(& docker container inspect ([string]$id) 2>$null)
            if ($LASTEXITCODE -ne 0 -or -not $raw.Count) { return $unknown }
            $items = @(($raw -join "`n") | ConvertFrom-Json -ErrorAction Stop)
            if ($items.Count -ne 1) { return $unknown }
            $container = $items[0]
            if (-not $container.State.Running -or
                [string]::IsNullOrWhiteSpace([string]$container.Id)) {
                return $unknown
            }
            $labels = $container.Config.Labels
            $workingDir = [string]$labels.'com.docker.compose.project.working_dir'
            $actual = ''
            if (-not [string]::IsNullOrWhiteSpace($workingDir) -and
                [IO.Path]::IsPathRooted($workingDir)) {
                $actual = [IO.Path]::GetFullPath($workingDir).TrimEnd('\', '/')
            }
            if (-not $container.NetworkSettings.Ports) { continue }
            foreach ($publishedPort in $container.NetworkSettings.Ports.PSObject.Properties) {
                if ($publishedPort.Name -notmatch '/tcp$') { continue }
                foreach ($entry in @($publishedPort.Value)) {
                    if (-not $entry) { continue }
                    $hostPort = 0
                    if (-not [int]::TryParse([string]$entry.HostPort, [ref]$hostPort) -or
                        $hostPort -lt 1 -or $hostPort -gt 65535 -or
                        [string]::IsNullOrWhiteSpace([string]$entry.HostIp)) {
                        return $unknown
                    }
                    $bindings += [pscustomobject]@{
                        ContainerId = [string]$container.Id
                        Project = [string]$labels.'com.docker.compose.project'
                        WorkingDir = $actual
                        Service = [string]$labels.'com.docker.compose.service'
                        HostIp = [string]$entry.HostIp
                        HostPort = $hostPort
                    }
                }
            }
        }
        return @{ Verified = $true; Bindings = @($bindings); ExpectedRoot = $expected }
    } catch {
        # Missing, changing, or malformed Docker metadata is never authority to
        # bypass the ordinary occupied-port gate.
        return $unknown
    }
}

function Test-WindowsODSDockerBrokerListener {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return $false }
    try {
        $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        $programFiles = [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles)
        if (-not $process -or [string]::IsNullOrWhiteSpace($programFiles) -or
            -not [string]::Equals([string]$process.ProcessName,
                'com.docker.backend', [StringComparison]::OrdinalIgnoreCase) -or
            -not [IO.Path]::IsPathRooted([string]$process.Path)) { return $false }
        $expected = [IO.Path]::GetFullPath((Join-Path $programFiles `
            'Docker\Docker\resources\com.docker.backend.exe'))
        $actual = [IO.Path]::GetFullPath([string]$process.Path)
        return [string]::Equals($actual, $expected,
            [StringComparison]::OrdinalIgnoreCase)
    } catch {
        return $false
    }
}

function Test-WindowsODSComposeOwnsListeners {
    param(
        [hashtable]$PortResult,
        [int]$Port,
        [string]$Service,
        [hashtable]$Ownership
    )
    if (-not $PortResult.InUse -or -not $Ownership.Verified -or
        [string]::IsNullOrWhiteSpace($Service)) { return $false }
    $listeners = @($PortResult.Listeners)
    if (-not $listeners.Count) { return $false }
    $seen = @{}
    foreach ($listener in $listeners) {
        $address = [string]$listener.LocalAddress
        if ([string]::IsNullOrWhiteSpace($address) -or
            [int]$listener.LocalPort -ne $Port -or
            -not (Test-WindowsODSDockerBrokerListener -ProcessId ([int]$listener.ProcessId)) -or
            $seen.ContainsKey($address)) { return $false }
        $seen[$address] = $true
        $bindingMatches = @($Ownership.Bindings | Where-Object {
            $_.HostPort -eq $Port -and $_.HostIp -eq $address
        })
        if ($bindingMatches.Count -ne 1) { return $false }
        $binding = $bindingMatches[0]
        if ($binding.Project -ne 'ods' -or $binding.Service -ne $Service -or
            -not [string]::Equals([string]$binding.WorkingDir,
                [string]$Ownership.ExpectedRoot,
                [StringComparison]::OrdinalIgnoreCase)) { return $false }
    }
    return $true
}

function Get-WindowsODSSelectedPortConflicts {
    param(
        [System.Collections.IDictionary]$PortsToCheck,
        [switch]$UsesNativeLemonade,
        [string]$InstallDir = ''
    )

    $managedLemonadeProcesses = @()
    if ($UsesNativeLemonade) {
        $managedLemonadeProcesses = @(Get-WindowsODSLemonadeProcesses)
    }
    $conflicts = @()
    $ownership = $null
    foreach ($service in $PortsToCheck.Keys) {
        $port = [int]$PortsToCheck[$service]
        $result = Test-WindowsPortInUse -Port $port
        if (-not $result.InUse) { continue }
        if ($service -eq "Lemonade (LLM)" -and
            (Test-WindowsODSLemonadeOwnsPort `
                -PortResult $result `
                -LemonadeProcesses $managedLemonadeProcesses)) {
            Write-AI "  Port $port is already owned by the managed Lemonade runtime; it will be reused."
            continue
        }
        $composeService = Get-WindowsODSExpectedComposeService -ServiceLabel $service
        if ($composeService -and $InstallDir) {
            if ($null -eq $ownership) {
                $ownership = Get-WindowsODSComposePortBindings -InstallDir $InstallDir
            }
            if (Test-WindowsODSComposeOwnsListeners -PortResult $result -Port $port `
                -Service $composeService -Ownership $ownership) {
                Write-AI "  Port $port belongs to this ODS installation's $composeService container; continuing."
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

# Native Lemonade legitimately belongs to Windows AMD/Lemonade installs. Other
# Lemonade processes may belong to unrelated products or users; never stop them
# as an installer preflight side effect. Check selected ports below instead.
$_usesNativeLemonade = ($gpuInfo.Backend -eq "amd" -and -not $cloudMode)

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
    # Preflight the exact host port phase 06 / New-ODSEnv will write: honor the
    # process-level and persisted WHISPER_PORT override, then apply the same
    # managed-AMD / Lemonade-conflict migration as Resolve-WindowsWhisperHostPort.
    $_whisperConfiguredPort = Resolve-WindowsODSPort `
        -Name "WHISPER_PORT" -DefaultPort 9000 -InstallDir $installDir
    $_whisperPortToCheck = [int](Resolve-WindowsWhisperHostPort `
        -ConfiguredPort ([string]$_whisperConfiguredPort) `
        -GpuBackend ([string]$gpuInfo.Backend) `
        -AmdInferenceRuntime $(if ($_usesNativeLemonade) { "lemonade" } else { "" }) `
        -AmdInferenceLocation $(if ($_usesNativeLemonade) { "host" } else { "" }))
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

$_portConflicts = @(Get-WindowsODSSelectedPortConflicts `
    -PortsToCheck $_portsToCheck -UsesNativeLemonade:$_usesNativeLemonade `
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
