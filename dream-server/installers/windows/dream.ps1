# ============================================================================
# Dream Server Windows CLI -- dream.ps1
# ============================================================================
# Day-to-day management of a Dream Server installation on Windows.
# Mirrors the Linux dream-cli command structure.
#
# Usage:
#   .\dream.ps1 status              # Health checks + GPU status
#   .\dream.ps1 start [service]     # Start all or one service
#   .\dream.ps1 stop [service]      # Stop all or one service
#   .\dream.ps1 restart [service]   # Restart all or one service
#   .\dream.ps1 logs <service> [N]  # Tail logs (default 100 lines)
#   .\dream.ps1 config show         # View .env (secrets masked)
#   .\dream.ps1 config edit         # Open .env in notepad
#   .\dream.ps1 chat "message"      # Quick chat via API
#   .\dream.ps1 update              # Pull latest images and restart
#   .\dream.ps1 doctor              # Diagnose runtime readiness
#   .\dream.ps1 repair voice        # Repair voice/STT/TTS readiness
#   .\dream.ps1 report              # Generate Windows diagnostics bundle
#   .\dream.ps1 version             # Show version
#   .\dream.ps1 help                # Show help
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
. (Join-Path $LibDir "detection.ps1")
. (Join-Path $LibDir "llm-endpoint.ps1")
. (Join-Path $LibDir "install-report.ps1")

# ── Resolve install directory ──
$InstallDir = $script:DS_INSTALL_DIR

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
        Write-AIError "Dream Server not found at $InstallDir. Set DREAM_HOME or run installer first."
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

function Read-DreamEnv {
    <#
    .SYNOPSIS
        Safely load .env file into a hashtable (no eval, no injection).
    #>
    return Get-WindowsDreamEnvMap -InstallDir $InstallDir
}

function Get-DreamEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$Default = ""
    )
    try {
        $envMap = Read-DreamEnv
        if ($envMap.ContainsKey($Name) -and -not [string]::IsNullOrWhiteSpace($envMap[$Name])) {
            return $envMap[$Name]
        }
    } catch { }
    return $Default
}

function Invoke-HermesSoulRefresh {
    <#
    .SYNOPSIS
        Render data/persona/SOUL.md and optionally copy it into dream-hermes.
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
                & $cmd.Source @($candidate.Args) $builder "--template" $template "--env" $envPath "--output" $output *>> $script:DS_LOG_FILE
                if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $output -PathType Leaf)) {
                    $rendered = $true
                    break
                }
            } catch {
                Add-Content -Path $script:DS_LOG_FILE -Value "Hermes SOUL.md refresh failed with $($candidate.Command): $($_.Exception.Message)"
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
        if ($names -contains "dream-hermes") {
            & docker exec dream-hermes cp /opt/hermes/docker/SOUL.md /opt/data/SOUL.md *>> $script:DS_LOG_FILE
            if ($LASTEXITCODE -eq 0) {
                Write-AISuccess "Synced Hermes SOUL.md"
            } else {
                Write-AIWarn "Could not sync Hermes SOUL.md into running container"
            }
        }
    }
}

function Get-DreamVoiceDiagnosis {
    $whisperPort = Get-DreamEnvValue -Name "WHISPER_PORT" -Default "9000"
    $whisperUrl = "http://localhost:$whisperPort"
    $sttModel = Get-DreamEnvValue -Name "AUDIO_STT_MODEL" -Default "Systran/faster-whisper-base"
    $sttModelEncoded = $sttModel -replace "/", "%2F"
    $modelUrl = "$whisperUrl/v1/models/$sttModelEncoded"
    $ttsPort = Get-DreamEnvValue -Name "TTS_PORT" -Default "8880"
    $ttsUrl = "http://localhost:$ttsPort"

    $result = [ordered]@{
        WhisperPort      = $whisperPort
        WhisperUrl       = $whisperUrl
        WhisperHealthy   = $false
        ModelsApiReady   = $false
        SttModel         = $sttModel
        SttModelCached   = $false
        SttModelUrl      = $modelUrl
        RecoveryCommand  = "Invoke-WebRequest -Method POST -Uri '$modelUrl' -TimeoutSec 3600"
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

function Test-DreamSttModelCache {
    try {
        $flags = Get-ComposeFlags
        if (-not (Test-DreamComposeServiceAvailable -ComposeFlags $flags -Service "whisper")) {
            return
        }
    } catch { }

    $diag = Get-DreamVoiceDiagnosis
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

function Wait-DreamHttpOk {
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

function Test-DreamComposeServiceAvailable {
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

function Write-DreamMissingComposeServiceHint {
    param(
        [string[]]$ComposeFlags,
        [Parameter(Mandatory = $true)][string]$Service
    )

    Write-AIError "Service '$Service' is not in the active Dream Server compose stack."

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

function Set-DreamEnvValue {
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

    $envVars = Read-DreamEnv
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
        Set-DreamEnvValue -Key "LLAMA_CPU_LIMIT" -Value $llamaCpuLimit
        $changed = $true
    }
    if ($envVars["LLAMA_CPU_RESERVATION"] -ne $llamaCpuReservation) {
        Set-DreamEnvValue -Key "LLAMA_CPU_RESERVATION" -Value $llamaCpuReservation
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
            Set-DreamEnvValue -Key $limitKey -Value $finalLimit
            $serviceChanged = $true
        }
        if ($envVars[$reservationKey] -ne $finalReservation) {
            Set-DreamEnvValue -Key $reservationKey -Value $finalReservation
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
    $env = Read-DreamEnv
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
    $backend = Get-NativeInferenceBackend
    $result = @{ Running = $false; Pid = 0; Healthy = $false; Backend = $backend }

    if (-not (Test-Path $script:INFERENCE_PID_FILE)) { return $result }

    $savedPid = [int](Get-Content $script:INFERENCE_PID_FILE -Raw).Trim()
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
    $envVars = Read-DreamEnv

    # Honour the unified BIND_ADDRESS knob (PR #964); empty/missing → loopback.
    $bindAddr = $envVars["BIND_ADDRESS"]
    if ([string]::IsNullOrWhiteSpace($bindAddr)) { $bindAddr = "127.0.0.1" }

    if ($backend -eq "lemonade") {
        $modelsDir = Join-Path (Join-Path $InstallDir "data") "models"
        $lemonadeArgs = @(
            "serve",
            "--port", "$($script:LEMONADE_PORT)",
            "--host", $bindAddr,
            "--no-tray",
            "--llamacpp", "vulkan",
            "--extra-models-dir", $modelsDir
        )
        $pidDir = Split-Path $script:INFERENCE_PID_FILE
        New-Item -ItemType Directory -Path $pidDir -Force | Out-Null

        $proc = Start-Process -FilePath $script:LEMONADE_EXE `
            -ArgumentList $lemonadeArgs -WindowStyle Hidden -PassThru
        Set-Content -Path $script:INFERENCE_PID_FILE -Value $proc.Id

        Write-AISuccess "Lemonade server started (PID $($proc.Id))"
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
        Write-Host "  Dream Server Status" -ForegroundColor Cyan
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
            $resp = Invoke-WebRequest -Uri $script:DREAM_AGENT_HEALTH_URL `
                -TimeoutSec 3 -UseBasicParsing -ErrorAction SilentlyContinue
            if ($resp.StatusCode -eq 200) {
                Write-AISuccess "Host Agent: running (port $($script:DREAM_AGENT_PORT))"
            } else {
                Write-AIWarn "Host Agent: responded with $($resp.StatusCode)"
            }
        } catch {
            Write-AIWarn "Host Agent: not responding (port $($script:DREAM_AGENT_PORT))"
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
        Test-DreamSttModelCache

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
        $hermesInStack = Test-DreamComposeServiceAvailable -ComposeFlags $flags -Service "hermes"
        if ($Service) {
            if (-not (Test-DreamComposeServiceAvailable -ComposeFlags $flags -Service $Service)) {
                Write-DreamMissingComposeServiceHint -ComposeFlags $flags -Service $Service
                exit 1
            }
            Write-AI "Starting $Service..."
            if ($Service -eq "hermes" -and $hermesInStack) {
                Invoke-HermesSoulRefresh
            }
            $composeExit = Invoke-DreamDockerCompose -InstallDir $InstallDir -ComposeFlags $flags `
                -ComposeArgs @("up", "-d", $Service)
            if ($composeExit -ne 0) {
                Write-AIError "docker compose up failed (exit code: $composeExit)"
                Write-DreamComposeDiagnostics -InstallDir $InstallDir -ComposeFlags $flags `
                    -Phase "dream.ps1 start ($Service)"
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
            $composeExit = Invoke-DreamDockerCompose -InstallDir $InstallDir -ComposeFlags $flags `
                -ComposeArgs @("up", "-d")
            if ($composeExit -ne 0) {
                Write-AIError "docker compose up failed (exit code: $composeExit)"
                Write-DreamComposeDiagnostics -InstallDir $InstallDir -ComposeFlags $flags -Phase "dream.ps1 start (all)"
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
    Test-Install
    Push-Location $InstallDir
    try {
        $flags = Get-ComposeFlags
        if ($Service) {
            if (-not (Test-DreamComposeServiceAvailable -ComposeFlags $flags -Service $Service)) {
                Write-DreamMissingComposeServiceHint -ComposeFlags $flags -Service $Service
                exit 1
            }
            Write-AI "Stopping $Service..."
            $composeExit = Invoke-DreamDockerCompose -InstallDir $InstallDir -ComposeFlags $flags `
                -ComposeArgs @("stop", $Service)
            if ($composeExit -ne 0) {
                Write-AIError "docker compose stop failed (exit code: $composeExit)"
                Write-DreamComposeDiagnostics -InstallDir $InstallDir -ComposeFlags $flags `
                    -Phase "dream.ps1 stop ($Service)"
                exit 1
            }
            Write-AISuccess "$Service stopped"
        } else {
            Write-AI "Stopping all services..."
            $composeExit = Invoke-DreamDockerCompose -InstallDir $InstallDir -ComposeFlags $flags `
                -ComposeArgs @("down")
            if ($composeExit -ne 0) {
                Write-AIError "docker compose down failed (exit code: $composeExit)"
                Write-DreamComposeDiagnostics -InstallDir $InstallDir -ComposeFlags $flags -Phase "dream.ps1 stop (all)"
                exit 1
            }

            # Stop native inference server (AMD path)
            if (Test-Path $script:INFERENCE_PID_FILE) {
                Stop-NativeInferenceServer
            }

            # Stop host agent
            Invoke-Agent -Action "stop"

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
        $hermesInStack = Test-DreamComposeServiceAvailable -ComposeFlags $flags -Service "hermes"
        if ($Service) {
            if (-not (Test-DreamComposeServiceAvailable -ComposeFlags $flags -Service $Service)) {
                Write-DreamMissingComposeServiceHint -ComposeFlags $flags -Service $Service
                exit 1
            }
            Write-AI "Restarting $Service..."
            if ($Service -eq "hermes" -and $hermesInStack) {
                Invoke-HermesSoulRefresh
            }
            $composeExit = Invoke-DreamDockerCompose -InstallDir $InstallDir -ComposeFlags $flags `
                -ComposeArgs @("restart", $Service)
            if ($composeExit -ne 0) {
                Write-AIError "docker compose restart failed (exit code: $composeExit)"
                Write-DreamComposeDiagnostics -InstallDir $InstallDir -ComposeFlags $flags `
                    -Phase "dream.ps1 restart ($Service)"
                exit 1
            }
            Write-AISuccess "$Service restarted"
            if ($Service -eq "hermes" -and $hermesInStack) {
                Invoke-HermesSoulRefresh -SyncContainer
            }
        } else {
            # For AMD, also restart native inference server
            if (Test-Path $script:INFERENCE_PID_FILE) {
                Stop-NativeInferenceServer
                Start-NativeInferenceServer
            }
            if ($hermesInStack) {
                Invoke-HermesSoulRefresh
            }
            Write-AI "Restarting all services..."
            $composeExit = Invoke-DreamDockerCompose -InstallDir $InstallDir -ComposeFlags $flags `
                -ComposeArgs @("restart")
            if ($composeExit -ne 0) {
                Write-AIError "docker compose restart failed (exit code: $composeExit)"
                Write-DreamComposeDiagnostics -InstallDir $InstallDir -ComposeFlags $flags -Phase "dream.ps1 restart (all)"
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
        Write-AI "Usage: .\dream.ps1 logs <service> [lines]"
        Write-AI "Services: llama-server, open-webui, dashboard-api, n8n, whisper, tts, ..."
        return
    }
    Test-Install
    Push-Location $InstallDir
    try {
        $flags = Get-ComposeFlags
        if (-not (Test-DreamComposeServiceAvailable -ComposeFlags $flags -Service $Service)) {
            Write-DreamMissingComposeServiceHint -ComposeFlags $flags -Service $Service
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
        if ($line -match "(SECRET|PASS|TOKEN|KEY)=") {
            $key = ($line -split "=")[0]
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
        Write-AI "Usage: .\dream.ps1 chat `"your message`""
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
        Write-AI "Is llama-server running? Try: .\dream.ps1 status"
    }
}

function Invoke-Update {
    Test-Install
    Push-Location $InstallDir
    try {
        Ensure-LlamaCpuBudget

        $flags = Get-ComposeFlags
        Write-AI "Pulling latest images..."
        $pullExit = Invoke-DreamDockerCompose -InstallDir $InstallDir -ComposeFlags $flags -ComposeArgs @("pull")
        if ($pullExit -ne 0) {
            Write-AIError "docker compose pull failed (exit code: $pullExit)"
            Write-DreamComposeDiagnostics -InstallDir $InstallDir -ComposeFlags $flags -Phase "dream.ps1 update (pull)"
            exit 1
        }
        Write-AI "Recreating containers..."
        $upExit = Invoke-DreamDockerCompose -InstallDir $InstallDir -ComposeFlags $flags `
            -ComposeArgs @("up", "-d", "--force-recreate")
        if ($upExit -ne 0) {
            Write-AIError "docker compose up failed (exit code: $upExit)"
            Write-DreamComposeDiagnostics -InstallDir $InstallDir -ComposeFlags $flags -Phase "dream.ps1 update (up --force-recreate)"
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
        Write-DreamInstallReport -InstallDir $InstallDir -ComposeFlags $flags | Out-Null
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
            (Test-DreamComposeServiceAvailable -ComposeFlags $flags -Service "whisper") -and
            (Test-DreamComposeServiceAvailable -ComposeFlags $flags -Service "tts")
        )

        Write-Host ""
        Write-Host "  Dream Doctor" -ForegroundColor Cyan
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

        $voice = Get-DreamVoiceDiagnosis
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
            Write-Host "  Repair: .\dream.ps1 repair voice" -ForegroundColor DarkGray
            Write-Host "  Manual: $($voice.RecoveryCommand)" -ForegroundColor DarkGray
            $hasIssue = $true
        }

        if ($voice.TtsHealthy) {
            Write-AISuccess "Kokoro TTS: healthy ($($voice.TtsUrl))"
        } else {
            Write-AIWarn "Kokoro TTS: not responding ($($voice.TtsUrl))"
            Write-Host "  Repair: .\dream.ps1 repair voice" -ForegroundColor DarkGray
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
            if (-not (Test-DreamComposeServiceAvailable -ComposeFlags $flags -Service $svc)) {
                $missingServices += $svc
            }
        }
        if ($missingServices.Count -gt 0) {
            Write-AIError "Voice services are not in this compose stack: $($missingServices -join ', ')"
            Write-AI "Enable voice in the installer or add the whisper/tts extension compose files, then retry."
            exit 1
        }

        Write-AI "Starting Whisper and Kokoro TTS..."
        $composeExit = Invoke-DreamDockerCompose -InstallDir $InstallDir -ComposeFlags $flags `
            -ComposeArgs @("up", "-d", "whisper", "tts")
        if ($composeExit -ne 0) {
            Write-AIError "docker compose up failed (exit code: $composeExit)"
            Write-DreamComposeDiagnostics -InstallDir $InstallDir -ComposeFlags $flags -Phase "dream.ps1 repair voice"
            exit 1
        }

        $voice = Get-DreamVoiceDiagnosis
        if (-not $voice.WhisperHealthy) {
            Write-AI "Waiting for Whisper STT..."
            Wait-DreamHttpOk -Url "$($voice.WhisperUrl)/health" -TimeoutSeconds 90 | Out-Null
        }
        if (-not $voice.TtsHealthy) {
            Write-AI "Waiting for Kokoro TTS..."
            Wait-DreamHttpOk -Url "$($voice.TtsUrl)/health" -TimeoutSeconds 90 | Out-Null
        }

        $voice = Get-DreamVoiceDiagnosis
        if (-not $voice.WhisperHealthy) {
            Write-AIError "Whisper STT is still not responding. Check: .\dream.ps1 logs whisper 100"
            exit 1
        }
        if (-not $voice.SttModelCached) {
            Write-AI "Downloading STT model ($($voice.SttModel))..."
            try {
                Invoke-WebRequest -Method POST -Uri $voice.SttModelUrl -TimeoutSec 3600 -UseBasicParsing -ErrorAction Stop | Out-Null
            } catch {
                Write-AIWarn "STT model download request failed; verifying cache before failing."
            }
        }

        $voice = Get-DreamVoiceDiagnosis
        if ($voice.SttModelCached) {
            Write-AISuccess "Whisper STT model cached ($($voice.SttModel))"
        } else {
            Write-AIError "STT model is still missing. Run manually: $($voice.RecoveryCommand)"
            exit 1
        }

        if ($voice.TtsHealthy) {
            Write-AISuccess "Kokoro TTS healthy"
        } else {
            Write-AIError "Kokoro TTS is still not responding. Check: .\dream.ps1 logs tts 100"
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
            Write-AI "Usage: .\dream.ps1 repair voice"
            Write-AIWarn "Unknown repair target: $Target"
            exit 1
        }
    }
}

function Invoke-Agent {
    param([string]$Action = "status")

    $agentScript = Join-Path (Join-Path $InstallDir "bin") "dream-host-agent.py"
    $pidFile     = $script:DREAM_AGENT_PID_FILE
    $logFile     = $script:DREAM_AGENT_LOG_FILE
    $port        = $script:DREAM_AGENT_PORT
    $healthUrl   = $script:DREAM_AGENT_HEALTH_URL

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
            $_python3 = Get-Command python3 -ErrorAction SilentlyContinue
            if (-not $_python3) { $_python3 = Get-Command python -ErrorAction SilentlyContinue }
            if (-not $_python3) {
                Write-AIError "Python not found in PATH -- install Python 3 and try again"
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

            # Start agent through a hidden PowerShell host so manual starts do
            # not leave a visible cmd.exe window. Prepend Docker to PATH so the
            # agent can find docker.exe (Docker Desktop may not be in the
            # system PATH yet after fresh install).
            $_dockerBin = "C:\Program Files\Docker\Docker\resources\bin"
            $_psQuote = {
                param([string]$Value)
                "'" + ($Value -replace "'", "''") + "'"
            }
            $_dockerPathLiteral = & $_psQuote "$_dockerBin;"
            $_pythonLiteral = & $_psQuote $_python3.Source
            $_agentScriptLiteral = & $_psQuote $agentScript
            $_pidFileLiteral = & $_psQuote $pidFile
            $_installDirLiteral = & $_psQuote $InstallDir
            $_logFileLiteral = & $_psQuote $logFile
            $_agentCommand = @"
`$env:PATH = $_dockerPathLiteral + `$env:PATH
`$agentArgs = @($_agentScriptLiteral, '--port', '$port', '--pid-file', $_pidFileLiteral, '--install-dir', $_installDirLiteral)
Start-Process -FilePath $_pythonLiteral -ArgumentList `$agentArgs -WorkingDirectory $_installDirLiteral -WindowStyle Hidden -RedirectStandardError $_logFileLiteral
"@
            $_encodedAgentCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($_agentCommand))
            Start-Process -FilePath "powershell.exe" `
                -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-EncodedCommand", $_encodedAgentCommand `
                -WindowStyle Hidden -WorkingDirectory $InstallDir

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
                Write-AIWarn "Host agent started but not yet responding -- check: .\dream.ps1 agent status"
            }
        }
        "stop" {
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
            Write-Host "  Usage: .\dream.ps1 agent [status|start|stop|restart|logs]" -ForegroundColor DarkGray
            Write-Host ""
        }
    }
}

function Show-Help {
    Write-Host ""
    Write-Host "  Dream Server CLI (Windows)" -ForegroundColor Green
    Write-Host "  Version $($script:DS_VERSION)" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  USAGE" -ForegroundColor White
    Write-Host "    .\dream.ps1 <command> [options]" -ForegroundColor DarkGray
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
    Write-Host "    .\dream.ps1 status" -ForegroundColor DarkGray
    Write-Host "    .\dream.ps1 logs llama-server 50" -ForegroundColor DarkGray
    Write-Host "    .\dream.ps1 restart open-webui" -ForegroundColor DarkGray
    Write-Host "    .\dream.ps1 repair voice" -ForegroundColor DarkGray
    Write-Host "    .\dream.ps1 chat `"What is quantum computing?`"" -ForegroundColor DarkGray
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
        $n = $(if ($Arguments.Count -ge 2) { [int]$Arguments[1] } else { 100 })
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
    "report"  { Invoke-Report }
    "agent"   {
        $action = ($Arguments | Select-Object -First 1)
        if (-not $action) { $action = "status" }
        Invoke-Agent -Action $action
    }
    "version" { Write-Host "Dream Server v$($script:DS_VERSION) (Windows)" -ForegroundColor Green }
    "help"    { Show-Help }
    default   {
        Write-AIWarn "Unknown command: $Command"
        Show-Help
    }
}
