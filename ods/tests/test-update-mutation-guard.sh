#!/usr/bin/env bash
# Cross-process Assistant First update/extension mutation serialization.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

# shellcheck source=../lib/python-cmd.sh
source "$ROOT_DIR/lib/python-cmd.sh"
PYTHON_CMD=$(ods_detect_python_cmd 2>/dev/null || true)
[[ -n "$PYTHON_CMD" ]] || fail "a runnable Python interpreter is required"
for required in jq curl timeout; do
    command -v "$required" >/dev/null 2>&1 || fail "$required is required"
done

TMP="$(mktemp -d)"
holder_pid=""
wrapper_pid=""
cleanup() {
    [[ -z "$holder_pid" ]] || kill "$holder_pid" 2>/dev/null || true
    [[ -z "$wrapper_pid" ]] || kill "$wrapper_pid" 2>/dev/null || true
    rm -rf -- "$TMP"
}
trap cleanup EXIT

mkdir -p "$TMP/data"
if "$PYTHON_CMD" -c 'import os,sys; sys.exit(0 if os.name == "nt" else 1)' \
    >/dev/null 2>&1; then
    set +e
    native_output=$("$PYTHON_CMD" \
        "$ROOT_DIR/scripts/run-with-extension-mutation-guard.py" \
        --lock-parent "$TMP/data" --timeout 0 -- bash -c true 2>&1)
    native_status=$?
    set -e
    [[ "$native_status" -eq 69 ]] \
        || fail "native Windows guard refusal returned $native_status instead of 69"
    grep -q "not yet qualified for native Windows source updates" \
        <<<"$native_output" \
        || fail "native Windows guard refusal was not actionable"
    pass "native Windows Assistant First source mutation fails closed"
    exit 0
fi

INSTALL="$TMP/install"
mkdir -p "$INSTALL/lib" "$INSTALL/scripts" \
    "$INSTALL/extensions/services/dashboard-api" "$INSTALL/data" "$TMP/home"
chmod go-w "$INSTALL/data"
cp "$ROOT_DIR/ods-update.sh" "$INSTALL/ods-update.sh"
cp "$ROOT_DIR/lib/python-cmd.sh" "$INSTALL/lib/python-cmd.sh"
cp "$ROOT_DIR/scripts/run-with-extension-mutation-guard.py" \
    "$INSTALL/scripts/run-with-extension-mutation-guard.py"
cp "$ROOT_DIR/extensions/services/dashboard-api/extension_operation_locks.py" \
    "$INSTALL/extensions/services/dashboard-api/extension_operation_locks.py"
chmod +x "$INSTALL/ods-update.sh"
cat > "$INSTALL/.env" <<'EOF'
ODS_INSTALL_PROFILE=assistant-first
ODS_MODE=local
GPU_BACKEND=cpu
EOF
printf '%s\n' '{"version":"test"}' > "$INSTALL/.version"

cat > "$TMP/hold_guard.py" <<'PY'
import json
import sys
import time
from pathlib import Path

module_dir = Path(sys.argv[1])
sys.path.insert(0, str(module_dir))
import extension_operation_locks as locks

data_dir = Path(sys.argv[2])
ready = Path(sys.argv[3])
release = Path(sys.argv[4])
guard = locks.mutation_guard_path(data_dir, repair_mode=True)
with locks.exclusive_file_lock(guard):
    path_info = guard.stat()
    directory_info = guard.parent.stat()
    ready.write_text(
        json.dumps(
            {
                "path": str(guard),
                "device": path_info.st_dev,
                "inode": path_info.st_ino,
                "mode": format(directory_info.st_mode & 0o777, "o"),
            }
        ),
        encoding="utf-8",
    )
    while not release.exists():
        time.sleep(0.02)
PY

wait_for_file() {
    local path="$1" count=0
    while [[ ! -f "$path" && "$count" -lt 250 ]]; do
        sleep 0.02
        count=$((count + 1))
    done
    [[ -f "$path" ]] || fail "timed out waiting for $path"
}

run_update_command() {
    local command="$1" output="$2"
    shift 2
    set +e
    HOME="$TMP/home" ODS_MUTATION_GUARD_TIMEOUT=0.05 \
        timeout 15 bash "$INSTALL/ods-update.sh" "$command" "$@" \
        >"$output" 2>&1
    RUN_STATUS=$?
    set -e
}

ready="$TMP/holder-ready.json"
release="$TMP/holder-release"
"$PYTHON_CMD" "$TMP/hold_guard.py" \
    "$INSTALL/extensions/services/dashboard-api" "$INSTALL/data" \
    "$ready" "$release" &
holder_pid=$!
wait_for_file "$ready"
[[ "$(jq -r '.mode' "$ready")" == "700" ]] \
    || fail "global guard directory was not mode 0700"

run_update_command update "$TMP/update-busy.out"
[[ "$RUN_STATUS" -eq 75 ]] \
    || fail "update contention returned $RUN_STATUS instead of 75"
grep -q "Another ODS update or extension mutation is in progress" \
    "$TMP/update-busy.out" || fail "update contention was not actionable"
! grep -q "Starting ODS update" "$TMP/update-busy.out" \
    || fail "contended update entered the mutation window"

set +e
HOME="$TMP/home" ODS_MUTATION_GUARD_TIMEOUT=0.05 \
    ODS_MUTATION_GUARD_FD=1 timeout 15 \
    bash "$INSTALL/ods-update.sh" update \
    >"$TMP/update-spoofed-fd.out" 2>&1
spoofed_fd_status=$?
set -e
[[ "$spoofed_fd_status" -eq 75 ]] \
    || fail "spoofed guard descriptor returned $spoofed_fd_status instead of 75"
grep -q "Another ODS update or extension mutation is in progress" \
    "$TMP/update-spoofed-fd.out" \
    || fail "spoofed guard descriptor bypassed canonical contention"
! grep -q "Starting ODS update" "$TMP/update-spoofed-fd.out" \
    || fail "spoofed guard descriptor entered the mutation window"
pass "an arbitrary open descriptor cannot impersonate the mutation guard"

run_update_command rollback "$TMP/rollback-busy.out"
[[ "$RUN_STATUS" -eq 75 ]] \
    || fail "rollback contention returned $RUN_STATUS instead of 75"
grep -q "Another ODS update or extension mutation is in progress" \
    "$TMP/rollback-busy.out" || fail "rollback contention was not actionable"

run_update_command status "$TMP/status.out"
[[ "$RUN_STATUS" -eq 0 ]] || fail "read-only status was blocked by the guard"
pass "update and rollback contend while read-only status remains available"

touch "$release"
wait "$holder_pid"
holder_pid=""

run_update_command update "$TMP/update-released.out"
[[ "$RUN_STATUS" -ne 0 && "$RUN_STATUS" -ne 75 && "$RUN_STATUS" -ne 124 ]] \
    || fail "released update did not proceed beyond the mutation guard"
grep -q "git-backed ODS source checkout" "$TMP/update-released.out" \
    || fail "released update did not reach its normal source precondition"
! grep -q "Another ODS update or extension mutation" "$TMP/update-released.out" \
    || fail "released update still reported guard contention"
pass "kernel release allows the next source update to reach normal validation"

child_ready="$TMP/wrapped-child-ready"
child_release="$TMP/wrapped-child-release"
# Arguments expand inside the child Bash.
# shellcheck disable=SC2016
HOME="$TMP/home" timeout 15 "$PYTHON_CMD" \
    "$INSTALL/scripts/run-with-extension-mutation-guard.py" \
    --lock-parent "$INSTALL/data" --timeout 1 -- \
    bash -c 'printf ready > "$1"; while [[ ! -f "$2" ]]; do sleep 0.02; done' \
    bash "$child_ready" "$child_release" >"$TMP/wrapper.out" 2>&1 &
wrapper_pid=$!
wait_for_file "$child_ready"

set +e
HOME="$TMP/home" timeout 15 "$PYTHON_CMD" \
    "$INSTALL/scripts/run-with-extension-mutation-guard.py" \
    --lock-parent "$INSTALL/data" --timeout 0.05 -- bash -c true \
    >"$TMP/wrapper-busy.out" 2>&1
competitor_status=$?
set -e
[[ "$competitor_status" -eq 75 ]] \
    || fail "exec-held guard competitor returned $competitor_status instead of 75"

touch "$child_release"
wait "$wrapper_pid"
wrapper_pid=""
HOME="$TMP/home" timeout 15 "$PYTHON_CMD" \
    "$INSTALL/scripts/run-with-extension-mutation-guard.py" \
    --lock-parent "$INSTALL/data" --timeout 1 -- bash -c true
pass "the inherited descriptor holds across exec and releases on child exit"

echo "[PASS] Assistant First update mutation guard contract"
