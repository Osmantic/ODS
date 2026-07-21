# ============================================================================
# ODS Windows -- Docker Compose diagnostics
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: When docker compose fails, print actionable context (config, docker
#          state) so installs on diverse hardware produce useful bug reports.
# Requires: ui.ps1 (Write-AI, Write-AIWarn, Write-Chapter) sourced first.
# ============================================================================

function Get-ODSComposeEnvFileArgs {
    param([string]$InstallDir)
    $envPath = Join-Path $InstallDir ".env"
    if (Test-Path $envPath) {
        return @("--env-file", ".env")
    }
    return @()
}

function Initialize-ODSComposeDockerClientConfig {
    param([string]$InstallDir)

    $dockerConfigDir = Join-Path (Join-Path $InstallDir "data") "docker-client-public"
    if (-not (Test-Path $dockerConfigDir)) {
        New-Item -ItemType Directory -Path $dockerConfigDir -Force | Out-Null
    }

    $dockerConfigPath = Join-Path $dockerConfigDir "config.json"
    if (-not (Test-Path $dockerConfigPath)) {
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($dockerConfigPath, "{`n  `"auths`": {}`n}`n", $utf8NoBom)
    }

    return $dockerConfigDir
}

function Invoke-ODSDockerCompose {
    <#
    .SYNOPSIS
        Run docker compose with PS 5.1-safe stderr handling; return exit code.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$InstallDir,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]]$ComposeFlags,
        [Parameter(Mandatory = $true)]
        [string[]]$ComposeArgs
    )
    Push-Location $InstallDir
    try {
        $dockerConfigDir = Initialize-ODSComposeDockerClientConfig -InstallDir $InstallDir
        $dockerClientArgs = @("--config", $dockerConfigDir)
        $hadDockerConfig = Test-Path Env:DOCKER_CONFIG
        $previousDockerConfig = $env:DOCKER_CONFIG
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'SilentlyContinue'
        $env:DOCKER_CONFIG = $dockerConfigDir
        & docker @dockerClientArgs compose @ComposeFlags @ComposeArgs 2>&1 | ForEach-Object { Write-Host "  $_" }
        $exitCode = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP
        return $exitCode
    }
    finally {
        if ($hadDockerConfig) {
            $env:DOCKER_CONFIG = $previousDockerConfig
        } else {
            Remove-Item Env:DOCKER_CONFIG -ErrorAction SilentlyContinue
        }
        if ($null -ne $prevEAP) {
            $ErrorActionPreference = $prevEAP
        }
        Pop-Location
    }
}

function Get-ODSComposeEnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$InstallDir,
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [string]$Default = "unknown"
    )

    $envPath = Join-Path $InstallDir ".env"
    if (-not (Test-Path $envPath)) { return $Default }

    try {
        $line = Get-Content $envPath -ErrorAction Stop |
            Where-Object { $_ -match "^$([regex]::Escape($Name))=" } |
            Select-Object -First 1
        if (-not $line) { return $Default }
        $value = ($line -split "=", 2)[1].Trim()
        $value = $value.Trim('"').Trim("'")
        if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
        return $value
    }
    catch {
        return $Default
    }
}

function Get-ODSComposeFailedImages {
    param([string]$ComposeLogPath)

    if ([string]::IsNullOrWhiteSpace($ComposeLogPath) -or -not (Test-Path $ComposeLogPath)) {
        return @()
    }

    $pattern = '([a-z0-9._-]+([.:][0-9]+)?/)?[a-z0-9._/-]+:[A-Za-z0-9._-]+'
    $imageMatches = @()
    try {
        $imageMatches = Select-String -Path $ComposeLogPath -Pattern $pattern -AllMatches -ErrorAction Stop |
            ForEach-Object { $_.Matches.Value } |
            Where-Object {
                $_ -match 'ghcr\.io|docker\.io|quay\.io|nvidia|llama|ods|open-webui|qdrant|speaches|comfy|litellm|perplexica'
            } |
            Sort-Object -Unique |
            Select-Object -First 20
    }
    catch {
        return @()
    }

    return @($imageMatches)
}

function Get-ODSComposeSensitiveEnvValues {
    param([string]$InstallDir)

    $envPath = Join-Path $InstallDir ".env"
    if (-not (Test-Path $envPath)) { return @() }

    $values = @()
    try {
        foreach ($line in (Get-Content $envPath -ErrorAction Stop)) {
            if ($line -notmatch '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { continue }
            $key = $Matches[1]
            $value = $Matches[2].Trim().Trim('"').Trim("'")
            # USER|EMAIL|BEARER cover schema secret:true keys the shorter set
            # missed - N8N_USER, LANGFUSE_INIT_USER_EMAIL, LANGFUSE_MINIO_ROOT_USER -
            # whose values would otherwise ship in the shareable failure report.
            # Keep in sync with ConvertTo-ODSComposeRedactedLine below and the
            # Linux support bundle's redaction set.
            if ($value.Length -ge 4 -and $key -match '(?i)(KEY|TOKEN|SECRET|PASSWORD|PASS|SALT|AUTH|CREDENTIAL|USER|EMAIL|BEARER)') {
                $values += $value
            }
        }
    }
    catch {
        return @()
    }

    return @($values | Sort-Object -Unique)
}

function ConvertTo-ODSComposeRedactedLine {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Line,
        [string[]]$SensitiveValues = @()
    )

    $redacted = $Line
    # Key set kept in sync with Get-ODSComposeSensitiveEnvValues above.
    if ($redacted -match '(?i)(KEY|TOKEN|SECRET|PASSWORD|PASS|SALT|AUTH|CREDENTIAL|USER|EMAIL|BEARER)' -and $redacted -match '[:=]') {
        $separatorMatch = [regex]::Match($redacted, '[:=]\s*')
        if ($separatorMatch.Success) {
            $redacted = $redacted.Substring(0, $separatorMatch.Index + $separatorMatch.Length) + "[REDACTED]"
        }
    }

    foreach ($value in $SensitiveValues) {
        if ([string]::IsNullOrWhiteSpace($value)) { continue }
        $redacted = $redacted.Replace($value, "[REDACTED]")
    }

    return $redacted
}

function Add-ODSComposePortReport {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ReportPath,
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [Parameter(Mandatory = $true)]
        [string]$Port
    )

    if ([string]::IsNullOrWhiteSpace($Port) -or $Port -eq "0") { return }

    $connections = @()
    try {
        $connections = Get-NetTCPConnection -LocalPort ([int]$Port) -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 3
    }
    catch {
        $connections = @()
    }

    if ($connections -and $connections.Count -gt 0) {
        "- ${Label}:${Port} occupied" | Add-Content -Path $ReportPath -Encoding UTF8
        foreach ($conn in $connections) {
            $pidText = if ($conn.OwningProcess) { " pid=$($conn.OwningProcess)" } else { "" }
            "  $($conn.LocalAddress):$($conn.LocalPort)$pidText" | Add-Content -Path $ReportPath -Encoding UTF8
        }
    }
    else {
        "- ${Label}:${Port} free" | Add-Content -Path $ReportPath -Encoding UTF8
    }
}

function Add-ODSComposeCommandSection {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ReportPath,
        [Parameter(Mandatory = $true)]
        [string]$Title,
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command,
        [int]$First = 0,
        [int]$Last = 0,
        [switch]$RedactSecrets,
        [string[]]$SensitiveValues = @()
    )

    "" | Add-Content -Path $ReportPath -Encoding UTF8
    $Title | Add-Content -Path $ReportPath -Encoding UTF8
    try {
        $output = & $Command 2>&1 | ForEach-Object { $_.ToString() }
        if ($output) {
            $lines = @($output)
            if ($RedactSecrets) {
                $lines = $lines | ForEach-Object {
                    ConvertTo-ODSComposeRedactedLine -Line $_ -SensitiveValues $SensitiveValues
                }
            }
            if ($First -gt 0) {
                $lines | Select-Object -First $First | Add-Content -Path $ReportPath -Encoding UTF8
            }
            elseif ($Last -gt 0) {
                $take = [Math]::Min($Last, $lines.Count)
                $start = [Math]::Max(0, $lines.Count - $take)
                for ($i = $start; $i -lt $lines.Count; $i++) {
                    $lines[$i] | Add-Content -Path $ReportPath -Encoding UTF8
                }
            }
            else {
                $lines | Add-Content -Path $ReportPath -Encoding UTF8
            }
        }
        else {
            "(no output)" | Add-Content -Path $ReportPath -Encoding UTF8
        }
    }
    catch {
        "command failed: $_" | Add-Content -Path $ReportPath -Encoding UTF8
    }
}

function Write-ODSComposeFailureReport {
    <#
    .SYNOPSIS
        Save a bounded, shareable report after install-time docker compose failure.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$InstallDir,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]]$ComposeFlags,
        [string[]]$ComposeArgs = @(),
        [string]$Phase = "install",
        [string]$ComposeLogPath = "",
        [string]$NextStep = "Open the saved report, fix the failed image/port/compose error it identifies, then re-run .\install.ps1."
    )

    $logsDir = Join-Path $InstallDir "logs"
    if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir -Force | Out-Null }

    $stamp = Get-Date -Format "yyyy-MM-dd-HHmmss"
    $reportPath = Join-Path $InstallDir "install-report-$stamp.txt"
    $composeCommand = ("docker compose " + (($ComposeFlags + $ComposeArgs) -join " ")).Trim()
    $flagsFile = Join-Path $InstallDir ".compose-flags"
    $gpuBackend = Get-ODSComposeEnvValue -InstallDir $InstallDir -Name "GPU_BACKEND" -Default "unknown"

    @(
        "ODS install failure report",
        "Generated: $((Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ"))",
        "Phase: $Phase",
        "",
        "Privacy note",
        "- This report avoids dumping the full .env.",
        "- Compose config output is redacted for common secret fields and known sensitive .env values.",
        "- Review before posting publicly.",
        "",
        "Summary",
        "- Install dir: $InstallDir",
        "- GPU backend: $gpuBackend",
        "- Compose command: $composeCommand",
        "- Installer log: $(if ($ComposeLogPath) { $ComposeLogPath } else { "unavailable" })",
        "- Cached compose flags: $(if (Test-Path $flagsFile) { Get-Content $flagsFile -Raw } else { "unavailable" })",
        "- Next step: $NextStep",
        "",
        "Configured model/runtime",
        "- ODS_MODE=$(Get-ODSComposeEnvValue -InstallDir $InstallDir -Name "ODS_MODE" -Default "unknown")",
        "- LLM_MODEL=$(Get-ODSComposeEnvValue -InstallDir $InstallDir -Name "LLM_MODEL" -Default "unknown")",
        "- GGUF_FILE=$(Get-ODSComposeEnvValue -InstallDir $InstallDir -Name "GGUF_FILE" -Default "unknown")",
        "- LLAMA_SERVER_IMAGE=$(Get-ODSComposeEnvValue -InstallDir $InstallDir -Name "LLAMA_SERVER_IMAGE" -Default "default")",
        "- CTX_SIZE=$(Get-ODSComposeEnvValue -InstallDir $InstallDir -Name "CTX_SIZE" -Default "unknown")",
        "",
        "Likely failed image(s)"
    ) | Set-Content -Path $reportPath -Encoding UTF8

    $failedImages = @(Get-ODSComposeFailedImages -ComposeLogPath $ComposeLogPath)
    if ($failedImages.Count -gt 0) {
        $failedImages | ForEach-Object { "- $_" } | Add-Content -Path $reportPath -Encoding UTF8
    }
    else {
        "- none detected from installer log" | Add-Content -Path $reportPath -Encoding UTF8
    }

    "" | Add-Content -Path $reportPath -Encoding UTF8
    "Port checks" | Add-Content -Path $reportPath -Encoding UTF8
    Add-ODSComposePortReport -ReportPath $reportPath -Label "llama-server" -Port (Get-ODSComposeEnvValue -InstallDir $InstallDir -Name "OLLAMA_PORT" -Default "11434")
    Add-ODSComposePortReport -ReportPath $reportPath -Label "open-webui" -Port (Get-ODSComposeEnvValue -InstallDir $InstallDir -Name "WEBUI_PORT" -Default "3000")
    Add-ODSComposePortReport -ReportPath $reportPath -Label "dashboard" -Port (Get-ODSComposeEnvValue -InstallDir $InstallDir -Name "DASHBOARD_PORT" -Default "3001")
    Add-ODSComposePortReport -ReportPath $reportPath -Label "dashboard-api" -Port (Get-ODSComposeEnvValue -InstallDir $InstallDir -Name "DASHBOARD_API_PORT" -Default "3002")
    Add-ODSComposePortReport -ReportPath $reportPath -Label "litellm" -Port (Get-ODSComposeEnvValue -InstallDir $InstallDir -Name "LITELLM_PORT" -Default "4000")
    Add-ODSComposePortReport -ReportPath $reportPath -Label "searxng" -Port (Get-ODSComposeEnvValue -InstallDir $InstallDir -Name "SEARXNG_PORT" -Default "8888")

    Push-Location $InstallDir
    try {
        $envArgs = Get-ODSComposeEnvFileArgs -InstallDir $InstallDir
        $sensitiveValues = @(Get-ODSComposeSensitiveEnvValues -InstallDir $InstallDir)
        Add-ODSComposeCommandSection -ReportPath $reportPath -Title "Docker version" -First 60 -Command { & docker version }
        Add-ODSComposeCommandSection -ReportPath $reportPath -Title "Docker info" -First 80 -Command { & docker info }
        Add-ODSComposeCommandSection -ReportPath $reportPath -Title "Compose config tail (redacted)" -Last 80 -RedactSecrets -SensitiveValues $sensitiveValues -Command { & docker compose @ComposeFlags @envArgs config }
        Add-ODSComposeCommandSection -ReportPath $reportPath -Title "Compose ps" -First 80 -Command { & docker compose @ComposeFlags @envArgs ps -a }
    }
    finally {
        Pop-Location
    }

    "" | Add-Content -Path $reportPath -Encoding UTF8
    "Installer log tail" | Add-Content -Path $reportPath -Encoding UTF8
    if ($ComposeLogPath -and (Test-Path $ComposeLogPath)) {
        Get-Content $ComposeLogPath -Tail 160 | Add-Content -Path $reportPath -Encoding UTF8
    }
    else {
        "installer log unavailable" | Add-Content -Path $reportPath -Encoding UTF8
    }

    return $reportPath
}

function Write-ODSComposeDiagnostics {
    <#
    .SYNOPSIS
        Print bounded docker/compose diagnostics after a compose command failed.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$InstallDir,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]]$ComposeFlags,
        [string]$Phase = "install",
        [string[]]$ComposeArgs = @(),
        [string]$ComposeLogPath = "",
        [string]$NextStep = "Open the saved report, fix the failed image/port/compose error it identifies, then re-run .\install.ps1.",
        [switch]$SaveReport
    )

    Write-Chapter "COMPOSE FAILURE DIAGNOSTICS"
    Write-AI "Phase: $Phase -- save this section if you report an issue."
    Write-AI "Docs: ods/docs/WINDOWS-TROUBLESHOOTING-GUIDE.md (section: Docker Compose failed)"
    Write-Host ""

    Push-Location $InstallDir
    try {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'SilentlyContinue'

        Write-Host "  --- docker version ---" -ForegroundColor DarkGray
        $dv = & docker version 2>&1 | ForEach-Object { $_.ToString() }
        if ($dv) { $dv | Select-Object -First 25 | ForEach-Object { Write-Host "  $_" } }
        else { Write-Host "  (docker version produced no output)" -ForegroundColor Yellow }
        Write-Host ""

        Write-Host "  --- docker info (first 35 lines) ---" -ForegroundColor DarkGray
        $di = & docker info 2>&1 | ForEach-Object { $_.ToString() }
        if ($di) { $di | Select-Object -First 35 | ForEach-Object { Write-Host "  $_" } }
        else { Write-Host "  (docker info failed -- is Docker Desktop running?)" -ForegroundColor Yellow }
        Write-Host ""

        $envArgs = Get-ODSComposeEnvFileArgs -InstallDir $InstallDir
        Write-AIWarn "Output below may include substituted .env values (secrets). Redact before posting publicly."
        Write-Host "  --- docker compose ... config (last 55 lines) ---" -ForegroundColor DarkGray
        $cfgOut = & docker compose @ComposeFlags @envArgs config 2>&1 | ForEach-Object { $_.ToString() }
        if ($cfgOut) {
            $cfgLines = @($cfgOut)
            $take = [Math]::Min(55, $cfgLines.Count)
            if ($cfgLines.Count -gt 0) {
                $start = [Math]::Max(0, $cfgLines.Count - $take)
                for ($i = $start; $i -lt $cfgLines.Count; $i++) {
                    Write-Host "  $($cfgLines[$i])"
                }
            }
        }
        else {
            Write-AIWarn "docker compose config produced no output (merge/parse error likely above)."
        }
        Write-Host ""

        Write-Host "  --- docker compose ... ps -a ---" -ForegroundColor DarkGray
        $psOut = & docker compose @ComposeFlags @envArgs ps -a 2>&1 | ForEach-Object { $_.ToString() }
        if ($psOut) {
            $psOut | Select-Object -First 40 | ForEach-Object { Write-Host "  $_" }
        }
        else {
            Write-Host "  (no ps output)" -ForegroundColor DarkGray
        }

        $ErrorActionPreference = $prevEAP
    }
    finally {
        Pop-Location
    }

    Write-Host ""
    Write-AI "Next: confirm Docker Desktop is running, WSL2 backend is on, and ports in .env are free."

    if ($SaveReport) {
        $reportPath = Write-ODSComposeFailureReport -InstallDir $InstallDir -ComposeFlags $ComposeFlags `
            -ComposeArgs $ComposeArgs -Phase $Phase -ComposeLogPath $ComposeLogPath -NextStep $NextStep
        Write-AIWarn "Compose failure report saved: $reportPath"
    }
}
