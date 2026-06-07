#!/usr/bin/env bash
# ============================================================================
# Dream Server Windows stop native-helper ordering tests
# ============================================================================
# Static regression for the Windows reinstall failure where Docker Desktop was
# stopped, so dream.ps1 stop exited before stopping DreamServerHostAgent. The
# host agent kept dream-host-agent.log open and blocked reinstall cleanup.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DREAM_PS1="$ROOT_DIR/installers/windows/dream.ps1"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASS=0
FAIL=0

pass() { echo -e "  ${GREEN}PASS${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}FAIL${NC} $1"; FAIL=$((FAIL + 1)); }

line_of() {
    local needle="$1"
    grep -nF -- "$needle" <<<"$stop_block" | head -n 1 | cut -d: -f1 || true
}

echo ""
echo "=== Windows stop native-helper ordering tests ==="
echo ""

[[ -f "$DREAM_PS1" ]] && pass "dream.ps1 exists" || fail "dream.ps1 missing"

stop_block="$(awk '
    /function Invoke-Stop/ { in_block=1 }
    in_block { print }
    in_block && /function Invoke-Restart/ { exit }
' "$DREAM_PS1")"

[[ -n "$stop_block" ]] && pass "Invoke-Stop block extracted" || fail "Invoke-Stop block missing"

service_guard_line="$(line_of 'if (-not $Service) {')"
agent_stop_line="$(line_of 'Invoke-Agent -Action "stop"')"
native_stop_line="$(line_of 'Stop-NativeInferenceServer')"
test_install_line="$(line_of 'Test-Install')"
compose_down_line="$(line_of '-ComposeArgs @("down")')"

if [[ -n "$service_guard_line" && -n "$agent_stop_line" && "$agent_stop_line" -gt "$service_guard_line" ]]; then
    pass "all-services stop has a native-helper preflight branch"
else
    fail "all-services stop should gate native-helper cleanup behind if (-not \$Service)"
fi

if [[ -n "$agent_stop_line" && -n "$test_install_line" && "$agent_stop_line" -lt "$test_install_line" ]]; then
    pass "host agent stops before Docker-dependent Test-Install"
else
    fail "host agent must stop before Test-Install can fail on Docker Desktop"
fi

if [[ -n "$native_stop_line" && -n "$test_install_line" && "$native_stop_line" -lt "$test_install_line" ]]; then
    pass "native inference stops before Docker-dependent Test-Install"
else
    fail "native inference must stop before Test-Install can fail on Docker Desktop"
fi

if [[ -n "$agent_stop_line" && -n "$compose_down_line" && "$agent_stop_line" -lt "$compose_down_line" ]]; then
    pass "host agent stops before docker compose down"
else
    fail "host agent should not wait for docker compose down"
fi

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
