#!/usr/bin/env bash
# Regression test for demo-offline.sh under Bash 3.2 (macOS default shell).
#
# demo-offline.sh is launched directly by users from the README.md instructions.
# Parameter case modification (${choice,,}) was added in Bash 4.0 and causes
# bad substitution under /bin/bash 3.2 on macOS.
#
# Run from repo root:  bash ods/tests/test-demo-offline-bash32.sh
# Or from ods:         bash tests/test-demo-offline-bash32.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DEMO="$ROOT_DIR/scripts/demo-offline.sh"

PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

[[ -f "$DEMO" ]] || { echo "demo-offline.sh not found at $DEMO" >&2; exit 1; }

# 1. Static: Ensure demo-offline.sh contains no Bash 4 case-modification expansions (${var,,} or ${var^^})
if grep -nE '\$\{[a-zA-Z_0-9]+,,|\$\{[a-zA-Z_0-9]+\^\^' "$DEMO"; then
    fail "demo-offline.sh contains non-portable Bash 4 case expansion"
else
    pass "no non-portable case expansion syntax in demo-offline.sh"
fi

# 2. Dynamic: Verify exit on option 'q' under /bin/bash with simulated terminal input
if [[ -x /bin/bash ]]; then
    out="$(printf "q\n" | /bin/bash "$DEMO" 2>&1)"
    rc=$?
    if [[ $rc -eq 0 && "$out" == *"Thanks for watching!"* ]]; then
        pass "demo-offline.sh exits cleanly on option q under /bin/bash (rc=0)"
    else
        fail "demo-offline.sh failed under /bin/bash (rc=$rc): $(printf '%s' "$out" | tail -n 2 | tr '\n' '|')"
    fi
fi

echo ""
echo "Passed: $PASS  Failed: $FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
echo "[PASS] demo-offline.sh Bash 3.2 compatibility passed"
