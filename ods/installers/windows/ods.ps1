# ============================================================================
# ODS Windows CLI -- ods.ps1
# ============================================================================
# Day-to-day management of a ODS installation on Windows.
# Mirrors the Linux ods-cli command structure.
#
# Usage:
#   .\ods.ps1 status              # Health checks + GPU status
#   .\ods.ps1 start [service]     # Start all or one service
#   .\ods.ps1 stop [service]      # Stop all or one service
#   .\ods.ps1 restart [service]   # Restart all or one service
#   .\ods.ps1 logs <service> [N]  # Tail logs (default 100 lines)
#   .\ods.ps1 config show         # View .env (secrets masked)
#   .\ods.ps1 config edit         # Open .env in notepad
#   .\ods.ps1 chat "message"      # Quick chat via API
#   .\ods.ps1 update              # Pull latest images and restart
#   .\ods.ps1 doctor              # Diagnose runtime readiness
#   .\ods.ps1 repair voice        # Repair voice/STT/TTS readiness
#   .\ods.ps1 enable <service>    # Enable an extension service
#   .\ods.ps1 disable <service>   # Disable an extension service
#   .\ods.ps1 report              # Generate Windows diagnostics bundle
#   .\ods.ps1 version             # Show version
#   .\ods.ps1 help                # Show help
#
# ============================================================================

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Command = "help",

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = "Stop"

# ── Locate libraries ──
# NOTE: Nested Join-Path required -- PS 5.1 only accepts 2 arguments
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LibDir = Join-Path $ScriptDir "lib"
. (Join-Path $LibDir "constants.ps1")
. (Join-Path $LibDir "ui.ps1")
. (Join-Path $LibDir "compose-diagnostics.ps1")
. (Join-Path $LibDir "backend-contract.ps1")
. (Join-Path $LibDir "detection.ps1")
. (Join-Path $LibDir "llm-endpoint.ps1")
. (Join-Path $LibDir "install-report.ps1")

$_resolvedLemonadeExe = Resolve-ODSLemonadeExe
if ($_resolvedLemonadeExe) { $script:LEMONADE_EXE = $_resolvedLemonadeExe }
$script:LEMONADE_TASK_NAME = "ODSLemonadeRuntime"

# ── Resolve install directory ──
$InstallDir = $script:ODS_INSTALL_DIR

# ============================================================================
# Helpers
# ============================================================================

function Test-DockerRunning {
    <#
    .SYNOPSIS
        Quick check if Docker daemon is responsive. Shows friendly message if not.
    #>
    $null = docker info 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-AIError "Docker Desktop is not running."
        Write-AI "Start it from the Start Menu, then try again."
        return $false
    }
    return $true
}

function Test-Install {
    if (-not (Test-Path $InstallDir)) {
        Write-AIError "ODS not found at $InstallDir. Set ODS_HOME or run installer first."
        exit 1
    }
    $baseCompose = Join-Path $InstallDir "docker-compose.base.yml"
    $monoCompose = Join-Path $InstallDir "docker-compose.yml"
    if (-not (Test-Path $baseCompose) -and -not (Test-Path $monoCompose)) {
        Write-AIError "docker-compose.base.yml not found in $InstallDir"
        exit 1
    }
    if (-not (Test-DockerRunning)) { exit 1 }
}

function Get-ComposeFlags {
    <#
    .SYNOPSIS
        Read saved compose flags from installer, or build default flags.
    #>
    $flagsFile = Join-Path $InstallDir ".compose-flags"
    if (Test-Path $flagsFile) {
        $raw = (Get-Content $flagsFile -Raw).Trim()
        return ($raw -split "\s+")
    }

    $launchRecord = Join-Path (Join-Path $InstallDir "logs") "compose-launch.txt"
    if (Test-Path $launchRecord) {
        $composeFlagsLine = Get-Content $launchRecord -ErrorAction SilentlyContinue |
            Where-Object { $_ -match "^compose_flags=" } |
            Select-Object -First 1
        if ($composeFlagsLine) {
            $raw = ($composeFlagsLine -replace "^compose_flags=", "").Trim()
            if (-not [string]::IsNullOrWhiteSpace($raw)) {
                Write-AIWarn ".compose-flags is missing; using compose flags from logs\compose-launch.txt"
                return ($raw -split "\s+")
            }
        }
    }

    # Fallback: detect from available files
    # --env-file explicit: Docker Compose V2 on Windows may not auto-discover
    # .env from the project directory when multiple -f flags are used.
    $flags = @("--env-file", ".env")
    $base = Join-Path $InstallDir "docker-compose.base.yml"
    $nvidia = Join-Path $InstallDir "docker-compose.nvidia.yml"
    $mono = Join-Path $InstallDir "docker-compose.yml"

    if (Test-Path $base) {
        $flags += @("-f", "docker-compose.base.yml")
        if (Test-Path $nvidia) {
            $flags += @("-f", "docker-compose.nvidia.yml")
        }
    } elseif (Test-Path $mono) {
        $flags += @("-f", "docker-compose.yml")
    }

    # Add enabled extension compose files
    $extDir = Join-Path (Join-Path $InstallDir "extensions") "services"
    if (Test-Path $extDir) {
        Get-ChildItem -Path $extDir -Directory | ForEach-Object {
            $composePath = Join-Path $_.FullName "compose.yaml"
            if (Test-Path $composePath) {
                $relPath = $composePath.Substring($InstallDir.Length + 1) -replace "\\", "/"
                $flags += @("-f", $relPath)
            }
        }
    }

    return $flags
}

function Read-ODSEnv {
    <#
    .SYNOPSIS
        Safely load .env file into a hashtable (no eval, no injection).
    #>
    return Get-WindowsODSEnvMap -InstallDir $InstallDir
}

function Get-ODSEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$Default = ""
    )
    try {
        $envMap = Read-ODSEnv
        if ($envMap.ContainsKey($Name) -and -not [string]::IsNullOrWhiteSpace($envMap[$Name])) {
            return $envMap[$Name]
        }
    } catch { }
    return $Default
}

function Sync-ODSNativeInferenceConfig {
    <#
    .SYNOPSIS
        Align native inference runtime constants with the installed .env.
    #>
    try {
        $envMap = Read-ODSEnv
        $lemonadePort = $envMap["AMD_INFERENCE_PORT"]
        if (-not [string]::IsNullOrWhiteSpace($lemonadePort)) {
            $parsedPort = 0
            if ([int]::TryParse($lemonadePort, [ref]$parsedPort) -and $parsedPort -gt 0 -and $parsedPort -le 65535) {
                $script:LEMONADE_PORT = $parsedPort
                $script:LEMONADE_HEALTH_URL = "http://localhost:$($script:LEMONADE_PORT)/api/v1/health"
            }
        }
    } catch { }
}

function Invoke-HermesSoulRefresh {
    <#
    .SYNOPSIS
        Render data/persona/SOUL.md and optionally copy it into ods-hermes.
    #>
    param([switch]$SyncContainer)

    $builder = Join-Path (Join-Path $InstallDir "scripts") "build-installation-context.py"
    $template = Join-Path (Join-Path (Join-Path $InstallDir "extensions") "services\hermes") "SOUL.md.template"
    $envPath = Join-Path $InstallDir ".env"
    $output = Join-Path (Join-Path (Join-Path $InstallDir "data") "persona") "SOUL.md"
    $outputDir = Split-Path -Parent $output

    if (-not (Test-Path $template)) {
        Write-AIWarn "Hermes SOUL.md template not found; skipping persona refresh."
        return
    }

    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
    $rendered = $false
    $profileArgs = @()
    try {
        $envMap = Read-ODSEnv
        if ($envMap["LLM_BACKEND"] -eq "lemonade" -and $envMap["AMD_INFERENCE_RUNTIME"] -eq "lemonade") {
            $profileArgs = @("--profile", "local-lemonade")
        }
    } catch { }
    if (Test-Path $builder) {
        $pythonCandidates = @(
            @{ Command = "python"; Args = @() },
            @{ Command = "python3"; Args = @() },
            @{ Command = "py"; Args = @("-3") }
        )

        foreach ($candidate in $pythonCandidates) {
            $cmd = Get-Command $candidate.Command -ErrorAction SilentlyContinue
            if (-not $cmd -or -not $cmd.Source) { continue }
            try {
                & $cmd.Source @($candidate.Args) $builder "--template" $template "--env" $envPath "--output" $output @profileArgs *>> $script:ODS_LOG_FILE
                if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $output -PathType Leaf)) {
                    $rendered = $true
                    break
                }
            } catch {
                Add-Content -Path $script:ODS_LOG_FILE -Value "Hermes SOUL.md refresh failed with $($candidate.Command): $($_.Exception.Message)"
            }
        }
    }

    if (-not $rendered) {
        if (Test-Path -LiteralPath $output -PathType Container) {
            Remove-Item -LiteralPath $output -Recurse -Force
        }
        if (-not (Test-Path -LiteralPath $output -PathType Leaf)) {
            $content = Get-Content -LiteralPath $template -Raw
            $content = $content -replace "(?m)^\s*<!-- INSTALLATION_CONTEXT -->\s*\r?\n?", ""
            [System.IO.File]::WriteAllText($output, $content, (New-Object System.Text.UTF8Encoding($false)))
            Write-AIWarn "Generated fallback Hermes SOUL.md without dynamic installation context"
        }
    }

    if ($SyncContainer) {
        $names = & docker ps --format "{{.Names}}" 2>$null
        if ($names -contains "ods-hermes") {
            & docker exec ods-hermes cp /opt/hermes/docker/SOUL.md /opt/data/SOUL.md *>> $script:ODS_LOG_FILE
            if ($LASTEXITCODE -eq 0) {
                Write-AISuccess "Synced Hermes SOUL.md"
            } else {
                Write-AIWarn "Could not sync Hermes SOUL.md into running container"
            }
        }
    }
}

function Get-ODSVoiceDiagnosis {
    $whisperPort = Get-ODSEnvValue -Name "WHISPER_PORT" -Default "9000"
    $whisperUrl = "http://localhost:$whisperPort"
    $sttModel = Get-ODSEnvValue -Name "AUDIO_STT_MODEL" -Default "Systran/faster-whisper-base"
    $sttModelEncoded = $sttModel -replace "/", "%2F"
    $modelUrl = "$whisperUrl/v1/models/$sttModelEncoded"
    $ttsPort = Get-ODSEnvValue -Name "TTS_PORT" -Default "8880"
    $ttsUrl = "http://localhost:$ttsPort"

    $result = [ordered]@{
        WhisperPort      = $whisperPort
        WhisperUrl       = $whisperUrl
        WhisperHealthy   = $false
        ModelsApiReady   = $false
        SttModel         = $sttModel
        SttModelCached   = $false
        SttModelUrl      = $modelUrl
        RecoveryCommand  = "curl.exe --max-time 30 -X POST '$modelUrl'"
        TtsPort          = $ttsPort
        TtsUrl           = $ttsUrl
        TtsHealthy       = $false
    }

    try {
        Invoke-WebRequest -Uri "$whisperUrl/health" -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop | Out-Null
        $result.WhisperHealthy = $true
    } catch { }

    try {
        $resp = Invoke-WebRequest -Uri "$whisperUrl/v1/models" -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        if ($resp.StatusCode -eq 200) {
            $result.ModelsApiReady = $true
        }
    } catch { }

    try {
        $resp = Invoke-WebRequest -Uri $modelUrl -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        if ($resp.StatusCode -eq 200) {
            $result.SttModelCached = $true
        }
    } catch { }

    try {
        Invoke-WebRequest -Uri "$ttsUrl/health" -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop | Out-Null
        $result.TtsHealthy = $true
    } catch { }

    return $result
}

function Invoke-ODSSttModelDownloadTrigger {
    param([Parameter(Mandatory=$true)][string]$ModelUrl)

    # Speaches keeps downloading after it accepts the request. Keep the caller
    # bounded so slow Hugging Face transfers do not wedge ods.ps1 or install.
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        $curlOutput = & $curl.Source --fail --silent --show-error --max-time 30 -X POST $ModelUrl 2>&1
        $curlExit = $LASTEXITCODE
        if ($curlExit -eq 0 -or $curlExit -eq 28) { return $true }
        Write-AIWarn "STT model download trigger returned curl exit $curlExit; verifying cache before failing."
        if ($curlOutput) {
            Write-Host "  $($curlOutput | Out-String)" -ForegroundColor DarkGray
        }
        return $false
    }

    try {
        Invoke-WebRequest -Method POST -Uri $ModelUrl -TimeoutSec 30 -UseBasicParsing -ErrorAction Stop | Out-Null
        return $true
    } catch {
        Write-AIWarn "STT model download trigger failed; verifying cache before failing."
        return $false
    }
}

function Wait-ODSSttModelCached {
    param(
        [Parameter(Mandatory=$true)][string]$ModelUrl,
        [int]$TimeoutSeconds = 900
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $check = Invoke-WebRequest -Uri $ModelUrl -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
            if ($check.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Seconds 5
    }

    try {
        $check = Invoke-WebRequest -Uri $ModelUrl -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
        return ($check.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Test-ODSSttModelCache {
    try {
        $flags = Get-ComposeFlags
        if (-not (Test-ODSComposeServiceAvailable -ComposeFlags $flags -Service "whisper")) {
            return
        }
    } catch { }

    $diag = Get-ODSVoiceDiagnosis
    if (-not $diag.WhisperHealthy) {
        Write-AIWarn "Whisper STT: not responding (port $($diag.WhisperPort))"
        return
    }
    if ($diag.SttModelCached) {
        Write-AISuccess "Whisper STT model: cached ($($diag.SttModel))"
        return
    }

    $apiState = if ($diag.ModelsApiReady) { "models API ready" } else { "models API not ready" }
    Write-AIWarn "Whisper STT model missing ($($diag.SttModel)) -- transcription will 404 ($apiState)"
    Write-Host "  Run: $($diag.RecoveryCommand)" -ForegroundColor DarkGray
}

function Wait-ODSHttpOk {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 400) {
                return $true
            }
        } catch { }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Test-ODSComposeServiceAvailable {
    param(
        [string[]]$ComposeFlags,
        [Parameter(Mandatory = $true)][string]$Service
    )

    try {
        $services = & docker compose @ComposeFlags config --services 2>$null
        return ($services -contains $Service)
    } catch {
        return $false
    }
}

function Write-ODSMissingComposeServiceHint {
    param(
        [string[]]$ComposeFlags,
        [Parameter(Mandatory = $true)][string]$Service
    )

    Write-AIError "Service '$Service' is not in the active ODS compose stack."

    $serviceDir = Join-Path (Join-Path (Join-Path $InstallDir "extensions") "services") $Service
    $composePath = Join-Path $serviceDir "compose.yaml"
    $disabledComposePath = Join-Path $serviceDir "compose.yaml.disabled"

    if (Test-Path $disabledComposePath) {
        Write-AI "The $Service extension appears disabled in this runtime tree."
    } elseif (Test-Path $composePath) {
        Write-AI "The $Service extension exists, but the active .compose-flags stack does not include it."
        Write-AI "This can happen after a reinstall with different feature choices or a stale compose cache."
    } else {
        Write-AI "No compose fragment for '$Service' was found under extensions/services."
    }

    if ($Service -eq "n8n" -or $Service -eq "workflows") {
        Write-AI "n8n is optional. Install with -Workflows or -All if you want workflow automation."
    }

    Write-AI "Active compose services:"
    try {
        $services = & docker compose @ComposeFlags config --services 2>$null
        if ($services) {
            $services | ForEach-Object { Write-AI "  $_" }
        } else {
            Write-AI "  (none returned by docker compose config --services)"
        }
    } catch {
        Write-AI "  Could not inspect compose services. Run the diagnostic command below manually."
    }

    Write-AI "Diagnostic command:"
    Write-AI "  `$flags = (Get-Content .compose-flags -Raw).Trim() -split '\s+'"
    Write-AI "  docker compose @flags config --services"
}

function Set-ODSEnvValue {
    <#
    .SYNOPSIS
        Upsert a KEY=VALUE pair in .env without adding a UTF-8 BOM.
    #>
    param(
        [string]$Key,
        [string]$Value
    )

    $envFile = Join-Path $InstallDir ".env"
    if (-not (Test-Path $envFile)) { return }

    $lines = New-Object 'System.Collections.Generic.List[string]'
    Get-Content $envFile | ForEach-Object { [void]$lines.Add($_) }

    $escapedKey = [regex]::Escape($Key)
    $updated = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^${escapedKey}=") {
            $lines[$i] = "${Key}=${Value}"
            $updated = $true
            break
        }
    }

    if (-not $updated) {
        [void]$lines.Add("${Key}=${Value}")
    }

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($envFile, $lines.ToArray(), $utf8NoBom)
}

function Select-AutoCpuValue {
    <#
    .SYNOPSIS
        Keep a manual CPU override only when it is valid and more conservative.
    #>
    param(
        [string]$Existing,
        [string]$Detected
    )

    $existingNumber = 0.0
    $detectedNumber = 0.0
    $style = [System.Globalization.NumberStyles]::Float
    $culture = [System.Globalization.CultureInfo]::InvariantCulture
    $existingValid = [double]::TryParse($Existing, $style, $culture, [ref]$existingNumber)
    $detectedValid = [double]::TryParse($Detected, $style, $culture, [ref]$detectedNumber)

    if ($existingValid -and $detectedValid -and $existingNumber -gt 0 -and $existingNumber -le $detectedNumber) {
        return $Existing
    }
    return $Detected
}

function Select-CappedCpuValue {
    param(
        [string]$Desired,
        [string]$Ceiling
    )

    $desiredNumber = 0.0
    $ceilingNumber = 0.0
    $style = [System.Globalization.NumberStyles]::Float
    $culture = [System.Globalization.CultureInfo]::InvariantCulture
    if (-not [double]::TryParse($Desired, $style, $culture, [ref]$desiredNumber)) {
        $desiredNumber = 1.0
    }
    if (-not [double]::TryParse($Ceiling, $style, $culture, [ref]$ceilingNumber) -or $ceilingNumber -le 0) {
        $ceilingNumber = 1.0
    }

    $value = [Math]::Min($desiredNumber, $ceilingNumber)
    if ($value -lt 0.01) { $value = 0.01 }
    return $value.ToString("0.0", $culture)
}

function Ensure-LlamaCpuBudget {
    <#
    .SYNOPSIS
        Backfill/cap llama-server CPU settings for existing installs.
    #>
    $envFile = Join-Path $InstallDir ".env"
    if (-not (Test-Path $envFile)) { return }

    $envVars = Read-ODSEnv
    $gpuBackend = $envVars["GPU_BACKEND"]
    if ([string]::IsNullOrWhiteSpace($gpuBackend) -or $gpuBackend -eq "none") {
        $gpuBackend = "cpu"
    }
    $gpuBackend = $gpuBackend.ToLowerInvariant()

    $budget = Get-LlamaCpuBudget -GpuBackend $gpuBackend
    $llamaCpuLimit = Select-AutoCpuValue -Existing $envVars["LLAMA_CPU_LIMIT"] -Detected $budget.Limit
    $llamaCpuReservation = Select-AutoCpuValue -Existing $envVars["LLAMA_CPU_RESERVATION"] -Detected $budget.Reservation

    $limitNumber = 0.0
    $reservationNumber = 0.0
    $style = [System.Globalization.NumberStyles]::Float
    $culture = [System.Globalization.CultureInfo]::InvariantCulture
    if ([double]::TryParse($llamaCpuLimit, $style, $culture, [ref]$limitNumber) -and
        [double]::TryParse($llamaCpuReservation, $style, $culture, [ref]$reservationNumber) -and
        $reservationNumber -gt $limitNumber) {
        $llamaCpuReservation = $llamaCpuLimit
    }

    $changed = $false
    if ($envVars["LLAMA_CPU_LIMIT"] -ne $llamaCpuLimit) {
        Set-ODSEnvValue -Key "LLAMA_CPU_LIMIT" -Value $llamaCpuLimit
        $changed = $true
    }
    if ($envVars["LLAMA_CPU_RESERVATION"] -ne $llamaCpuReservation) {
        Set-ODSEnvValue -Key "LLAMA_CPU_RESERVATION" -Value $llamaCpuReservation
        $changed = $true
    }

    if ($changed) {
        Write-AI ("Auto-adjusted llama-server CPU budget: limit={0}, reservation={1} (Docker CPUs: {2})" -f `
            $llamaCpuLimit, $llamaCpuReservation, $budget.Available)
    }

    $serviceChanged = $false
    $serviceBudgets = @(
        @{ Name = "TTS"; DesiredLimit = "8.0"; DesiredReservation = "2.0" },
        @{ Name = "WHISPER"; DesiredLimit = "4.0"; DesiredReservation = "1.0" },
        @{ Name = "HERMES"; DesiredLimit = "4.0"; DesiredReservation = "0.5" },
        @{ Name = "COMFYUI"; DesiredLimit = "16.0"; DesiredReservation = "2.0" }
    )
    foreach ($service in $serviceBudgets) {
        $limitKey = "$($service.Name)_CPU_LIMIT"
        $reservationKey = "$($service.Name)_CPU_RESERVATION"
        $detectedLimit = Select-CappedCpuValue -Desired $service.DesiredLimit -Ceiling $budget.Available
        $finalLimit = Select-AutoCpuValue -Existing $envVars[$limitKey] -Detected $detectedLimit
        $detectedReservation = Select-CappedCpuValue -Desired $service.DesiredReservation -Ceiling $finalLimit
        $finalReservation = Select-AutoCpuValue -Existing $envVars[$reservationKey] -Detected $detectedReservation

        $finalLimitNumber = 0.0
        $finalReservationNumber = 0.0
        if ([double]::TryParse($finalLimit, $style, $culture, [ref]$finalLimitNumber) -and
            [double]::TryParse($finalReservation, $style, $culture, [ref]$finalReservationNumber) -and
            $finalReservationNumber -gt $finalLimitNumber) {
            $finalReservation = $finalLimit
        }

        if ($envVars[$limitKey] -ne $finalLimit) {
            Set-ODSEnvValue -Key $limitKey -Value $finalLimit
            $serviceChanged = $true
        }
        if ($envVars[$reservationKey] -ne $finalReservation) {
            Set-ODSEnvValue -Key $reservationKey -Value $finalReservation
            $serviceChanged = $true
        }
    }

    if ($serviceChanged) {
        Write-AI ("Auto-adjusted bundled service CPU budgets (Docker CPUs: {0})" -f $budget.Available)
    }
}

# ── AMD native inference server management (Lemonade or llama-server) ──

function Get-NativeInferenceBackend {
    <#
    .SYNOPSIS
        Determine which native inference backend is configured (from .env LLM_BACKEND).
    #>
    Sync-ODSNativeInferenceConfig
    $env = Read-ODSEnv
    $backend = $env["LLM_BACKEND"]
    if ($backend -eq "lemonade" -and (Test-Path $script:LEMONADE_EXE)) { return "lemonade" }
    if (Test-Path $script:LLAMA_SERVER_EXE) { return "llama-server" }
    return "none"
}

function Get-NativeInferenceStatus {
    <#
    .SYNOPSIS
        Check if native inference server is running (AMD path: Lemonade or llama-server).
    .OUTPUTS
        @{ Running; Pid; Healthy; Backend }
    #>
    Sync-ODSNativeInferenceConfig
    $backend = Get-NativeInferenceBackend
    $result = @{ Running = $false; Pid = 0; Healthy = $false; Backend = $backend }

    if (-not (Test-Path $script:INFERENCE_PID_FILE)) { return $result }

    # Guard the PID parse: this runs with $ErrorActionPreference = "Stop", so
    # casting an empty or non-numeric PID file to [int] throws a terminating
    # error and crashes `ods status`/`start`/`stop`. Mirror the numeric guard
    # used elsewhere and treat a bad PID file as "not running" (and clear it).
    $rawPid = (Get-Content $script:INFERENCE_PID_FILE -Raw)
    if (-not $rawPid) { $rawPid = "" }
    if ($rawPid.Trim() -notmatch '^\d+$') {
        Remove-Item $script:INFERENCE_PID_FILE -Force -ErrorAction SilentlyContinue
        return $result
    }
    $savedPid = [int]$rawPid.Trim()
    try {
        $proc = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
        if ($proc -and -not $proc.HasExited) {
            $result.Running = $true
            $result.Pid = $savedPid

            # Health check (Lemonade uses /api/v1/health, llama-server uses /health)
            $healthUrl = $(if ($backend -eq "lemonade") { $script:LEMONADE_HEALTH_URL } else { "http://localhost:8080/health" })
            try {
                $resp = Invoke-WebRequest -Uri $healthUrl `
                    -TimeoutSec 3 -UseBasicParsing -ErrorAction SilentlyContinue
                if ($resp.StatusCode -eq 200) {
                    $result.Healthy = $true
                }
            } catch { }
        }
    } catch { }

    # Clean up stale PID file
    if (-not $result.Running -and (Test-Path $script:INFERENCE_PID_FILE)) {
        Remove-Item $script:INFERENCE_PID_FILE -Force -ErrorAction SilentlyContinue
    }

    return $result
}

function Stop-ODSNativeProcessId {
    param([int]$ProcessId)

    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 30; $i++) {
        $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if (-not $proc) { return }
        Start-Sleep -Milliseconds 500
    }
}

function Stop-ODSOpenCodeRuntime {
    $opencodeExe = $script:OPENCODE_EXE
    $opencodePort = [string]$script:OPENCODE_PORT
    $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $byPid = @{}
    foreach ($proc in $processes) {
        if ($null -ne $proc.ProcessId) {
            $byPid[[int]$proc.ProcessId] = $proc
        }
    }

    $pidsToStop = @{}
    foreach ($proc in $processes) {
        $exe = [string]$proc.ExecutablePath
        $cmd = [string]$proc.CommandLine
        $isODSOpenCode = $false

        if ($exe -and $opencodeExe -and $exe.Equals($opencodeExe, [StringComparison]::OrdinalIgnoreCase)) {
            $isODSOpenCode = (
                $cmd -match '(?i)\bweb\b' -and
                $cmd -match ('(?i)--port\s+' + [regex]::Escape($opencodePort))
            )
        }

        if (-not $isODSOpenCode) { continue }

        $pidsToStop[[int]$proc.ProcessId] = $true

        $parentId = [int]$proc.ParentProcessId
        if ($parentId -gt 0 -and $byPid.ContainsKey($parentId)) {
            $parent = $byPid[$parentId]
            $parentName = [string]$parent.Name
            $parentCmd = [string]$parent.CommandLine
            $isHiddenLauncher = (
                $parentName -match '^(powershell|pwsh|wscript|cscript)(\.exe)?$' -and
                (
                    $parentName -match '^(wscript|cscript)(\.exe)?$' -or
                    $parentCmd -match '(?i)-EncodedCommand' -or
                    $parentCmd -match '(?i)-WindowStyle\s+Hidden'
                )
            )
            if ($isHiddenLauncher) {
                $pidsToStop[$parentId] = $true
            }
        }
    }

    foreach ($listener in @(Get-NetTCPConnection -LocalPort $script:OPENCODE_PORT -State Listen -ErrorAction SilentlyContinue)) {
        $ownerId = [int]$listener.OwningProcess
        if ($ownerId -le 0 -or -not $byPid.ContainsKey($ownerId)) { continue }
        $owner = $byPid[$ownerId]
        $exe = [string]$owner.ExecutablePath
        if ($exe -and $opencodeExe -and $exe.Equals($opencodeExe, [StringComparison]::OrdinalIgnoreCase)) {
            $pidsToStop[$ownerId] = $true
        }
    }

    if ($pidsToStop.Count -eq 0) { return }

    foreach ($pidValue in @($pidsToStop.Keys)) {
        Stop-ODSNativeProcessId -ProcessId ([int]$pidValue)
    }
    Write-AISuccess "OpenCode stopped ($($pidsToStop.Count) process(es))"
}

function Stop-ODSLemonadeRuntime {
    Sync-ODSNativeInferenceConfig
    try { Stop-ScheduledTask -TaskName $script:LEMONADE_TASK_NAME -ErrorAction SilentlyContinue } catch { }
    try { Unregister-ScheduledTask -TaskName $script:LEMONADE_TASK_NAME -Confirm:$false -ErrorAction SilentlyContinue } catch { }

    if (Test-Path $script:INFERENCE_PID_FILE) {
        $rawPid = (Get-Content -LiteralPath $script:INFERENCE_PID_FILE -Raw).Trim()
        if ($rawPid -match '^\d+$') {
            Stop-ODSNativeProcessId -ProcessId ([int]$rawPid)
        }
        Remove-Item -LiteralPath $script:INFERENCE_PID_FILE -Force -ErrorAction SilentlyContinue
    }

    foreach ($listener in @(Get-NetTCPConnection -LocalPort $script:LEMONADE_PORT -State Listen -ErrorAction SilentlyContinue)) {
        if ($listener.OwningProcess -gt 0) {
            Stop-ODSNativeProcessId -ProcessId ([int]$listener.OwningProcess)
        }
    }

    $binDir = Split-Path -Parent $script:LEMONADE_EXE
    $userProfile = [Environment]::GetFolderPath("UserProfile")
    $cacheBin = if ($userProfile) { Join-Path (Join-Path (Join-Path $userProfile ".cache") "lemonade") "bin" } else { $null }
    $modelsDir = Join-Path (Join-Path $InstallDir "data") "models"
    foreach ($child in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($binDir, [StringComparison]::OrdinalIgnoreCase)) -or
        ($cacheBin -and $_.ExecutablePath -and $_.ExecutablePath.StartsWith($cacheBin, [StringComparison]::OrdinalIgnoreCase)) -or
        ($_.CommandLine -and $_.CommandLine.IndexOf($modelsDir, [StringComparison]::OrdinalIgnoreCase) -ge 0)
    })) {
        Stop-ODSNativeProcessId -ProcessId ([int]$child.ProcessId)
    }
}

function Start-ODSLemonadeRuntime {
    param([string]$BindAddress)

    Sync-ODSNativeInferenceConfig
    $modelsDir = Join-Path (Join-Path $InstallDir "data") "models"
    Stop-ODSLemonadeRuntime

    $argString = "serve --port $($script:LEMONADE_PORT) --host $BindAddress --no-tray --llamacpp vulkan --extra-models-dir `"$modelsDir`""
    $launchMethod = "scheduled task"
    try {
        $action = New-ScheduledTaskAction -Execute $script:LEMONADE_EXE -Argument $argString -WorkingDirectory (Split-Path -Parent $script:LEMONADE_EXE)
        $trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddYears(1))
        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $script:LEMONADE_TASK_NAME -Action $action -Trigger $trigger -Principal $principal -Force -ErrorAction Stop | Out-Null
        Start-ScheduledTask -TaskName $script:LEMONADE_TASK_NAME -ErrorAction Stop
    } catch {
        $launchMethod = "direct process"
        Write-AIWarn "Could not start Lemonade through Task Scheduler: $_"
        Write-AI "Starting Lemonade directly for this Windows session..."
        Start-Process -FilePath $script:LEMONADE_EXE -ArgumentList $argString -WindowStyle Hidden -WorkingDirectory (Split-Path -Parent $script:LEMONADE_EXE) | Out-Null
    }

    Start-Sleep -Seconds 5
    $proc = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.Equals($script:LEMONADE_EXE, [StringComparison]::OrdinalIgnoreCase) } |
        Sort-Object ProcessId -Descending |
        Select-Object -First 1
    if (-not $proc -and $launchMethod -eq "scheduled task") {
        $launchMethod = "direct process"
        Write-AIWarn "Lemonade scheduled task did not start a server process."
        Write-AI "Starting Lemonade directly for this Windows session..."
        Start-Process -FilePath $script:LEMONADE_EXE -ArgumentList $argString -WindowStyle Hidden -WorkingDirectory (Split-Path -Parent $script:LEMONADE_EXE) | Out-Null
        Start-Sleep -Seconds 3
        $proc = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.ExecutablePath -and $_.ExecutablePath.Equals($script:LEMONADE_EXE, [StringComparison]::OrdinalIgnoreCase) } |
            Sort-Object ProcessId -Descending |
            Select-Object -First 1
    }
    if (-not $proc) {
        throw "Lemonade $launchMethod started but no Lemonade process was found"
    }

    $pidDir = Split-Path $script:INFERENCE_PID_FILE
    New-Item -ItemType Directory -Path $pidDir -Force | Out-Null
    Set-Content -Path $script:INFERENCE_PID_FILE -Value $proc.ProcessId
    return [int]$proc.ProcessId
}

# Backward-compat alias
function Get-NativeLlamaStatus { return Get-NativeInferenceStatus }

function Start-NativeInferenceServer {
    <#
    .SYNOPSIS
        Start native inference server for AMD path (Lemonade or llama-server).
    #>
    $status = Get-NativeInferenceStatus
    if ($status.Running) {
        Write-AISuccess "Native $($status.Backend) already running (PID $($status.Pid))"
        return
    }

    $backend = Get-NativeInferenceBackend
    $envVars = Read-ODSEnv

    # Honour the unified BIND_ADDRESS knob (PR #964); empty/missing → loopback.
    $bindAddr = $envVars["BIND_ADDRESS"]
    if ([string]::IsNullOrWhiteSpace($bindAddr)) { $bindAddr = "127.0.0.1" }

    if ($backend -eq "lemonade") {
        $procId = Start-ODSLemonadeRuntime -BindAddress $bindAddr
        Write-AISuccess "Lemonade server started (PID $procId)"
        Write-AI "Waiting for health..."

        $maxWait = 60; $waited = 0
        while ($waited -lt $maxWait) {
            Start-Sleep -Seconds 2; $waited += 2
            try {
                $resp = Invoke-WebRequest -Uri $script:LEMONADE_HEALTH_URL `
                    -TimeoutSec 3 -UseBasicParsing -ErrorAction SilentlyContinue
                if ($resp.StatusCode -eq 200) {
                    Write-AISuccess "Lemonade server healthy"
                    return
                }
            } catch { }
        }
        Write-AIWarn "Lemonade server may still be starting..."
    } elseif ($backend -eq "llama-server") {
        $ggufFile = $envVars["GGUF_FILE"]
        $ctxSize  = $envVars["CTX_SIZE"]
        if (-not $ggufFile) { $ggufFile = "Qwen3.5-9B-Q4_K_M.gguf" }
        if (-not $ctxSize)  { $ctxSize = "16384" }

        $modelPath = Join-Path (Join-Path $InstallDir "data\models") $ggufFile
        if (-not (Test-Path $modelPath)) {
            Write-AIError "Model not found: $modelPath"
            return
        }

        $llamaArgs = @(
            "--model", $modelPath,
            "--host", $bindAddr,
            "--port", "8080",
            "--n-gpu-layers", "999",
            "--ctx-size", $ctxSize
        )
        if ($envVars["LLAMA_ARG_FLASH_ATTN"]) { $llamaArgs += @("--flash-attn", $envVars["LLAMA_ARG_FLASH_ATTN"]) }
        if ($envVars["LLAMA_ARG_CACHE_TYPE_K"]) { $llamaArgs += @("--cache-type-k", $envVars["LLAMA_ARG_CACHE_TYPE_K"]) }
        if ($envVars["LLAMA_ARG_CACHE_TYPE_V"]) { $llamaArgs += @("--cache-type-v", $envVars["LLAMA_ARG_CACHE_TYPE_V"]) }
        if ($envVars["LLAMA_ARG_N_CPU_MOE"]) { $llamaArgs += @("--n-cpu-moe", $envVars["LLAMA_ARG_N_CPU_MOE"]) }
        if ($envVars["LLAMA_PARALLEL"]) { $llamaArgs += @("--parallel", $envVars["LLAMA_PARALLEL"]) }
        if ($envVars["LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS"]) { $llamaArgs += @("--checkpoint-every-n-tokens", $envVars["LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS"]) }
        if ($envVars["LLAMA_ARG_NO_CACHE_PROMPT"] -and $envVars["LLAMA_ARG_NO_CACHE_PROMPT"] -notin @("0", "false", "off", "no")) { $llamaArgs += @("--no-cache-prompt") }
        if ($envVars["LLAMA_ARG_SPEC_TYPE"]) { $llamaArgs += @("--spec-type", $envVars["LLAMA_ARG_SPEC_TYPE"]) }
        if ($envVars["LLAMA_ARG_SPEC_DRAFT_N_MAX"]) { $llamaArgs += @("--spec-draft-n-max", $envVars["LLAMA_ARG_SPEC_DRAFT_N_MAX"]) }

        $pidDir = Split-Path $script:INFERENCE_PID_FILE
        New-Item -ItemType Directory -Path $pidDir -Force | Out-Null

        $proc = Start-Process -FilePath $script:LLAMA_SERVER_EXE `
            -ArgumentList $llamaArgs -WindowStyle Hidden -PassThru
        Set-Content -Path $script:INFERENCE_PID_FILE -Value $proc.Id

        Write-AISuccess "Native llama-server started (PID $($proc.Id))"
        Write-AI "Waiting for health..."

        $maxWait = 60; $waited = 0
        while ($waited -lt $maxWait) {
            Start-Sleep -Seconds 2; $waited += 2
            try {
                $resp = Invoke-WebRequest -Uri "http://localhost:8080/health" `
                    -TimeoutSec 3 -UseBasicParsing -ErrorAction SilentlyContinue
                if ($resp.StatusCode -eq 200) {
                    Write-AISuccess "Native llama-server healthy"
                    return
                }
            } catch { }
        }
        Write-AIWarn "llama-server may still be loading model..."
    } else {
        Write-AIError "No native inference server found. Re-run the installer."
    }
}

# Backward-compat alias
function Start-NativeLlamaServer { Start-NativeInferenceServer }

function Stop-NativeInferenceServer {
    $status = Get-NativeInferenceStatus
    if ($status.Backend -eq "lemonade") {
        Stop-ODSLemonadeRuntime
        Write-AISuccess "Native lemonade stopped"
        return
    }

    if (-not $status.Running) {
        Write-AI "Native inference server not running"
        return
    }

    try {
        Stop-Process -Id $status.Pid -Force -ErrorAction SilentlyContinue
        Write-AISuccess "Native $($status.Backend) stopped (PID $($status.Pid))"
    } catch {
        Write-AIWarn "Could not stop PID $($status.Pid): $_"
    }

    if (Test-Path $script:INFERENCE_PID_FILE) {
        Remove-Item $script:INFERENCE_PID_FILE -Force -ErrorAction SilentlyContinue
    }
}

# Backward-compat alias
function Stop-NativeLlamaServer { Stop-NativeInferenceServer }

# ============================================================================
# Commands
# ============================================================================

function Invoke-Status {
    Test-Install
    Push-Location $InstallDir
    try {
        $flags = Get-ComposeFlags
        Write-Host ""
        Write-Host "  ODS Status" -ForegroundColor Cyan
        Write-Host ("  " + ("-" * 40)) -ForegroundColor DarkGray

        # Native inference server status (AMD: Lemonade or llama-server)
        if (Test-Path $script:INFERENCE_PID_FILE) {
            $nativeStatus = Get-NativeInferenceStatus
            if ($nativeStatus.Running) {
                $healthStr = $(if ($nativeStatus.Healthy) { "healthy" } else { "loading" })
                Write-AISuccess "$($nativeStatus.Backend) (native): running PID $($nativeStatus.Pid) ($healthStr)"
            } else {
                Write-AIWarn "$($nativeStatus.Backend) (native): not running (stale PID cleaned)"
            }
        }

        # Host agent status
        try {
            $resp = Invoke-WebRequest -Uri $script:ODS_AGENT_HEALTH_URL `
                -TimeoutSec 3 -UseBasicParsing -ErrorAction SilentlyContinue
            if ($resp.StatusCode -eq 200) {
                Write-AISuccess "Host Agent: running (port $($script:ODS_AGENT_PORT))"
            } else {
                Write-AIWarn "Host Agent: responded with $($resp.StatusCode)"
            }
        } catch {
            Write-AIWarn "Host Agent: not responding (port $($script:ODS_AGENT_PORT))"
        }

        # Docker services
        Write-Host ""
        & docker compose @flags ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" 2>$null

        # Health checks
        Write-Host ""
        Write-Host "  Health Checks" -ForegroundColor Cyan
        Write-Host ("  " + ("-" * 40)) -ForegroundColor DarkGray

        $llmEndpoint = Get-WindowsLocalLlmEndpoint -InstallDir $InstallDir -NativeBackend (Get-NativeInferenceBackend)
        $endpoints = @(
            @{ Name = "LLM API";    Url = $llmEndpoint.HealthUrl }
            @{ Name = "Chat UI";    Url = "http://localhost:3000" }
            @{ Name = "Dashboard";  Url = "http://localhost:3001" }
        )

        foreach ($ep in $endpoints) {
            try {
                $resp = Invoke-WebRequest -Uri $ep.Url -TimeoutSec 3 `
                    -UseBasicParsing -ErrorAction SilentlyContinue
                if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 400) {
                    Write-AISuccess "$($ep.Name): healthy"
                } else {
                    Write-AIWarn "$($ep.Name): $($resp.StatusCode)"
                }
            } catch {
                Write-AIWarn "$($ep.Name): not responding"
            }
        }
        Test-ODSSttModelCache

        # GPU status
        Write-Host ""
        $gpuInfo = Get-GpuInfo
        if ($gpuInfo.Backend -eq "nvidia") {
            Write-Host "  GPU Status" -ForegroundColor Cyan
            try {
                $gpuStats = & nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits 2>$null
                if ($gpuStats) {
                    $gpuStats -split "`n" | ForEach-Object {
                        $parts = $_ -split ","
                        if ($parts.Count -ge 5) {
                            Write-Host "  $($parts[0].Trim()): $($parts[1].Trim())% GPU | $($parts[2].Trim())MB/$($parts[3].Trim())MB VRAM | $($parts[4].Trim())C" -ForegroundColor White
                        }
                    }
                }
            } catch { }
        } elseif ($gpuInfo.Backend -eq "amd") {
            Write-Host "  GPU: $($gpuInfo.Name) ($($gpuInfo.MemoryType) memory)" -ForegroundColor White
        }

        Write-Host ""
    } finally {
        Pop-Location
    }
}

function Invoke-Start {
    param([string]$Service)
    Test-Install
    Push-Location $InstallDir
    try {
        Ensure-LlamaCpuBudget

        # Start native inference server first (AMD path: Lemonade or llama-server)
        if (-not $Service -and ((Get-NativeInferenceBackend) -ne "none")) {
            Start-NativeInferenceServer
        }

        # Start host agent (if not already running)
        if (-not $Service) {
            Invoke-Agent -Action "start"
        }

        $flags = Get-ComposeFlags
        $hermesInStack = Test-ODSComposeServiceAvailable -ComposeFlags $flags -Service "hermes"
        if ($Service) {
            if (-not (Test-ODSComposeServiceAvailable -ComposeFlags $flags -Service $Service)) {
                Write-ODSMissingComposeServiceHint -ComposeFlags $flags -Service $Service
                exit 1
            }
            Write-AI "Starting $Service..."
            if ($Service -eq "hermes" -and $hermesInStack) {
                Invoke-HermesSoulRefresh
            }
            $composeExit = Invoke-ODSDockerCompose -InstallDir $InstallDir -ComposeFlags $flags `
                -ComposeArgs @("up", "-d", $Service)
            if ($composeExit -ne 0) {
                Write-AIError "docker compose up failed (exit code: $composeExit)"
                Write-ODSComposeDiagnostics -InstallDir $InstallDir -ComposeFlags $flags `
                    -Phase "ods.ps1 start ($Service)"
                exit 1
            }
            Write-AISuccess "$Service started"
            if ($Service -eq "hermes" -and $hermesInStack) {
                Invoke-HermesSoulRefresh -SyncContainer
            }
        } else {
            if ($hermesInStack) {
                Invoke-HermesSoulRefresh
            }
            Write-AI "Starting all services..."
            $composeExit = Invoke-ODSDockerCompose -InstallDir $InstallDir -ComposeFlags $flags `
                -ComposeArgs @("up", "-d")
            if ($composeExit -ne 0) {
                Write-AIError "docker compose up failed (exit code: $composeExit)"
                Write-ODSComposeDiagnostics -InstallDir $InstallDir -ComposeFlags $flags -Phase "ods.ps1 start (all)"
                exit 1
            }
            Write-AISuccess "All services started"
            if ($hermesInStack) {
                Invoke-HermesSoulRefresh -SyncContainer
            }
        }
    } finally {
        Pop-Location
    }
}

function Invoke-Stop {
    param([string]$Service)

    if (-not $Service) {
        if (-not (Test-Path $InstallDir)) {
            Write-AIError "ODS not found at $InstallDir. Set ODS_HOME or run installer first."
            exit 1
        }
        Push-Location $InstallDir
        try {
            # Native helpers do not depend on Docker and may hold install files or ports.
            if ((Get-NativeInferenceBackend) -ne "none") {
                Stop-NativeInferenceServer
            }
            Invoke-Agent -Action "stop"
            Stop-ODSOpenCodeRuntime
        } finally {
            Pop-Location
        }
    }

    Test-Install
    Push-Location $InstallDir
    try {
        $flags = Get-ComposeFlags
        if ($Service) {
            if (-not (Test-ODSComposeServiceAvailable -ComposeFlags $flags -Service $Service)) {
                Write-ODSMissingComposeServiceHint -ComposeFlags $flags -Service $Service
                exit 1
            }
            Write-AI "Stopping $Service..."
            $composeExit = Invoke-ODSDockerCompose -InstallDir $InstallDir -ComposeFlags $flags `
                -ComposeArgs @("stop", $Service)
            if ($composeExit -ne 0) {
                Write-AIError "docker compose stop failed (exit code: $composeExit)"
                Write-ODSComposeDiagnostics -InstallDir $InstallDir -ComposeFlags $flags `
                    -Phase "ods.ps1 stop ($Service)"
                exit 1
            }
            Write-AISuccess "$Service stopped"
        } else {
            Write-AI "Stopping all services..."
            $composeExit = Invoke-ODSDockerCompose -InstallDir $InstallDir -ComposeFlags $flags `
                -ComposeArgs @("down")
            if ($composeExit -ne 0) {
                Write-AIError "docker compose down failed (exit code: $composeExit)"
                Write-ODSComposeDiagnostics -InstallDir $InstallDir -ComposeFlags $flags -Phase "ods.ps1 stop (all)"
                exit 1
            }

            Write-AISuccess "All services stopped"
        }
    } finally {
        Pop-Location
    }
}

function Invoke-Restart {
    param([string]$Service)
    Test-Install
    Push-Location $InstallDir
    try {
        Ensure-LlamaCpuBudget

        $flags = Get-ComposeFlags
        $hermesInStack = Test-ODSComposeServiceAvailable -ComposeFlags $flags -Service "hermes"
        if ($Service) {
            if (-not (Test-ODSComposeServiceAvailable -ComposeFlags $flags -Service $Service)) {
                Write-ODSMissingComposeServiceHint -ComposeFlags $flags -Service $Service
                exit 1
            }
            Write-AI "Restarting $Service..."
            if ($Service -eq "hermes" -and $hermesInStack) {
                Invoke-HermesSoulRefresh
            }
            $composeExit = Invoke-ODSDockerCompose -InstallDir $InstallDir -ComposeFlags $flags `
                -ComposeArgs @("restart", $Service)
            if ($composeExit -ne 0) {
                Write-AIError "docker compose restart failed (exit code: $composeExit)"
                Write-ODSComposeDiagnostics -InstallDir $InstallDir -ComposeFlags $flags `
                    -Phase "ods.ps1 restart ($Service)"
                exit 1
            }
            Write-AISuccess "$Service restarted"
            if ($Service -eq "hermes" -and $hermesInStack) {
                Invoke-HermesSoulRefresh -SyncContainer
            }
        } else {
            # For AMD, also restart native inference server
            if ((Get-NativeInferenceBackend) -ne "none") {
                Stop-NativeInferenceServer
                Start-NativeInferenceServer
            }
            if ($hermesInStack) {
                Invoke-HermesSoulRefresh
            }
            Write-AI "Restarting all services..."
            $composeExit = Invoke-ODSDockerCompose -InstallDir $InstallDir -ComposeFlags $flags `
                -ComposeArgs @("restart")
            if ($composeExit -ne 0) {
                Write-AIError "docker compose restart failed (exit code: $composeExit)"
                Write-ODSComposeDiagnostics -InstallDir $InstallDir -ComposeFlags $flags -Phase "ods.ps1 restart (all)"
                exit 1
            }
            Write-AISuccess "All services restarted"
            if ($hermesInStack) {
                Invoke-HermesSoulRefresh -SyncContainer
            }
        }
    } finally {
        Pop-Location
    }
}

function Invoke-Logs {
    param(
        [string]$Service,
        [int]$Lines = 100
    )
    if (-not $Service) {
        Write-AI "Usage: .\ods.ps1 logs <service> [lines]"
        Write-AI "Services: llama-server, open-webui, dashboard-api, n8n, whisper, tts, ..."
        return
    }
    Test-Install
    Push-Location $InstallDir
    try {
        $flags = Get-ComposeFlags
        if (-not (Test-ODSComposeServiceAvailable -ComposeFlags $flags -Service $Service)) {
            Write-ODSMissingComposeServiceHint -ComposeFlags $flags -Service $Service
            exit 1
        }
        & docker compose @flags logs -f --tail $Lines $Service
    } finally {
        Pop-Location
    }
}

function Invoke-ConfigShow {
    Test-Install
    Write-Host ""
    Write-Host "  Configuration" -ForegroundColor Cyan
    Write-Host "  Install dir: $InstallDir" -ForegroundColor White
    Write-Host ""

    $envFile = Join-Path $InstallDir ".env"
    if (-not (Test-Path $envFile)) {
        Write-AIWarn ".env not found"
        return
    }

    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -match "^#" -or $line -eq "") { return }
        # Redact any key whose NAME contains a sensitive keyword, mirroring the
        # Linux CLI's `ods config show` over-mask policy. Anchoring keywords to
        # the "=" (the old behavior) let *_PASSWORD, *_SALT, and similar values
        # print in cleartext because the keyword is not the last token before "=".
        $key = ($line -split '=', 2)[0].Trim()
        if ($line.Contains('=') -and
            $key -match '(?i)secret|password|pass|token|key|salt|bearer|user|email') {
            Write-Host "  $key=***" -ForegroundColor DarkGray
        } else {
            Write-Host "  $line" -ForegroundColor White
        }
    }
    Write-Host ""
}

function Invoke-Chat {
    param([string]$Message)
    if (-not $Message) {
        Write-AI "Usage: .\ods.ps1 chat `"your message`""
        return
    }

    $body = @{
        model    = "default"
        messages = @(
            @{ role = "user"; content = $Message }
        )
    } | ConvertTo-Json -Depth 3

    $llmEndpoint = Get-WindowsLocalLlmEndpoint -InstallDir $InstallDir -NativeBackend (Get-NativeInferenceBackend)
    try {
        $resp = Invoke-RestMethod -Uri $llmEndpoint.ChatCompletionsUrl `
            -Method POST -Body $body -ContentType "application/json" -TimeoutSec 120

        if ($resp.choices -and $resp.choices[0].message) {
            Write-Host ""
            Write-Host $resp.choices[0].message.content
            Write-Host ""
        }
    } catch {
        Write-AIError "Chat request failed: $_"
        Write-AI "Is llama-server running? Try: .\ods.ps1 status"
    }
}

function Invoke-Update {
    Test-Install
    Push-Location $InstallDir
    try {
        Ensure-LlamaCpuBudget

        $flags = Get-ComposeFlags
        Write-AI "Pulling latest images..."
        $pullExit = Invoke-ODSDockerCompose -InstallDir $InstallDir -ComposeFlags $flags -ComposeArgs @("pull")
        if ($pullExit -ne 0) {
            Write-AIError "docker compose pull failed (exit code: $pullExit)"
            Write-ODSComposeDiagnostics -InstallDir $InstallDir -ComposeFlags $flags -Phase "ods.ps1 update (pull)"
            exit 1
        }
        Write-AI "Recreating containers..."
        $upExit = Invoke-ODSDockerCompose -InstallDir $InstallDir -ComposeFlags $flags `
            -ComposeArgs @("up", "-d", "--force-recreate")
        if ($upExit -ne 0) {
            Write-AIError "docker compose up failed (exit code: $upExit)"
            Write-ODSComposeDiagnostics -InstallDir $InstallDir -ComposeFlags $flags -Phase "ods.ps1 update (up --force-recreate)"
            exit 1
        }
        Write-AISuccess "Update complete"

        Start-Sleep -Seconds 5
        Invoke-Status
    } finally {
        Pop-Location
    }
}

function Invoke-Report {
    Test-Install
    Push-Location $InstallDir
    try {
        $flags = Get-ComposeFlags
        Write-ODSInstallReport -InstallDir $InstallDir -ComposeFlags $flags | Out-Null
    } finally {
        Pop-Location
    }
}

function Invoke-Doctor {
    Test-Install
    Push-Location $InstallDir
    try {
        $flags = Get-ComposeFlags
        $voiceInStack = (
            (Test-ODSComposeServiceAvailable -ComposeFlags $flags -Service "whisper") -and
            (Test-ODSComposeServiceAvailable -ComposeFlags $flags -Service "tts")
        )

        Write-Host ""
        Write-Host "  ODS Doctor" -ForegroundColor Cyan
        Write-Host ("  " + ("-" * 40)) -ForegroundColor DarkGray

        $hasIssue = $false

        Write-Host ""
        Write-Host "  Voice Readiness" -ForegroundColor Cyan
        if (-not $voiceInStack) {
            Write-AI "Voice services: not enabled in this compose stack"
            Write-Host ""
            Write-AISuccess "Doctor found no voice readiness issues."
            return
        }

        $voice = Get-ODSVoiceDiagnosis
        if ($voice.WhisperHealthy) {
            Write-AISuccess "Whisper STT: healthy ($($voice.WhisperUrl))"
        } else {
            Write-AIWarn "Whisper STT: not responding ($($voice.WhisperUrl))"
            $hasIssue = $true
        }

        if ($voice.SttModelCached) {
            Write-AISuccess "Whisper STT model: cached ($($voice.SttModel))"
        } elseif ($voice.WhisperHealthy) {
            Write-AIWarn "Whisper STT model: missing ($($voice.SttModel))"
            Write-Host "  Repair: .\ods.ps1 repair voice" -ForegroundColor DarkGray
            Write-Host "  Manual: $($voice.RecoveryCommand)" -ForegroundColor DarkGray
            $hasIssue = $true
        }

        if ($voice.TtsHealthy) {
            Write-AISuccess "Kokoro TTS: healthy ($($voice.TtsUrl))"
        } else {
            Write-AIWarn "Kokoro TTS: not responding ($($voice.TtsUrl))"
            Write-Host "  Repair: .\ods.ps1 repair voice" -ForegroundColor DarkGray
            $hasIssue = $true
        }

        Write-Host ""
        if ($hasIssue) {
            Write-AIWarn "Doctor found repairable voice issues."
            exit 1
        }
        Write-AISuccess "Doctor found no voice readiness issues."
    } finally {
        Pop-Location
    }
}

function Invoke-RepairVoice {
    Test-Install
    Push-Location $InstallDir
    try {
        $flags = Get-ComposeFlags

        Write-Host ""
        Write-Host "  Repair Voice" -ForegroundColor Cyan
        Write-Host ("  " + ("-" * 40)) -ForegroundColor DarkGray

        $missingServices = @()
        foreach ($svc in @("whisper", "tts")) {
            if (-not (Test-ODSComposeServiceAvailable -ComposeFlags $flags -Service $svc)) {
                $missingServices += $svc
            }
        }
        if ($missingServices.Count -gt 0) {
            Write-AIError "Voice services are not in this compose stack: $($missingServices -join ', ')"
            Write-AI "Enable voice in the installer or add the whisper/tts extension compose files, then retry."
            exit 1
        }

        Write-AI "Starting Whisper and Kokoro TTS..."
        $composeExit = Invoke-ODSDockerCompose -InstallDir $InstallDir -ComposeFlags $flags `
            -ComposeArgs @("up", "-d", "whisper", "tts")
        if ($composeExit -ne 0) {
            Write-AIError "docker compose up failed (exit code: $composeExit)"
            Write-ODSComposeDiagnostics -InstallDir $InstallDir -ComposeFlags $flags -Phase "ods.ps1 repair voice"
            exit 1
        }

        $voice = Get-ODSVoiceDiagnosis
        if (-not $voice.WhisperHealthy) {
            Write-AI "Waiting for Whisper STT..."
            Wait-ODSHttpOk -Url "$($voice.WhisperUrl)/health" -TimeoutSeconds 90 | Out-Null
        }
        if (-not $voice.TtsHealthy) {
            Write-AI "Waiting for Kokoro TTS..."
            Wait-ODSHttpOk -Url "$($voice.TtsUrl)/health" -TimeoutSeconds 90 | Out-Null
        }

        $voice = Get-ODSVoiceDiagnosis
        if (-not $voice.WhisperHealthy) {
            Write-AIError "Whisper STT is still not responding. Check: .\ods.ps1 logs whisper 100"
            exit 1
        }
        if (-not $voice.SttModelCached) {
            Write-AI "Downloading STT model ($($voice.SttModel))..."
            Invoke-ODSSttModelDownloadTrigger -ModelUrl $voice.SttModelUrl | Out-Null
            Wait-ODSSttModelCached -ModelUrl $voice.SttModelUrl -TimeoutSeconds 900 | Out-Null
        }

        $voice = Get-ODSVoiceDiagnosis
        if ($voice.SttModelCached) {
            Write-AISuccess "Whisper STT model cached ($($voice.SttModel))"
        } else {
            Write-AIError "STT model is still missing. Run manually: $($voice.RecoveryCommand)"
            exit 1
        }

        if ($voice.TtsHealthy) {
            Write-AISuccess "Kokoro TTS healthy"
        } else {
            Write-AIError "Kokoro TTS is still not responding. Check: .\ods.ps1 logs tts 100"
            exit 1
        }

        Write-AISuccess "Voice repair complete."
    } finally {
        Pop-Location
    }
}

function Invoke-Repair {
    param([string]$Target)

    if ([string]::IsNullOrWhiteSpace($Target)) {
        $Target = "voice"
    }

    switch ($Target.ToLower()) {
        "voice" { Invoke-RepairVoice }
        "stt"   { Invoke-RepairVoice }
        "tts"   { Invoke-RepairVoice }
        default {
            Write-AI "Usage: .\ods.ps1 repair voice"
            Write-AIWarn "Unknown repair target: $Target"
            exit 1
        }
    }
}

function Test-ODSHostAgentPythonCandidate {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$PrefixArgs = @()
    )

    if ([string]::IsNullOrWhiteSpace($FilePath) -or -not (Test-Path $FilePath)) {
        return $false
    }
    if ($FilePath -match '\\WindowsApps\\python3?\.exe$') {
        return $false
    }

    try {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        $version = & $FilePath @PrefixArgs --version 2>&1
        $exitCode = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP
        return ($exitCode -eq 0 -and (($version | Out-String) -match 'Python 3\.'))
    } catch {
        $ErrorActionPreference = $prevEAP
        return $false
    }
}

function New-ODSHostAgentPythonCandidate {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$PrefixArgs = @()
    )

    [pscustomobject]@{
        FilePath   = $FilePath
        PrefixArgs = @($PrefixArgs)
    }
}

function Resolve-ODSHostAgentPython {
    $seen = @{}
    $candidateFiles = New-Object System.Collections.Generic.List[string]

    foreach ($root in @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python"),
        $env:ProgramFiles,
        ${env:ProgramFiles(x86)}
    )) {
        if ([string]::IsNullOrWhiteSpace($root) -or -not (Test-Path $root)) { continue }
        Get-ChildItem -Path $root -Directory -Filter "Python*" -ErrorAction SilentlyContinue |
            ForEach-Object {
                $exe = Join-Path $_.FullName "python.exe"
                if (Test-Path $exe) { $candidateFiles.Add($exe) }
            }
    }

    foreach ($name in @("python3", "python")) {
        $cmd = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source) {
            $candidateFiles.Add($cmd.Source)
        }
    }

    foreach ($file in $candidateFiles) {
        $key = $file.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        if (Test-ODSHostAgentPythonCandidate -FilePath $file) {
            return (New-ODSHostAgentPythonCandidate -FilePath $file)
        }
    }

    $pyLauncher = Get-Command py -CommandType Application -ErrorAction SilentlyContinue
    if ($pyLauncher -and $pyLauncher.Source -and
        (Test-ODSHostAgentPythonCandidate -FilePath $pyLauncher.Source -PrefixArgs @("-3"))) {
        return (New-ODSHostAgentPythonCandidate -FilePath $pyLauncher.Source -PrefixArgs @("-3"))
    }

    return $null
}

function Invoke-Agent {
    param([string]$Action = "status")

    $agentScript = Join-Path (Join-Path $InstallDir "bin") "ods-host-agent.py"
    $pidFile     = $script:ODS_AGENT_PID_FILE
    $logFile     = $script:ODS_AGENT_LOG_FILE
    $port        = $script:ODS_AGENT_PORT
    $healthUrl   = $script:ODS_AGENT_HEALTH_URL

    switch ($Action.ToLower()) {
        "status" {
            try {
                $resp = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 3 `
                    -UseBasicParsing -ErrorAction SilentlyContinue
                if ($resp.StatusCode -eq 200) {
                    Write-AISuccess "Host agent: running (port $port)"
                } else {
                    Write-AIWarn "Host agent: responded with status $($resp.StatusCode)"
                }
            } catch {
                Write-AIWarn "Host agent: not responding (port $port)"
            }
        }
        "start" {
            # Check if already running
            try {
                $resp = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 2 `
                    -UseBasicParsing -ErrorAction SilentlyContinue
                if ($resp.StatusCode -eq 200) {
                    Write-AISuccess "Host agent already running (port $port)"
                    return
                }
            } catch { }

            # Find Python
            $_python3 = Resolve-ODSHostAgentPython
            if (-not $_python3) {
                Write-AIError "Python 3 not found (ignoring Microsoft Store aliases) -- install Python 3.12 and try again"
                return
            }
            if (-not (Test-Path $agentScript)) {
                Write-AIError "Agent script not found: $agentScript"
                return
            }

            # Clean stale PID
            if (Test-Path $pidFile) {
                try {
                    $_oldPid = [int](Get-Content $pidFile -Raw).Trim()
                    Stop-Process -Id $_oldPid -Force -ErrorAction SilentlyContinue
                } catch { }
                Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
            }

            $pidDir = Split-Path $pidFile
            New-Item -ItemType Directory -Path $pidDir -Force -ErrorAction SilentlyContinue | Out-Null

            # Start the agent through Task Scheduler so SSH-launched restarts
            # survive the non-interactive PowerShell session ending.
            $_dockerBin = "C:\Program Files\Docker\Docker\resources\bin"
            $_psQuote = {
                param([string]$Value)
                "'" + ($Value -replace "'", "''") + "'"
            }
            $_dockerPathLiteral = & $_psQuote "$_dockerBin;"
            $_pythonLiteral = & $_psQuote $_python3.FilePath
            $_pythonPrefixArgsLiteral = "@(" + (($_python3.PrefixArgs | ForEach-Object { & $_psQuote $_ }) -join ", ") + ")"
            $_agentScriptLiteral = & $_psQuote $agentScript
            $_pidFileLiteral = & $_psQuote $pidFile
            $_installDirLiteral = & $_psQuote $InstallDir
            $_logFileLiteral = & $_psQuote $logFile
            $_agentCommand = @"
`$env:PATH = $_dockerPathLiteral + `$env:PATH
`$agentArgs = $_pythonPrefixArgsLiteral + @($_agentScriptLiteral, '--port', '$port', '--pid-file', $_pidFileLiteral, '--install-dir', $_installDirLiteral)
Set-Location $_installDirLiteral
Start-Process -FilePath $_pythonLiteral -ArgumentList `$agentArgs -WorkingDirectory $_installDirLiteral -WindowStyle Hidden -RedirectStandardError $_logFileLiteral -Wait
"@
            $_encodedAgentCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($_agentCommand))
            try { Stop-ScheduledTask -TaskName $script:ODS_AGENT_TASK_NAME -ErrorAction SilentlyContinue } catch { }
            $taskAction = New-ScheduledTaskAction -Execute "powershell.exe" `
                -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -EncodedCommand $_encodedAgentCommand" `
                -WorkingDirectory $InstallDir
            $taskTrigger = New-ScheduledTaskTrigger -AtLogOn
            $taskSettings = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
            $taskPrincipal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
            Register-ScheduledTask -TaskName $script:ODS_AGENT_TASK_NAME `
                -Action $taskAction -Trigger $taskTrigger -Settings $taskSettings -Principal $taskPrincipal `
                -Description "ODS Host Agent -- manages extensions and bridges dashboard to host" `
                -Force | Out-Null
            Start-ScheduledTask -TaskName $script:ODS_AGENT_TASK_NAME

            Start-Sleep -Seconds 3
            try {
                $resp = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 3 `
                    -UseBasicParsing -ErrorAction SilentlyContinue
                if ($resp.StatusCode -eq 200) {
                    Write-AISuccess "Host agent started (port $port)"
                } else {
                    Write-AIWarn "Host agent started but health check returned $($resp.StatusCode)"
                }
            } catch {
                Write-AIWarn "Host agent started but not yet responding -- check: .\ods.ps1 agent status"
            }
        }
        "stop" {
            try { Stop-ScheduledTask -TaskName $script:ODS_AGENT_TASK_NAME -ErrorAction SilentlyContinue } catch { }
            if (Test-Path $pidFile) {
                try {
                    $_pid = [int](Get-Content $pidFile -Raw).Trim()
                    Stop-Process -Id $_pid -Force -ErrorAction SilentlyContinue
                    Write-AISuccess "Host agent stopped (PID $_pid)"
                } catch {
                    Write-AIWarn "Could not stop agent PID: $_"
                }
                Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
            } else {
                Write-AI "Host agent not running (no PID file)"
            }
            foreach ($_listener in @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) {
                if ($_listener.OwningProcess -gt 0) {
                    Stop-Process -Id ([int]$_listener.OwningProcess) -Force -ErrorAction SilentlyContinue
                }
            }
        }
        "restart" {
            Invoke-Agent -Action "stop"
            Start-Sleep -Seconds 1
            Invoke-Agent -Action "start"
        }
        "logs" {
            if (Test-Path $logFile) {
                Get-Content $logFile -Tail 100 -Wait
            } else {
                Write-AIWarn "No log file at $logFile"
            }
        }
        default {
            Write-Host ""
            Write-Host "  Usage: .\ods.ps1 agent [status|start|stop|restart|logs]" -ForegroundColor DarkGray
            Write-Host ""
        }
    }
}

function Update-ComposeFlags {
    <#
    .SYNOPSIS
        Regenerate .compose-flags after an enable/disable operation.

        Strategy (in priority order):
        1. If scripts/resolve-compose-stack.sh exists and bash is available,
           delegate entirely to the canonical resolver (preserves backend
           overlays, multi-GPU overlays, user-extension overlays, and
           docker-compose.override.yml -- exactly the same stack the installer
           built). This is the safe path.
        2. Otherwise fall back to a minimal in-process swap: keep every token
           in the existing .compose-flags that is NOT an extension service -f
           entry, then re-scan extensions/services for enabled compose.yaml
           fragments and append them. This preserves all backend and GPU
           overlays (--env-file, -f docker-compose.base.yml,
           -f docker-compose.nvidia.yml, etc.) because those paths never
           match 'extensions/services' and are kept verbatim.

        The fallback intentionally mirrors only what the Windows installer
        writes: base + GPU overlay + enabled extension compose.yaml entries.
        It does NOT add GPU-specific per-extension overlays (compose.nvidia.yaml
        etc.) because those are the canonical resolver's responsibility and
        we must not silently diverge from it.
    #>
    $flagsFile = Join-Path $InstallDir ".compose-flags"
    if (-not (Test-Path $flagsFile)) {
        Write-AIWarn "No .compose-flags file found -- skipping regeneration."
        return
    }

    # ── Path 1: delegate to the canonical resolver ────────────────────────────
    $resolverScript = Join-Path (Join-Path $InstallDir "scripts") "resolve-compose-stack.sh"
    $bashExe = Get-Command bash -ErrorAction SilentlyContinue
    if ((Test-Path $resolverScript) -and $bashExe) {
        # Read GPU_BACKEND and TIER from .env so the resolver uses the same
        # parameters that the installer originally selected.
        $gpuBackend = "nvidia"
        $tier = "1"
        try {
            $envMap = Read-ODSEnv
            if ($envMap.ContainsKey("GPU_BACKEND") -and $envMap["GPU_BACKEND"]) {
                $gpuBackend = $envMap["GPU_BACKEND"].ToLower()
            }
            if ($envMap.ContainsKey("TIER") -and $envMap["TIER"]) {
                $tier = $envMap["TIER"]
            }
        } catch { }

        $wslInstallDir = $InstallDir -replace "\\", "/" -replace "^([A-Za-z]):", "/mnt/`$1"
        $wslInstallDir = $wslInstallDir.ToLower() -replace "^/mnt/([a-z])", { "/mnt/$($_.Groups[1].Value.ToLower())" }

        $resolvedFlagsRaw = & $bashExe.Source "$resolverScript" `
            --script-dir "$InstallDir" `
            --gpu-backend "$gpuBackend" `
            --tier "$tier" `
            2>$null
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($resolvedFlagsRaw)) {
            # Prepend --env-file .env if the existing flags had it (the resolver
            # emits only -f flags; the Windows installer adds --env-file separately).
            $existingRaw = (Get-Content $flagsFile -Raw).Trim()
            $newContent = $resolvedFlagsRaw.Trim()
            if ($existingRaw -match '--env-file') {
                $newContent = "--env-file .env " + $newContent
            }
            $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
            [System.IO.File]::WriteAllText($flagsFile, $newContent, $utf8NoBom)
            Write-AI "Updated .compose-flags (via resolve-compose-stack.sh)"
            return
        }
        Write-AIWarn "resolve-compose-stack.sh returned non-zero or empty output; falling back to minimal swap."
    }

    # ── Path 2: minimal in-process swap (fallback) ────────────────────────────
    # Keep all tokens that are NOT an extension service -f entry, then
    # re-append only the enabled compose.yaml fragments.
    # This preserves --env-file, -f docker-compose.base.yml,
    # -f docker-compose.nvidia.yml, and any other backend overlays verbatim.
    $existing = (Get-Content $flagsFile -Raw).Trim() -split "\s+"
    $baseFlags = New-Object System.Collections.Generic.List[string]
    $skipNext = $false
    for ($i = 0; $i -lt $existing.Count; $i++) {
        if ($skipNext) { $skipNext = $false; continue }
        if ($existing[$i] -eq "-f" -and ($i + 1) -lt $existing.Count) {
            $nextVal = $existing[$i + 1]
            # Strip extension service entries (compose.yaml and per-backend
            # overlays such as compose.nvidia.yaml, compose.local.yaml).
            if ($nextVal -match "extensions[/\\]services[/\\]") {
                $skipNext = $true   # also drop the path token that follows -f
                continue
            }
        }
        [void]$baseFlags.Add($existing[$i])
    }

    # Re-append only compose.yaml (the base fragment) for enabled extensions.
    # Per-backend and local-mode overlays require the canonical resolver.
    $extDir = Join-Path (Join-Path $InstallDir "extensions") "services"
    if (Test-Path $extDir) {
        Get-ChildItem -Path $extDir -Directory | Sort-Object Name | ForEach-Object {
            $composePath = Join-Path $_.FullName "compose.yaml"
            if (Test-Path $composePath) {
                $relPath = $composePath.Substring($InstallDir.Length + 1) -replace "\\", "/"
                [void]$baseFlags.Add("-f")
                [void]$baseFlags.Add($relPath)
            }
        }
    }

    $newContent = $baseFlags -join " "
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($flagsFile, $newContent, $utf8NoBom)
    Write-AI "Updated .compose-flags (fallback minimal swap)"
}

function Get-ExtensionServiceDir {
    <#
    .SYNOPSIS
        Resolve the extension service directory for a given service ID.
        Returns $null if not found.
    #>
    param([Parameter(Mandatory=$true)][string]$ServiceId)

    $extDir = Join-Path (Join-Path $InstallDir "extensions") "services"
    if (-not (Test-Path $extDir)) { return $null }

    $svcDir = Join-Path $extDir $ServiceId
    if (Test-Path $svcDir) { return $svcDir }
    return $null
}

function Get-ExtensionCategory {
    <#
    .SYNOPSIS
        Read the category field from manifest.yaml for a service directory.
        Returns empty string if not found or unreadable.
    #>
    param([Parameter(Mandatory=$true)][string]$ServiceDir)

    foreach ($manifestName in @("manifest.yaml", "manifest.yml")) {
        $manifestPath = Join-Path $ServiceDir $manifestName
        if (Test-Path $manifestPath) {
            $line = Get-Content $manifestPath -ErrorAction SilentlyContinue |
                Where-Object { $_ -match "^\s*category:" } |
                Select-Object -First 1
            if ($line) {
                return (($line -split "category:")[1]).Trim().Trim('"').Trim("'")
            }
        }
    }
    return ""
}

function Test-ODSInstallFiles {
    <#
    .SYNOPSIS
        Validate that the ODS install directory and compose files are present.
        Does NOT require Docker Desktop to be running -- intentionally lighter
        than Test-Install so that 'ods enable' works offline.
    #>
    if (-not (Test-Path $InstallDir)) {
        Write-AIError "ODS not found at $InstallDir. Set ODS_HOME or run installer first."
        exit 1
    }
    $baseCompose = Join-Path $InstallDir "docker-compose.base.yml"
    $monoCompose = Join-Path $InstallDir "docker-compose.yml"
    if (-not (Test-Path $baseCompose) -and -not (Test-Path $monoCompose)) {
        Write-AIError "docker-compose.base.yml not found in $InstallDir"
        exit 1
    }
}

function Invoke-Enable {
    <#
    .SYNOPSIS
        Enable an extension service -- mirrors 'ods enable <service>' from the Linux CLI.
        Renames compose.yaml.disabled back to compose.yaml and regenerates .compose-flags.
        Does NOT require Docker Desktop to be running (file-only operation).
    #>
    param([string]$ServiceId)

    # Validate install files only -- Docker is not needed to rename a compose fragment.
    Test-ODSInstallFiles

    if ([string]::IsNullOrWhiteSpace($ServiceId)) {
        Write-AIError "Usage: .\ods.ps1 enable <service>"
        Write-AI "Example: .\ods.ps1 enable comfyui"
        exit 1
    }

    $svcDir = Get-ExtensionServiceDir -ServiceId $ServiceId
    if (-not $svcDir) {
        Write-AIError "Unknown extension service: '$ServiceId'"
        Write-AI "Check available services under: $(Join-Path (Join-Path $InstallDir 'extensions') 'services')"
        exit 1
    }

    $category = Get-ExtensionCategory -ServiceDir $svcDir
    if ($category -eq "core") {
        Write-AISuccess "$ServiceId is a core service (always enabled)."
        return
    }

    $composePath  = Join-Path $svcDir "compose.yaml"
    $disabledPath = Join-Path $svcDir "compose.yaml.disabled"

    if (Test-Path $composePath) {
        Write-AISuccess "$ServiceId is already enabled."
        Write-AI "Run '.\ods.ps1 start $ServiceId' to launch it."
        return
    }

    if (Test-Path $disabledPath) {
        Rename-Item -LiteralPath $disabledPath -NewName "compose.yaml" -Force
        Update-ComposeFlags
        Write-AISuccess "$ServiceId enabled."
        Write-AI "Run '.\ods.ps1 start $ServiceId' to launch it."
        return
    }

    Write-AIError "No compose fragment found for '$ServiceId' (expected compose.yaml or compose.yaml.disabled)."
    Write-AI "This may be a core service or the extension is not installed."
    exit 1
}

function Invoke-Disable {
    <#
    .SYNOPSIS
        Disable an extension service -- mirrors 'ods disable <service>' from the Linux CLI.
        Stops the running container when Docker is available, then renames
        compose.yaml to compose.yaml.disabled and regenerates .compose-flags.
        The file/cache changes always run even when Docker Desktop is offline.
    #>
    param([string]$ServiceId)

    # Validate install files only -- Docker stop is best-effort below.
    Test-ODSInstallFiles

    if ([string]::IsNullOrWhiteSpace($ServiceId)) {
        Write-AIError "Usage: .\ods.ps1 disable <service>"
        Write-AI "Example: .\ods.ps1 disable comfyui"
        exit 1
    }

    $svcDir = Get-ExtensionServiceDir -ServiceId $ServiceId
    if (-not $svcDir) {
        Write-AIError "Unknown extension service: '$ServiceId'"
        Write-AI "Check available services under: $(Join-Path (Join-Path $InstallDir 'extensions') 'services')"
        exit 1
    }

    $category = Get-ExtensionCategory -ServiceDir $svcDir
    if ($category -eq "core") {
        Write-AIError "Cannot disable core service: $ServiceId"
        exit 1
    }

    $composePath  = Join-Path $svcDir "compose.yaml"
    $disabledPath = Join-Path $svcDir "compose.yaml.disabled"

    if (Test-Path $disabledPath) {
        Write-AISuccess "$ServiceId is already disabled."
        return
    }

    if (-not (Test-Path $composePath)) {
        Write-AIError "No compose fragment found for '$ServiceId'."
        exit 1
    }

    # Best-effort container stop -- skip gracefully when Docker Desktop is
    # offline so the rename + flags update always succeeds.
    $dockerRunning = $false
    try { $null = docker info 2>$null; $dockerRunning = ($LASTEXITCODE -eq 0) } catch { }
    if ($dockerRunning) {
        $flags = Get-ComposeFlags
        Write-AI "Stopping $ServiceId..."
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        & docker compose @flags stop $ServiceId 2>$null
        $ErrorActionPreference = $prevEAP
    } else {
        Write-AIWarn "Docker Desktop is not running -- skipping container stop. $ServiceId will be excluded from the next 'ods start'."
    }

    # Rename and refresh flags regardless of Docker state.
    Rename-Item -LiteralPath $composePath -NewName "compose.yaml.disabled" -Force
    Update-ComposeFlags
    Write-AISuccess "$ServiceId disabled."
    Write-AI "Data preserved. Run '.\ods.ps1 enable $ServiceId' to re-enable."
}

function Show-Help {
    Write-Host ""
    Write-Host "  ODS CLI (Windows)" -ForegroundColor Green
    Write-Host "  Version $($script:ODS_VERSION)" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  USAGE" -ForegroundColor White
    Write-Host "    .\ods.ps1 <command> [options]" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  COMMANDS" -ForegroundColor White
    Write-Host "    status              " -ForegroundColor Cyan -NoNewline
    Write-Host "Health checks + GPU status" -ForegroundColor DarkGray
    Write-Host "    start [service]     " -ForegroundColor Cyan -NoNewline
    Write-Host "Start all or one service" -ForegroundColor DarkGray
    Write-Host "    stop [service]      " -ForegroundColor Cyan -NoNewline
    Write-Host "Stop all or one service" -ForegroundColor DarkGray
    Write-Host "    restart [service]   " -ForegroundColor Cyan -NoNewline
    Write-Host "Restart all or one service" -ForegroundColor DarkGray
    Write-Host "    logs <svc> [lines]  " -ForegroundColor Cyan -NoNewline
    Write-Host "Tail logs (default 100)" -ForegroundColor DarkGray
    Write-Host "    config show         " -ForegroundColor Cyan -NoNewline
    Write-Host "View .env (secrets masked)" -ForegroundColor DarkGray
    Write-Host "    config edit         " -ForegroundColor Cyan -NoNewline
    Write-Host "Open .env in notepad" -ForegroundColor DarkGray
    Write-Host "    chat `"message`"      " -ForegroundColor Cyan -NoNewline
    Write-Host "Quick chat via API" -ForegroundColor DarkGray
    Write-Host "    update              " -ForegroundColor Cyan -NoNewline
    Write-Host "Pull latest images and restart" -ForegroundColor DarkGray
    Write-Host "    doctor              " -ForegroundColor Cyan -NoNewline
    Write-Host "Diagnose runtime readiness" -ForegroundColor DarkGray
    Write-Host "    repair voice        " -ForegroundColor Cyan -NoNewline
    Write-Host "Start voice services and cache STT model" -ForegroundColor DarkGray
    Write-Host "    enable <service>    " -ForegroundColor Cyan -NoNewline
    Write-Host "Enable an extension service (e.g. comfyui, langfuse)" -ForegroundColor DarkGray
    Write-Host "    disable <service>   " -ForegroundColor Cyan -NoNewline
    Write-Host "Disable an extension service" -ForegroundColor DarkGray
    Write-Host "    agent [action]      " -ForegroundColor Cyan -NoNewline
    Write-Host "Host agent: status|start|stop|restart|logs" -ForegroundColor DarkGray
    Write-Host "    report              " -ForegroundColor Cyan -NoNewline
    Write-Host "Generate Windows diagnostics bundle" -ForegroundColor DarkGray
    Write-Host "    version             " -ForegroundColor Cyan -NoNewline
    Write-Host "Show version" -ForegroundColor DarkGray
    Write-Host "    help                " -ForegroundColor Cyan -NoNewline
    Write-Host "Show this help" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  EXAMPLES" -ForegroundColor White
    Write-Host "    .\ods.ps1 status" -ForegroundColor DarkGray
    Write-Host "    .\ods.ps1 logs llama-server 50" -ForegroundColor DarkGray
    Write-Host "    .\ods.ps1 restart open-webui" -ForegroundColor DarkGray
    Write-Host "    .\ods.ps1 repair voice" -ForegroundColor DarkGray
    Write-Host "    .\ods.ps1 enable comfyui" -ForegroundColor DarkGray
    Write-Host "    .\ods.ps1 disable langfuse" -ForegroundColor DarkGray
    Write-Host "    .\ods.ps1 chat `"What is quantum computing?`"" -ForegroundColor DarkGray
    Write-Host ""
}

# ============================================================================
# Command Dispatch
# ============================================================================

switch ($Command.ToLower()) {
    "status"  { Invoke-Status }
    "start"   { Invoke-Start -Service ($Arguments | Select-Object -First 1) }
    "stop"    { Invoke-Stop -Service ($Arguments | Select-Object -First 1) }
    "restart" { Invoke-Restart -Service ($Arguments | Select-Object -First 1) }
    "logs"    {
        $svc = $Arguments | Select-Object -First 1
        # Validate the optional line count instead of a bare [int] cast, which
        # throws an unhandled .NET conversion error on non-numeric input
        # (e.g. `ods logs llama-server 5m`). Mirrors the [int]::TryParse guard
        # used elsewhere in this script; the Unix CLIs pass the value straight
        # to `docker compose --tail`, which rejects bad input gracefully too.
        $n = 100
        if ($Arguments.Count -ge 2) {
            $parsedLines = 0
            if ([int]::TryParse([string]$Arguments[1], [ref]$parsedLines) -and $parsedLines -gt 0) {
                $n = $parsedLines
            } else {
                Write-AIWarn "Invalid line count '$($Arguments[1])'; using $n."
            }
        }
        Invoke-Logs -Service $svc -Lines $n
    }
    "config"  {
        $action = ($Arguments | Select-Object -First 1)
        if ($action -eq "edit") {
            Test-Install
            & notepad (Join-Path $InstallDir ".env")
        } else {
            Invoke-ConfigShow
        }
    }
    "chat"    { Invoke-Chat -Message ($Arguments -join " ") }
    "update"  { Invoke-Update }
    "doctor"  { Invoke-Doctor }
    "repair"  { Invoke-Repair -Target ($Arguments | Select-Object -First 1) }
    "enable"  { Invoke-Enable -ServiceId ($Arguments | Select-Object -First 1) }
    "disable" { Invoke-Disable -ServiceId ($Arguments | Select-Object -First 1) }
    "report"  { Invoke-Report }
    "agent"   {
        $action = ($Arguments | Select-Object -First 1)
        if (-not $action) { $action = "status" }
        Invoke-Agent -Action $action
    }
    "version" { Write-Host "ODS v$($script:ODS_VERSION) (Windows)" -ForegroundColor Green }
    "help"    { Show-Help }
    default   {
        Write-AIWarn "Unknown command: $Command"
        Show-Help
    }
}
