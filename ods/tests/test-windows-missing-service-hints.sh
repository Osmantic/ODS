#!/bin/bash
# ============================================================================
# ODS Windows missing compose service hint tests
# ============================================================================
# Static checks for friendly Windows CLI errors when an optional service is not
# present in the active .compose-flags stack.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ODS_PS1="$ROOT_DIR/installers/windows/ods.ps1"

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
echo "=== Windows missing compose service hint tests ==="
echo ""

[[ -f "$ODS_PS1" ]] && pass "ods.ps1 exists" || fail "ods.ps1 missing"

check 'function Write-ODSMissingComposeServiceHint' "$ODS_PS1" "missing-service hint helper exists"
check "Service '\$Service' is not in the active ODS compose stack." "$ODS_PS1" "hint explains service is absent from active stack"
check 'compose.yaml.disabled' "$ODS_PS1" "hint checks disabled extension compose fragment"
check 'active .compose-flags stack does not include it' "$ODS_PS1" "hint explains stale/mismatched compose flags"
check 'n8n is optional. Install with -Workflows or -All' "$ODS_PS1" "hint gives n8n/workflows remediation"
check 'docker compose @flags config --services' "$ODS_PS1" "hint prints diagnostic compose-services command"

guard_count=$(grep -Fc 'Write-ODSMissingComposeServiceHint -ComposeFlags $flags -Service $Service' "$ODS_PS1" || true)
if [[ "$guard_count" -ge 4 ]]; then
    pass "start/stop/restart/logs guard missing services before docker compose"
else
    fail "expected missing-service guard in start/stop/restart/logs, found $guard_count"
fi

logs_block="$(awk '
    /function Invoke-Logs/ { in_block=1 }
    in_block { print }
    in_block && /function Invoke-ConfigShow/ { exit }
' "$ODS_PS1")"

if grep -Fq 'Test-ODSComposeServiceAvailable -ComposeFlags $flags -Service $Service' <<<"$logs_block" &&
   grep -Fq 'Write-ODSMissingComposeServiceHint -ComposeFlags $flags -Service $Service' <<<"$logs_block"; then
    pass "logs command checks compose service availability before tailing logs"
else
    fail "logs command does not guard missing services before tailing logs"
fi

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
