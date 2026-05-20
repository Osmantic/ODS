#!/bin/bash
# ============================================================================
# Dream Server Windows compose failure report tests
# ============================================================================
# Static checks for install-time automatic report wiring on Windows.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DIAG_LIB="$ROOT_DIR/installers/windows/lib/compose-diagnostics.ps1"
INSTALL_PS1="$ROOT_DIR/installers/windows/install-windows.ps1"

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

check 'function Write-DreamComposeFailureReport' "$DIAG_LIB" "report writer function exists"
check 'install-report-$stamp.txt' "$DIAG_LIB" "report uses install-report timestamp path"
check 'Get-DreamComposeFailedImages' "$DIAG_LIB" "report extracts failed images from compose log"
check 'ConvertTo-DreamComposeRedactedLine' "$DIAG_LIB" "report has compose config redaction helper"
check 'Get-NetTCPConnection' "$DIAG_LIB" "report includes Windows port checks"
check 'Compose config tail (redacted)' "$DIAG_LIB" "report captures redacted compose config"
check '[switch]$SaveReport' "$DIAG_LIB" "diagnostics only save report when requested"
check '-ComposeLogPath $_composeLog' "$INSTALL_PS1" "installer passes compose log to diagnostics"
check '-ComposeArgs @("up", "-d", "--remove-orphans", "--no-build")' "$INSTALL_PS1" "installer passes exact compose up args"
check '-SaveReport' "$INSTALL_PS1" "installer enables saved report on compose failure"
check 'function Assert-DreamWindowsComposeCwd' "$INSTALL_PS1" "installer asserts compose cwd before launch"
check 'Write-DreamWindowsComposeLaunchRecord' "$INSTALL_PS1" "installer writes compose launch record"
check '"compose-launch.txt"' "$INSTALL_PS1" "installer records compose launch artifact path"
check '[Environment]::CurrentDirectory' "$INSTALL_PS1" "installer keeps .NET cwd aligned with install dir"

if grep -q "Write-DreamComposeDiagnostics .*SaveReport" "$ROOT_DIR/installers/windows/dream.ps1"; then
    fail "dream.ps1 command failures should not create install reports by default"
else
    pass "dream.ps1 diagnostics remain console-only by default"
fi

if command -v pwsh >/dev/null 2>&1; then
    TMP_DIR="$(mktemp -d)"
    cleanup() { rm -rf "$TMP_DIR"; }
    trap cleanup EXIT

    INSTALL_DIR="$TMP_DIR/dream-server"
    mkdir -p "$TMP_DIR/bin" "$INSTALL_DIR/logs"

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

    cat > "$INSTALL_DIR/.env" <<'EOF'
GPU_BACKEND=nvidia
LLAMA_SERVER_IMAGE=ghcr.io/ggml-org/llama.cpp:server-cuda-b8648
DASHBOARD_API_KEY=super-secret-dashboard-key
OPENCLAW_TOKEN=super-secret-openclaw-token
OLLAMA_PORT=39134
EOF

    cat > "$INSTALL_DIR/logs/compose-up.log" <<'EOF'
Error response from daemon: failed to resolve reference "ghcr.io/ggml-org/llama.cpp:server-cuda-b8648": not found
EOF

    if PATH="$TMP_DIR/bin:$PATH" DIAG_LIB="$DIAG_LIB" INSTALL_DIR="$INSTALL_DIR" pwsh -NoProfile -Command '
        $ErrorActionPreference = "Stop"
        . $env:DIAG_LIB
        $report = Write-DreamComposeFailureReport `
            -InstallDir $env:INSTALL_DIR `
            -ComposeFlags @("-f", "docker-compose.base.yml") `
            -ComposeArgs @("up", "-d") `
            -Phase "test phase" `
            -ComposeLogPath (Join-Path $env:INSTALL_DIR "logs/compose-up.log")
        if (-not (Test-Path $report)) { throw "report not created" }
        $text = Get-Content $report -Raw
        foreach ($needle in @(
            "Dream Server install failure report",
            "GPU backend: nvidia",
            "ghcr.io/ggml-org/llama.cpp:server-cuda-b8648",
            "Compose config tail (redacted)",
            "DASHBOARD_API_KEY: [REDACTED]",
            "OPENCLAW_TOKEN: [REDACTED]"
        )) {
            if (-not $text.Contains($needle)) { throw "missing $needle" }
        }
        if ($text.Contains("super-secret-dashboard-key") -or $text.Contains("super-secret-openclaw-token")) {
            throw "sensitive compose config value leaked"
        }
    '; then
        pass "PowerShell report writer creates redacted report with mocked docker"
    else
        fail "PowerShell report writer runtime behavior failed"
    fi

    if INSTALL_PS1="$INSTALL_PS1" INSTALL_DIR="$INSTALL_DIR" pwsh -NoProfile -Command '
        $ErrorActionPreference = "Stop"
        $code = Get-Content $env:INSTALL_PS1 -Raw
        $assertStart = $code.IndexOf("function Assert-DreamWindowsComposeCwd")
        $recordStart = $code.IndexOf("function Write-DreamWindowsComposeLaunchRecord")
        $launchStart = $code.IndexOf("# ── Start Docker services")
        if ($assertStart -lt 0 -or $recordStart -lt 0 -or $launchStart -lt 0) {
            throw "required function block not found"
        }
        $helperCode = $code.Substring($assertStart, $launchStart - $assertStart)
        function Write-AIError { param([string]$Message) Write-Host "ERROR:$Message" }
        function Write-AI { param([string]$Message) Write-Host "INFO:$Message" }
        Invoke-Expression $helperCode

        Push-Location $env:INSTALL_DIR
        $previous = [Environment]::CurrentDirectory
        [Environment]::CurrentDirectory = $env:INSTALL_DIR
        try {
            Assert-DreamWindowsComposeCwd -InstallDir $env:INSTALL_DIR
            Write-DreamWindowsComposeLaunchRecord -InstallDir $env:INSTALL_DIR `
                -ComposeFlags @("--env-file", ".env", "-f", "docker-compose.base.yml") `
                -ComposeArgs @("up", "-d", "--remove-orphans", "--no-build")
        }
        finally {
            [Environment]::CurrentDirectory = $previous
            Pop-Location
        }

        $record = Join-Path $env:INSTALL_DIR "logs\compose-launch.txt"
        if (-not (Test-Path $record)) { throw "launch record not created" }
        $text = Get-Content $record -Raw
        foreach ($needle in @(
            "cwd=$env:INSTALL_DIR",
            "dotnet_cwd=$env:INSTALL_DIR",
            "compose_command=docker compose --env-file .env -f docker-compose.base.yml up -d --remove-orphans --no-build",
            "compose_ps_command=cd"
        )) {
            if (-not $text.Contains($needle)) { throw "missing $needle" }
        }
    '; then
        pass "PowerShell compose launch record captures install-dir cwd"
    else
        fail "PowerShell compose launch record runtime behavior failed"
    fi
else
    pass "PowerShell runtime behavior skipped (pwsh unavailable)"
fi

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
