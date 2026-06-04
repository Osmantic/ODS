#!/bin/bash
# ============================================================================
# Dream Server Windows install readiness summary tests
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SUMMARY_LIB="$ROOT_DIR/installers/windows/lib/readiness-summary.ps1"
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
echo "=== Windows install readiness summary tests ==="
echo ""

[[ -f "$SUMMARY_LIB" ]] && pass "readiness summary library exists" || fail "readiness summary library missing"
check 'readiness-summary.ps1' "$INSTALL_PS1" "Windows installer sources readiness summary library"
check 'function Write-DreamInstallReadinessSummary' "$SUMMARY_LIB" "summary writer function exists"
check 'Test-DreamReadinessHttp' "$SUMMARY_LIB" "summary probes HTTP endpoints"
check 'Get-DreamReadinessContainerState' "$SUMMARY_LIB" "summary reads Docker container state"
check 'Write-DreamInstallReadinessSummary -Checks $readinessChecks' "$INSTALL_PS1" "installer prints readiness summary"
check '$windowsEnvMap = Get-WindowsDreamEnvMap -InstallDir $installDir' "$INSTALL_PS1" "installer caches Windows env map for health checks"
check '$healthWhisperPort' "$INSTALL_PS1" "installer health check uses configured Whisper port"
check 'Assert-DreamWindowsManagedContainers' "$INSTALL_PS1" "installer asserts compose-managed containers exist"
check 'Docker Compose did not create any managed Windows containers' "$INSTALL_PS1" "installer fails loud on zero Windows containers"
check '-RequiredServices @("dashboard", "dashboard-api", "open-webui")' "$INSTALL_PS1" "installer requires core Windows container services"
check 'Dream Server is not being marked fully healthy until readiness recovers.' "$INSTALL_PS1" "installer avoids success card wording on degraded readiness"

if command -v pwsh >/dev/null 2>&1; then
    OUTPUT="$(SUMMARY_LIB="$SUMMARY_LIB" pwsh -NoProfile -Command '
        $ErrorActionPreference = "Stop"
        . $env:SUMMARY_LIB
        function Test-DreamReadinessHttp {
            param([string]$Url, [int]$TimeoutSec = 3)
            if ($Url -like "*3001*") { return @{ Code = 200; Ready = $true } }
            return @{ Code = 0; Ready = $false }
        }
        function Get-DreamReadinessContainerState {
            param([string]$Container)
            if ($Container -eq "dream-webui") { return "running" }
            if ($Container -eq "dream-qdrant") { return "missing" }
            return "healthy"
        }
        $checks = @(
            @{ Name = "Dashboard"; Url = "http://localhost:3001"; Container = "dream-dashboard"; OpenUrl = "http://localhost:3001" },
            @{ Name = "Chat UI"; Url = "http://localhost:3000"; Container = "dream-webui"; OpenUrl = "http://localhost:3000" },
            @{ Name = "Qdrant"; Url = "http://localhost:6333"; Container = "dream-qdrant"; OpenUrl = "http://localhost:6333" }
        )
        Write-DreamInstallReadinessSummary -Checks $checks -StatusCommand ".\dream.ps1 status" -LogPath "C:\dream\logs\install.log" -DashboardUrl "http://localhost:3001"
    ')"

    [[ "$OUTPUT" == *"INSTALL READINESS"* ]] && pass "runtime summary has heading" || fail "runtime summary missing heading"
    [[ "$OUTPUT" == *"Ready now: 1/3"* ]] && pass "runtime summary counts ready services" || fail "runtime summary count mismatch"
    [[ "$OUTPUT" == *"[OK] Dashboard"* ]] && pass "runtime summary lists ready service" || fail "runtime summary missing ready service"
    [[ "$OUTPUT" == *"[!!] Chat UI"* ]] && pass "runtime summary lists starting service" || fail "runtime summary missing starting service"
    [[ "$OUTPUT" == *"[!!] Qdrant"* ]] && pass "runtime summary lists missing service" || fail "runtime summary missing missing service"
else
    pass "PowerShell runtime behavior skipped (pwsh unavailable)"
fi

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
