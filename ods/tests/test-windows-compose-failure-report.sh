#!/bin/bash
# ============================================================================
# ODS Windows compose failure report tests
# ============================================================================
# Static checks for install-time automatic report wiring on Windows.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DIAG_LIB="$ROOT_DIR/installers/windows/lib/compose-diagnostics.ps1"
INSTALL_PS1="$ROOT_DIR/installers/windows/install-windows.ps1"
PRE_SCRIPT="$ROOT_DIR/installers/windows/phases/01-preflight.ps1"
DOCKER_PHASE="$ROOT_DIR/installers/windows/phases/05-docker.ps1"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASS=0
FAIL=0

pass() { echo -e "  ${GREEN}PASS${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}FAIL${NC} $1"; FAIL=$((FAIL + 1)); }

check() {
    local pattern="$1" file="$2" label="$3"
    if grep -Fq -- "$pattern" "$file"; then
        pass "$label"
    else
        fail "$label"
    fi
}

echo ""
echo "=== Windows compose failure report tests ==="
echo ""

[[ -f "$DIAG_LIB" ]] && pass "compose diagnostics library exists" || fail "compose diagnostics library missing"
[[ -f "$INSTALL_PS1" ]] && pass "Windows installer exists" || fail "Windows installer missing"
[[ -f "$PRE_SCRIPT" ]] && pass "Windows preflight phase exists" || fail "Windows preflight phase missing"
[[ -f "$DOCKER_PHASE" ]] && pass "Windows Docker phase exists" || fail "Windows Docker phase missing"

check 'function Write-ODSComposeFailureReport' "$DIAG_LIB" "report writer function exists"
check 'install-report-$stamp.txt' "$DIAG_LIB" "report uses install-report timestamp path"
check 'Get-ODSComposeFailedImages' "$DIAG_LIB" "report extracts failed images from compose log"
check 'ConvertTo-ODSComposeRedactedLine' "$DIAG_LIB" "report has compose config redaction helper"
check 'Get-NetTCPConnection' "$DIAG_LIB" "report includes Windows port checks"
check 'Compose config tail (redacted)' "$DIAG_LIB" "report captures redacted compose config"
check '[switch]$SaveReport' "$DIAG_LIB" "diagnostics only save report when requested"
check '-ComposeLogPath $_composeLog' "$INSTALL_PS1" "installer passes compose log to diagnostics"
check '$composeUpArgs = @("up", "-d", "--remove-orphans", "--no-build", "--pull", "never")' "$INSTALL_PS1" "installer passes guarded compose up args"
check 'Invoke-ODSWindowsComposeImagePreflight' "$INSTALL_PS1" "installer preflights compose images before up"
check '-SaveReport' "$INSTALL_PS1" "installer enables saved report on compose failure"
check 'function Assert-ODSWindowsComposeCwd' "$INSTALL_PS1" "installer asserts compose cwd before launch"
check 'Write-ODSWindowsComposeLaunchRecord' "$INSTALL_PS1" "installer writes compose launch record"
check '"compose-launch.txt"' "$INSTALL_PS1" "installer records compose launch artifact path"
check '[Environment]::CurrentDirectory' "$INSTALL_PS1" "installer keeps .NET cwd aligned with install dir"
check 'Compose working directory: $installDir' "$INSTALL_PS1" "installer logs compose working directory"
check 'Push-Location $installDir' "$INSTALL_PS1" "installer runs compose build/up from install dir"
check 'Join-Path $installDir $cfPath' "$INSTALL_PS1" "installer validates relative compose files under install dir"
check 'HERMES_AGENT_IMAGE_FALLBACK' "$INSTALL_PS1" "installer supports Hermes image fallback"
check 'Validating Hermes Agent image tag before startup' "$INSTALL_PS1" "installer validates Hermes image before compose up"
check 'ImageEnvName = "LLAMA_SERVER_IMAGE"' "$INSTALL_PS1" "image validation labels override env var"
check 'Get-ODSWindowsUserDockerClientArgs' "$INSTALL_PS1" "installer preserves the user Docker config for recovery"
check 'ODSWindowsOriginalDockerConfigDefined' "$INSTALL_PS1" "installer snapshots an existing DOCKER_CONFIG before isolation"
check 'image validation failed with the install-scoped Docker config' "$INSTALL_PS1" "image validation retries outside the installer-scoped Docker config"
check 'Write-AIWarn "Using $source for $Reason."' "$INSTALL_PS1" "installer records Docker config promotion reason"
check 'Continuing Compose preflight and service launch with the user'\''s Docker config' "$INSTALL_PS1" "installer carries Docker fallback through preflight and launch"
check 'Compose service launch failed with the install-scoped Docker config' "$INSTALL_PS1" "compose up retries outside the installer-scoped Docker config"
check 'Managed-container inspection failed with the install-scoped Docker config' "$INSTALL_PS1" "managed-container inspection retries outside the installer-scoped Docker config"
check 'Write-ODSWindowsComposeLaunchRecord -InstallDir $installDir -ComposeFlags $composeFlags' "$INSTALL_PS1" "compose launch record is refreshed after a Docker config fallback"
check '$_probeImage = "alpine:3.20"' "$PRE_SCRIPT" "preflight uses pinned Alpine probe image"
check '$_inspectExit = $LASTEXITCODE' "$PRE_SCRIPT" "preflight captures inspect exit before deciding to pull"
check 'docker pull $_probeImage' "$PRE_SCRIPT" "preflight pulls missing probe image before bind-mount test"
check 'Docker could not download $_probeImage' "$PRE_SCRIPT" "preflight reports Alpine pull failure separately"
check 'throw "Docker probe image download failed"' "$PRE_SCRIPT" "preflight image download failure terminates installer"
check 'throw "Docker bind-mount probe failed"' "$PRE_SCRIPT" "preflight unexpected bind-mount failure terminates installer"
check 'The probe image ($_probeImage) is already available; this is a file-sharing path issue.' "$PRE_SCRIPT" "preflight separates file sharing from image availability"
check 'throw "Docker Desktop cannot bind-mount $installDir"' "$PRE_SCRIPT" "preflight file-sharing failure terminates installer"
check 'throw "Docker daemon is not responding"' "$DOCKER_PHASE" "Docker daemon prerequisite failure is terminating"
check 'throw "Docker Compose not found"' "$DOCKER_PHASE" "Docker Compose prerequisite failure is terminating"

if grep -q "Write-ODSComposeDiagnostics .*SaveReport" "$ROOT_DIR/installers/windows/ods.ps1"; then
    fail "ods.ps1 command failures should not create install reports by default"
else
    pass "ods.ps1 diagnostics remain console-only by default"
fi

if command -v pwsh >/dev/null 2>&1; then
    TMP_DIR="$(mktemp -d)"
    cleanup() { rm -rf "$TMP_DIR"; }
    trap cleanup EXIT

    INSTALL_DIR="$TMP_DIR/ods"
    mkdir -p "$TMP_DIR/bin" "$INSTALL_DIR/logs"

    if [[ "${OS:-}" == "Windows_NT" ]]; then
        cat > "$TMP_DIR/bin/docker.cmd" <<'EOF'
@echo off
if /I "%~1"=="version" (
  echo Docker version 29.2.1
  exit /b 0
)
if /I "%~1"=="info" (
  echo Server Version: 29.2.1
  exit /b 0
)
set args=%*
echo %args% | findstr /I /C:"compose" >nul || exit /b 0
echo %args% | findstr /I /C:"config" >nul || goto :check_ps
echo services:
echo   dashboard-api:
echo     environment:
echo       DASHBOARD_API_KEY: super-secret-dashboard-key
echo       OPENCLAW_TOKEN: super-secret-openclaw-token
exit /b 0
:check_ps
echo %args% | findstr /I /C:"ps -a" >nul || exit /b 0
echo NAME IMAGE COMMAND SERVICE CREATED STATUS PORTS
exit /b 0
EOF
    else
        cat > "$TMP_DIR/bin/docker" <<'EOF'
#!/usr/bin/env bash
if [[ "$1" == "version" ]]; then
  echo "Docker version 29.2.1"
  exit 0
fi
if [[ "$1" == "info" ]]; then
  echo "Server Version: 29.2.1"
  exit 0
fi
if [[ "$1" == "compose" ]]; then
  if [[ "$*" == *" config"* ]]; then
    echo "services:"
    echo "  dashboard-api:"
    echo "    environment:"
    echo "      DASHBOARD_API_KEY: super-secret-dashboard-key"
    echo "      OPENCLAW_TOKEN: super-secret-openclaw-token"
    echo "      N8N_USER: super-secret-n8n-user"
    echo "      LANGFUSE_INIT_USER_EMAIL: super-secret-langfuse-email"
    exit 0
  fi
  if [[ "$*" == *" ps -a"* ]]; then
    echo "NAME IMAGE COMMAND SERVICE CREATED STATUS PORTS"
    exit 0
  fi
fi
exit 0
EOF
        chmod +x "$TMP_DIR/bin/docker"
    fi

    cat > "$INSTALL_DIR/.env" <<'EOF'
GPU_BACKEND=nvidia
LLAMA_SERVER_IMAGE=ghcr.io/ggml-org/llama.cpp:server-cuda-b8648
DASHBOARD_API_KEY=super-secret-dashboard-key
OPENCLAW_TOKEN=super-secret-openclaw-token
N8N_USER=super-secret-n8n-user
LANGFUSE_INIT_USER_EMAIL=super-secret-langfuse-email
OLLAMA_PORT=39134
EOF

    cat > "$INSTALL_DIR/logs/compose-up.log" <<'EOF'
Error response from daemon: failed to resolve reference "ghcr.io/ggml-org/llama.cpp:server-cuda-b8648": not found
EOF

    if PATH="$TMP_DIR/bin:$PATH" DIAG_LIB="$DIAG_LIB" INSTALL_DIR="$INSTALL_DIR" pwsh -NoProfile -Command '
        $ErrorActionPreference = "Stop"
        . $env:DIAG_LIB
        $report = Write-ODSComposeFailureReport `
            -InstallDir $env:INSTALL_DIR `
            -ComposeFlags @("-f", "docker-compose.base.yml") `
            -ComposeArgs @("up", "-d") `
            -Phase "test phase" `
            -ComposeLogPath (Join-Path $env:INSTALL_DIR "logs/compose-up.log")
        if (-not (Test-Path $report)) { throw "report not created" }
        $text = Get-Content $report -Raw
        foreach ($needle in @(
            "ODS install failure report",
            "GPU backend: nvidia",
            "ghcr.io/ggml-org/llama.cpp:server-cuda-b8648",
            "Compose config tail (redacted)",
            "DASHBOARD_API_KEY: [REDACTED]",
            "OPENCLAW_TOKEN: [REDACTED]",
            "N8N_USER: [REDACTED]",
            "LANGFUSE_INIT_USER_EMAIL: [REDACTED]"
        )) {
            if (-not $text.Contains($needle)) { throw "missing $needle" }
        }
        foreach ($secret in @(
            "super-secret-dashboard-key",
            "super-secret-openclaw-token",
            "super-secret-n8n-user",
            "super-secret-langfuse-email"
        )) {
            if ($text.Contains($secret)) { throw "sensitive compose config value leaked: $secret" }
        }
    '; then
        pass "PowerShell report writer creates redacted report with mocked docker"
    else
        fail "PowerShell report writer runtime behavior failed"
    fi

    if INSTALL_PS1="$INSTALL_PS1" INSTALL_DIR="$INSTALL_DIR" pwsh -NoProfile -Command '
        $ErrorActionPreference = "Stop"
        $code = Get-Content $env:INSTALL_PS1 -Raw
         $assertStart = $code.IndexOf("function Assert-ODSWindowsComposeCwd")
         $recordStart = $code.IndexOf("function Write-ODSWindowsComposeLaunchRecord")
         $launchStart = $code.IndexOf("# ── Start Docker services")
         $imageTestStart = $code.IndexOf("function Test-ODSDockerImageAvailable")
         $envReaderStart = $code.IndexOf("function Get-ODSEnvValueFromFile")
         if ($assertStart -lt 0 -or $recordStart -lt 0 -or $launchStart -lt 0 -or $imageTestStart -lt 0 -or $envReaderStart -lt 0) {
             throw "required function block not found"
         }
         $helperCode = $code.Substring($assertStart, $launchStart - $assertStart)
         $imageHelperCode = $code.Substring($imageTestStart, $envReaderStart - $imageTestStart)
        function Write-Utf8NoBom {
            param([string]$Path, [string]$Content)
            [System.IO.File]::WriteAllText($Path, $Content, (New-Object System.Text.UTF8Encoding($false)))
        }
        function Write-AIError { param([string]$Message) Write-Host "ERROR:$Message" }
        function Write-AI { param([string]$Message) Write-Host "INFO:$Message" }
        function Write-AISuccess { param([string]$Message) Write-Host "SUCCESS:$Message" }
        function Write-AIWarn { param([string]$Message) Write-Host "WARN:$Message" }
         function docker {
            param([Parameter(ValueFromRemainingArguments = $true)][string[]]$DockerArgs)
            $joined = $DockerArgs -join " "
            if ($joined -match "docker-client-public") {
                $global:LASTEXITCODE = 1
                return
            }
            if ($joined -match "image inspect") {
                $global:LASTEXITCODE = 0
                return
            }
            $global:LASTEXITCODE = 0
         }
         Invoke-Expression $helperCode
         Invoke-Expression $imageHelperCode

        Push-Location $env:INSTALL_DIR
        $previous = [Environment]::CurrentDirectory
        [Environment]::CurrentDirectory = $env:INSTALL_DIR
        try {
            $originalDockerConfig = Join-Path $env:INSTALL_DIR "docker-user-config"
            $env:DOCKER_CONFIG = $originalDockerConfig
            $script:ODSWindowsOriginalDockerConfigDefined = Test-Path Env:DOCKER_CONFIG
            $script:ODSWindowsOriginalDockerConfig = $env:DOCKER_CONFIG
            Initialize-ODSWindowsDockerClientConfig -InstallDir $env:INSTALL_DIR
            $script:ODSWindowsDockerClientArgs = @("--config", $env:DOCKER_CONFIG)
            $script:ODSWindowsUserDockerClientArgs = @(Get-ODSWindowsUserDockerClientArgs)
            $script:ODSWindowsDockerConfigMode = "install-scoped"
            $resolvedImage = Resolve-ODSDockerImageOrFallback -Image "example.invalid/ods:test" -Label "test image"
            if ($resolvedImage -ne "example.invalid/ods:test") { throw "image validation did not recover through the user Docker config" }
            if ($script:ODSWindowsDockerConfigMode -ne "user") { throw "Docker config mode was not promoted after image validation" }
            if ($env:DOCKER_CONFIG -ne $originalDockerConfig) { throw "original DOCKER_CONFIG was not restored" }
            $expectedDockerConfig = $originalDockerConfig
            Assert-ODSWindowsComposeCwd -InstallDir $env:INSTALL_DIR
            Write-ODSWindowsComposeLaunchRecord -InstallDir $env:INSTALL_DIR `
                -ComposeFlags @("--env-file", ".env", "-f", "docker-compose.base.yml") `
                -ComposeArgs @("up", "-d", "--remove-orphans", "--no-build", "--pull", "never") `
                -DockerClientArgs @("--config", $expectedDockerConfig)
        }
        finally {
            [Environment]::CurrentDirectory = $previous
            Pop-Location
        }

        $record = Join-Path $env:INSTALL_DIR "logs\compose-launch.txt"
        if (-not (Test-Path $record)) { throw "launch record not created" }
        $text = Get-Content $record -Raw
        $normalizedText = $text.Replace("\", "/")
        $normalizedInstallDir = $env:INSTALL_DIR.Replace("\", "/")
        $normalizedDockerConfig = $expectedDockerConfig.Replace("\", "/")
        foreach ($needle in @(
            "cwd=$normalizedInstallDir",
            "dotnet_cwd=$normalizedInstallDir",
            "docker_config=$normalizedDockerConfig",
            "compose_command=docker --config `"$normalizedDockerConfig`" compose --env-file .env -f docker-compose.base.yml up -d --remove-orphans --no-build --pull never",
            "compose_ps_command=cd"
        )) {
            if (-not $normalizedText.Contains($needle)) { throw "missing $needle" }
        }
    '; then
        pass "PowerShell Docker fallback preserves original config during early image validation"
    else
        fail "PowerShell compose launch record runtime behavior failed"
    fi
else
    pass "PowerShell runtime behavior skipped (pwsh unavailable)"
fi

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
