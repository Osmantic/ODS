#!/usr/bin/env bash
# ============================================================================
# install.sh pass-through flag parity
# ----------------------------------------------------------------------------
# install.sh documents a fixed list of pass-through options in its header
# comment and forwards "$@" verbatim to install-core.sh. Every documented flag
# must therefore appear as a case arm in install-core.sh's argument parser —
# otherwise a documented flag aborts the install with "Unknown option".
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DISPATCHER="$ROOT_DIR/install.sh"
CORE="$ROOT_DIR/install-core.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASS=0
FAIL=0

pass() { echo -e "  ${GREEN}PASS${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}FAIL${NC} $1"; FAIL=$((FAIL + 1)); }

echo ""
echo "=== install.sh pass-through flag parity ==="
echo ""

[[ -f "$DISPATCHER" ]] && pass "install.sh exists" || { fail "install.sh missing"; echo "$FAIL failure(s)"; exit 1; }
[[ -f "$CORE" ]] && pass "install-core.sh exists" || { fail "install-core.sh missing"; echo "$FAIL failure(s)"; exit 1; }

# The dispatcher header advertises pass-through options on consecutive comment
# lines starting with "# --". Extract every documented long flag.
documented="$(sed -n 's/^# \(--[a-z0-9-].*\)/\1/p' "$DISPATCHER" | tr ' ' '\n' \
    | grep -oE '^--[a-z0-9-]+' | sort -u)"

[[ -n "$documented" ]] && pass "dispatcher documents pass-through flags" \
    || fail "no pass-through flags parsed from install.sh header"

while IFS= read -r flag; do
    # Each documented flag must be a case pattern in install-core.sh's parser
    # (either as its own arm like `--foo)` or combined like `-h|--help)`).
    if grep -Eq -- "(^|[[:space:]|])${flag}\)" "$CORE"; then
        pass "install-core.sh accepts documented flag $flag"
    else
        fail "install.sh documents $flag but install-core.sh rejects it as unknown"
    fi
done <<< "$documented"

# --bootstrap specifically must leave bootstrap mode enabled.
if grep -Fq -- '--bootstrap) NO_BOOTSTRAP=false; shift ;;' "$CORE"; then
    pass "--bootstrap keeps NO_BOOTSTRAP=false"
else
    fail "--bootstrap is not wired to NO_BOOTSTRAP=false"
fi

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[[ "$FAIL" -eq 0 ]]
