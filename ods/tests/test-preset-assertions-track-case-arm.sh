#!/usr/bin/env bash
# test-preset-import-export.sh must assert against the real case arm, not a
# fixed grep window (#5635).
#
# Its checks used `grep -A N "export|e)"`. As the arm grew, needles fell out of
# the window and correct code was reported as broken — `cd "$PRESETS_DIR"` sits
# 19 lines after the label against a -A15 window, and `tar czf` at +20 against
# -A20 passed by one line. This pins that the assertions track the block and
# still fail when the code is genuinely wrong.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUITE="$ROOT/tests/test-preset-import-export.sh"
REAL_CLI="$ROOT/ods-cli"

PASS=0; FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL+1)); }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

run_against() { ODS_CLI="$1" bash "$SUITE" > "$TMP/out" 2>&1 || true; }
failed_count() { grep -cE '^\[0;31m✗\[0m|^✗ ' "$TMP/out" 2>/dev/null || echo 0; }

# ── 1. the shipped CLI is clean ─────────────────────────────────────────────
run_against "$REAL_CLI"
if grep -qE "All tests passed" "$TMP/out"; then
    pass "assertions pass against the shipped ods-cli"
else
    fail "false failure against the shipped CLI"
    sed 's/^/       /' "$TMP/out" | tail -12
fi

# ── 2. a grown case arm must not break them — the #5635 regression ──────────
awk '{print} index($0,"export|e)"){for(i=0;i<40;i++) print "            # padding"}' \
    "$REAL_CLI" > "$TMP/padded-cli"
run_against "$TMP/padded-cli"
if grep -qE "All tests passed" "$TMP/out"; then
    pass "assertions survive a case arm grown by 40 lines"
else
    fail "a longer case arm still breaks the assertions"
    grep -E '✗' "$TMP/out" | sed 's/^/       /' | head -6
fi

# ── 3. they must still catch genuinely wrong code ───────────────────────────
sed 's|^            cd "\$PRESETS_DIR"$|            # removed for this check|' \
    "$REAL_CLI" > "$TMP/tampered-cli"
if grep -q 'cd "\$PRESETS_DIR"' "$TMP/tampered-cli"; then
    echo "[SKIP] could not stage the tampered CLI (source line changed shape)"
else
    run_against "$TMP/tampered-cli"
    if grep -q "Export may create absolute paths" "$TMP/out"; then
        pass "removing the relative-archive cd is still caught"
    else
        fail "tampered CLI passed — the assertions no longer test anything"
        sed 's/^/       /' "$TMP/out" | tail -8
    fi
fi

echo "------------------------------------------------------------"
echo "PASS=$PASS FAIL=$FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
