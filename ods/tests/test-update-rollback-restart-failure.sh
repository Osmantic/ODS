#!/usr/bin/env bash
# ============================================================================
# ods-update.sh rollback restart-failure contract (#4182)
# ============================================================================
# _update_rollback() restored the snapshot and then ran a compose down/up with
# a v1 fallback. The fallback was a bare command, so under `set -euo pipefail`
# its failure killed the script right there: no error, no recovery steps, and
# the caller's own `return 1` never ran. The operator saw a log ending at
# "trying v1..." with the stack down and nothing telling them so.
#
# Strategy: extract the two functions under test and drive them with stub
# `docker` / `docker-compose` binaries on PATH, the same approach as
# tests/test-token-spy-session-manager.sh.
#
# Usage: ./tests/test-update-rollback-restart-failure.sh
# ============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPDATE_SCRIPT="$ROOT_DIR/ods-update.sh"

PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

[[ -f "$UPDATE_SCRIPT" ]] || { echo "[FAIL] ods-update.sh not found"; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/install" "$TMP/snap"

# Extract only the functions under test; the script's top level parses args.
FUNCS="$TMP/funcs.sh"
sed -n '/^_compose_restart() {/,/^}/p;/^_update_rollback() {/,/^}/p' \
    "$UPDATE_SCRIPT" > "$FUNCS"
[[ -s "$FUNCS" ]] || { echo "[FAIL] could not extract rollback functions"; exit 1; }

# run_rollback <docker-up-exits> <docker-compose-exits>
# Emits the driver's stdout+stderr, then a final "rc=<n>" line for the
# function's return status and "END" if the script survived to the end.
run_rollback() {
    local v2_up_exit="$1" v1_exit="$2"

    cat > "$TMP/bin/docker" <<STUB
#!/bin/sh
# 'docker compose down' succeeds; 'docker compose up' exits $v2_up_exit
for a in "\$@"; do [ "\$a" = "up" ] && exit $v2_up_exit; done
exit 0
STUB
    printf '#!/bin/sh\nexit %s\n' "$v1_exit" > "$TMP/bin/docker-compose"
    chmod +x "$TMP/bin/docker" "$TMP/bin/docker-compose"

    {
        echo 'set -euo pipefail'
        echo 'log_info(){ echo "[INFO] $*"; }'
        echo 'log_warn(){ echo "[WARN] $*"; }'
        echo 'log_error(){ echo "[ERROR] $*" >&2; }'
        echo '_restore_snapshot(){ return 0; }'
        echo "INSTALL_DIR=\"$TMP/install\""
        cat "$FUNCS"
        echo 'rc=0; _update_rollback "test reason" "'"$TMP/snap"'" "" || rc=$?'
        echo 'echo "rc=$rc"'
        echo 'echo "END"'
    } > "$TMP/drive.sh"

    PATH="$TMP/bin:$PATH" bash "$TMP/drive.sh" 2>&1 || true
}

# run_rollback_bare <docker-up-exits> <docker-compose-exits>
# Same stubs, but calls _update_rollback as a bare statement exactly as
# cmd_update does. `set -e` is therefore live inside the function, so this is
# what the operator's terminal really shows.
run_rollback_bare() {
    local v2_up_exit="$1" v1_exit="$2"

    cat > "$TMP/bin/docker" <<STUB
#!/bin/sh
for a in "\$@"; do [ "\$a" = "up" ] && exit $v2_up_exit; done
exit 0
STUB
    printf '#!/bin/sh\nexit %s\n' "$v1_exit" > "$TMP/bin/docker-compose"
    chmod +x "$TMP/bin/docker" "$TMP/bin/docker-compose"

    {
        echo 'set -euo pipefail'
        echo 'log_info(){ echo "[INFO] $*"; }'
        echo 'log_warn(){ echo "[WARN] $*"; }'
        echo 'log_error(){ echo "[ERROR] $*" >&2; }'
        echo '_restore_snapshot(){ return 0; }'
        echo "INSTALL_DIR=\"$TMP/install\""
        cat "$FUNCS"
        echo '_update_rollback "test reason" "'"$TMP/snap"'" ""'
        echo 'echo "REACHED_CALLER_RETURN"'
    } > "$TMP/drive-bare.sh"

    PATH="$TMP/bin:$PATH" bash "$TMP/drive-bare.sh" 2>&1 || true
}

# ── 1. both compose implementations fail to bring the stack up ──────────────
out="$(run_rollback 1 1)"

if grep -q "rc=1" <<< "$out"; then
    pass "_update_rollback reports failure to its caller"
else
    fail "expected rc=1 from _update_rollback, got: $(grep -o 'rc=[0-9]*' <<< "$out" || echo none)"
fi

if grep -q "CRITICAL: Snapshot was restored but the services did not come back up" <<< "$out"; then
    pass "operator is told the services are down"
else
    fail "no CRITICAL message naming the failed restart"
fi

if grep -q "ods-update.sh health" <<< "$out" && grep -q "docker compose up -d" <<< "$out"; then
    pass "recovery steps are printed"
else
    fail "recovery steps missing from the failure output"
fi

if grep -q "Rollback complete" <<< "$out"; then
    fail "claimed 'Rollback complete' even though the stack never came up"
else
    pass "does not claim 'Rollback complete' on a failed restart"
fi

# ── 1b. bare call, as cmd_update makes it: `set -e` is live ─────────────────
# The script still exits (the pre-existing _restore_snapshot failure branch
# behaves the same way), but the operator must not be left guessing: the
# diagnosis has to reach the terminal before the exit.
bare="$(run_rollback_bare 1 1)"

if grep -q "CRITICAL: Snapshot was restored but the services did not come back up" <<< "$bare"; then
    pass "under a bare call the diagnosis is printed before the script exits"
else
    fail "bare call exits without telling the operator anything; output was:"
    printf '%s\n' "$bare" | sed 's/^/       /'
fi

if grep -q "Rollback complete" <<< "$bare"; then
    fail "bare call still claimed 'Rollback complete'"
else
    pass "bare call does not claim 'Rollback complete'"
fi

# ── 2. the v1 fallback still rescues a v2 failure ───────────────────────────
out="$(run_rollback 1 0)"
if grep -q "Rollback complete" <<< "$out" && grep -q "rc=0" <<< "$out"; then
    pass "v1 fallback still succeeds and reports completion"
else
    fail "v1 fallback path regressed; output was:"
    printf '%s\n' "$out" | sed 's/^/       /'
fi

# ── 3. the happy path is unchanged ──────────────────────────────────────────
out="$(run_rollback 0 1)"
if grep -q "Rollback complete" <<< "$out" && grep -q "rc=0" <<< "$out"; then
    pass "compose v2 success path reports completion"
else
    fail "happy path regressed; output was:"
    printf '%s\n' "$out" | sed 's/^/       /'
fi

echo "------------------------------------------------------------"
echo "PASS=$PASS FAIL=$FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
