#!/usr/bin/env bash
# ============================================================================
# Dream Server Windows installer flag parity tests
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$ROOT_DIR/.." && pwd)"
ROOT_INSTALLER="$REPO_ROOT/install.ps1"
WINDOWS_INSTALLER="$ROOT_DIR/installers/windows/install-windows.ps1"
WINDOWS_QUICKSTART="$ROOT_DIR/docs/WINDOWS-QUICKSTART.md"

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
echo "=== Windows installer flag parity tests ==="
echo ""

[[ -f "$ROOT_INSTALLER" ]] && pass "root Windows installer wrapper exists" || fail "root Windows installer wrapper missing"
[[ -f "$WINDOWS_INSTALLER" ]] && pass "Windows installer orchestrator exists" || fail "Windows installer orchestrator missing"

for flag in Hermes NoHermes Langfuse NoLangfuse NoBootstrap; do
    check "[switch]\$$flag" "$ROOT_INSTALLER" "root wrapper forwards -$flag"
done

check "[switch]\$NoBootstrap" "$WINDOWS_INSTALLER" "Windows installer exposes -NoBootstrap"
check '$noBootstrapFlag = $NoBootstrap.IsPresent' "$WINDOWS_INSTALLER" "Windows installer captures -NoBootstrap"
check '-NoBootstrap $noBootstrapFlag' "$WINDOWS_INSTALLER" "Windows bootstrap decision receives -NoBootstrap"

check '| `-NoHermes` | Disable Hermes Agent |' "$WINDOWS_QUICKSTART" "Windows quickstart documents -NoHermes"
check '| `-NoBootstrap` | Wait for the full model before launching |' "$WINDOWS_QUICKSTART" "Windows quickstart documents -NoBootstrap"

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
