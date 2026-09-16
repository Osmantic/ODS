#!/usr/bin/env bash
# `ods update` must not report success for an update that never ran (#4179),
# and its fallback pre-update backup must actually be invocable (#4175).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ODS_CLI="$ROOT/ods-cli"
ODS_BACKUP="$ROOT/ods-backup.sh"

PASS=0; FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL+1)); }

# ── #4175: the exact argv cmd_update uses must be accepted by ods-backup.sh ──
# A bare positional hits the parser's `*)` catch-all, so the fallback snapshot
# could never succeed and the update proceeded with only a warning.
label="pre-update-20260101-000000"

set +e
out_positional="$("$ODS_BACKUP" "$label" 2>&1)"; rc_positional=$?
set -e
if [ "$rc_positional" -ne 0 ] && printf '%s' "$out_positional" | grep -q "Unknown option"; then
    pass "a bare positional label is rejected by ods-backup.sh (the old call)"
else
    fail "expected ods-backup.sh to reject a positional label; rc=$rc_positional"
fi

if grep -q -- '--description "\$_snap_label"' "$ODS_CLI"; then
    pass "cmd_update passes the label via --description"
else
    fail "cmd_update must invoke ods-backup.sh with --description"
fi

if grep -q '"\$INSTALL_DIR/ods-backup.sh" "\$_snap_label"' "$ODS_CLI"; then
    fail "cmd_update still passes the label as a bare positional"
else
    pass "no bare-positional invocation remains"
fi

# ── #4179: abort paths must not all collapse to exit 0 ──────────────────────
# Drive the real function with stubbed logging; 2 = operator declined,
# 1 = tool refused, and cmd_update must map only the former to success.
FUNCS="$(mktemp)"; trap 'rm -f "$FUNCS"' EXIT
sed -n '/^_check_version_compat() {/,/^}/p' "$ODS_CLI" > "$FUNCS"
[ -s "$FUNCS" ] || { echo "[FAIL] could not extract _check_version_compat"; exit 1; }

if grep -q 'log "Update cancelled."' "$FUNCS" && \
   awk '/log "Update cancelled."/{getline; if ($0 ~ /return 2/) found=1} END{exit !found}' "$FUNCS"; then
    pass "an operator declining returns the distinct 'declined' code"
else
    fail "the cancel path must be distinguishable from a hard block"
fi

if awk '/UPGRADE BLOCKED/{f=1} f&&/return 1/{found=1} END{exit !found}' "$FUNCS"; then
    pass "the hard compatibility block still returns a failure code"
else
    fail "the hard block must return a failure code"
fi

# The mapping in cmd_update: declined -> 0, refused -> non-zero.
map=$(sed -n '/Version compatibility check/,/^    fi$/p' "$ODS_CLI")
if printf '%s' "$map" | grep -q '_compat_rc.*-eq 2' && printf '%s' "$map" | grep -q 'exit 1'; then
    pass "cmd_update exits non-zero when the tool refuses the update"
else
    fail "cmd_update must exit non-zero on a refused update; got: $map"
fi

if printf '%s' "$map" | grep -qE 'if ! _check_version_compat'; then
    fail "cmd_update still collapses every abort reason into one branch"
else
    pass "abort reasons are no longer collapsed"
fi

echo "------------------------------------------------------------"
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
