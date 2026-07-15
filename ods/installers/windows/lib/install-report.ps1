# ============================================================================
# ODS Windows -- install report generator
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Build a shareable support bundle with compose + system diagnostics.
# Requires: ui.ps1, detection.ps1, and llm-endpoint.ps1 sourced first.
# ============================================================================

function Invoke-OptionalCommand {
    param(
        [string]$Command,
        [string[]]$CommandArgs = @(),
        [int]$MaxLines = 120
    )

    $result = @{
        ok        = $false
        exit_code = -1
        lines     = @()
    }

    try {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        $output = & $Command @CommandArgs 2>&1 | ForEach-Object { $_.ToString() }
        $exitCode = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP

        $result.ok = ($exitCode -eq 0)
        $result.exit_code = $exitCode
        if ($output) {
            $result.lines = @($output | Select-Object -First $MaxLines)
        }
        return $result
    } catch {
        $result.lines = @("command failed to execute: $($_.Exception.Message)")
        return $result
    }
}

function Test-HttpEndpoint {
    param(
        [string]$Url,
        [int]$TimeoutSec = 3
    )

    $status = @{
        url = $Url
        ok = $false
        status_code = 0
        error = ""
    }

    try {
        $resp = Invoke-WebRequest -Uri $Url -TimeoutSec $TimeoutSec -UseBasicParsing -ErrorAction SilentlyContinue
        if ($resp) {
            $status.status_code = [int]$resp.StatusCode
            $status.ok = ($status.status_code -ge 200 -and $status.status_code -lt 400)
        }
    } catch {
        $status.error = $_.Exception.Message
    }

    return $status
}

function New-ODSInstallReport {
    param(
        [string]$InstallDir = $script:ODS_INSTALL_DIR,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]]$ComposeFlags
    )

    $gpu = Get-GpuInfo
    $computer = Get-CimInstance Win32_ComputerSystem
    $os = Get-CimInstance Win32_OperatingSystem
    $ramBytes = [int64]$computer.TotalPhysicalMemory

    $nativeBackend = "none"
    if (Get-Command Get-NativeInferenceBackend -ErrorAction SilentlyContinue) {
        $nativeBackend = Get-NativeInferenceBackend
    }
    $envMap = Get-WindowsODSEnvMap -InstallDir $InstallDir
    $llmEndpoint = Get-WindowsLocalLlmEndpoint -InstallDir $InstallDir -GpuBackend $gpu.Backend -NativeBackend $nativeBackend

    $composeConfigArgs = @("compose") + $ComposeFlags + @("config")
    $composePsArgs = @("compose") + $ComposeFlags + @("ps", "-a")

    $whisperPort = if ($envMap.ContainsKey("WHISPER_PORT") -and $envMap["WHISPER_PORT"]) { $envMap["WHISPER_PORT"] } else { "9000" }
    $sttModel = if ($envMap.ContainsKey("AUDIO_STT_MODEL") -and $envMap["AUDIO_STT_MODEL"]) { $envMap["AUDIO_STT_MODEL"] } else { "Systran/faster-whisper-base" }
    $sttModelEncoded = $sttModel -replace "/", "%2F"
    $sttModelUrl = "http://localhost:$whisperPort/v1/models/$sttModelEncoded"
    $whisperStatus = Test-HttpEndpoint -Url "http://localhost:$whisperPort/health"
    $sttModelStatus = Test-HttpEndpoint -Url $sttModelUrl -TimeoutSec 5
    $sttModelCached = if ($whisperStatus.ok) { $sttModelStatus.ok } else { $null }

    $report = [ordered]@{
        generated_at = (Get-Date).ToString("o")
        platform = [ordered]@{
            os_caption = $os.Caption
            os_version = $os.Version
            os_build = $os.BuildNumber
            computer_name = $env:COMPUTERNAME
            ram_gb = [Math]::Round($ramBytes / 1GB, 2)
        }
        gpu = [ordered]@{
            backend = $gpu.Backend
            name = $gpu.Name
            vram_mb = $gpu.VramMB
            memory_type = $gpu.MemoryType
            native_inference_backend = $nativeBackend
        }
        compose = [ordered]@{
            flags = @($ComposeFlags)
            docker_version = Invoke-OptionalCommand -Command "docker" -CommandArgs @("version") -MaxLines 40
            docker_info = Invoke-OptionalCommand -Command "docker" -CommandArgs @("info") -MaxLines 80
            compose_config = Invoke-OptionalCommand -Command "docker" -CommandArgs $composeConfigArgs
            compose_ps = Invoke-OptionalCommand -Command "docker" -CommandArgs $composePsArgs -MaxLines 80
        }
        health = [ordered]@{
            llm_api = Test-HttpEndpoint -Url $llmEndpoint.HealthUrl
            open_webui = Test-HttpEndpoint -Url "http://localhost:3000"
            dashboard = Test-HttpEndpoint -Url "http://localhost:3001"
            dashboard_api = Test-HttpEndpoint -Url "http://localhost:3002/health"
            whisper = $whisperStatus
            stt_model = [ordered]@{
                name = $sttModel
                cached = $sttModelCached
                status_code = $sttModelStatus.status_code
                url = $sttModelUrl
                recovery_command = "Invoke-WebRequest -Method POST -Uri '$sttModelUrl' -TimeoutSec 3600"
            }
        }
    }

    return $report
}

function Write-ODSInstallReport {
    param(
        [Parameter(Mandatory = $true)]
        [string]$InstallDir,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]]$ComposeFlags
    )

    $artifactsDir = Join-Path (Join-Path $InstallDir "artifacts") "windows-report"
    New-Item -ItemType Directory -Path $artifactsDir -Force | Out-Null

    $jsonPath = Join-Path $artifactsDir "report.json"
    $txtPath = Join-Path $artifactsDir "report.txt"

    $report = New-ODSInstallReport -InstallDir $InstallDir -ComposeFlags $ComposeFlags
    ($report | ConvertTo-Json -Depth 8) | Set-Content -Path $jsonPath -Encoding UTF8

    $lines = @()
    $lines += "ODS Windows Report"
    $lines += "Generated: $($report.generated_at)"
    $lines += ""
    $lines += "Privacy"
    $lines += "- docker compose config output (inside report.json) can include interpolated env values from .env -- API keys, tokens, or other secrets. Review and redact before sharing publicly."
    $lines += ""
    $lines += "Platform"
    $lines += "- OS: $($report.platform.os_caption) ($($report.platform.os_version), build $($report.platform.os_build))"
    $lines += "- Machine: $($report.platform.computer_name)"
    $lines += "- RAM: $($report.platform.ram_gb) GB"
    $lines += ""
    $lines += "GPU"
    $lines += "- Backend: $($report.gpu.backend)"
    $lines += "- Name: $($report.gpu.name)"
    $lines += "- VRAM: $($report.gpu.vram_mb) MB"
    $lines += "- Native inference backend: $($report.gpu.native_inference_backend)"
    $lines += ""
    $lines += "Compose"
    $lines += "- Flags: docker compose $($report.compose.flags -join ' ')"
    $lines += "- docker version exit: $($report.compose.docker_version.exit_code)"
    $lines += "- docker info exit: $($report.compose.docker_info.exit_code)"
    $lines += "- compose config exit: $($report.compose.compose_config.exit_code)"
    $lines += "- compose ps -a exit: $($report.compose.compose_ps.exit_code)"
    $lines += ""
    $lines += "Health"
    $lines += "- LLM API: $($report.health.llm_api.ok) (status $($report.health.llm_api.status_code))"
    $lines += "- Open WebUI: $($report.health.open_webui.ok) (status $($report.health.open_webui.status_code))"
    $lines += "- Dashboard: $($report.health.dashboard.ok) (status $($report.health.dashboard.status_code))"
    $lines += "- Dashboard API: $($report.health.dashboard_api.ok) (status $($report.health.dashboard_api.status_code))"
    $lines += "- Whisper: $($report.health.whisper.ok) (status $($report.health.whisper.status_code))"
    if ($report.health.whisper.ok) {
        $lines += "- Whisper STT model cached: $($report.health.stt_model.cached) ($($report.health.stt_model.name), status $($report.health.stt_model.status_code))"
        if (-not $report.health.stt_model.cached) {
            $lines += "  Recovery: $($report.health.stt_model.recovery_command)"
        }
    } else {
        $lines += "- Whisper STT model cached: skipped (Whisper unavailable)"
    }
    $lines += ""
    $lines += "Raw command snippets are available in report.json."

    $lines | Set-Content -Path $txtPath -Encoding UTF8

    Write-Host ""
    Write-Chapter "INSTALL REPORT"
    Write-AISuccess "Report generated:"
    Write-AI "  JSON: $jsonPath"
    Write-AI "  Text: $txtPath"
    Write-AIWarn "compose config in report.json may contain secrets from .env -- review before sharing."
    Write-AI "Attach report.json when filing Windows install/runtime issues."

    return @{
        JsonPath = $jsonPath
        TextPath = $txtPath
    }
}
