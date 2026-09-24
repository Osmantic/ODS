#!/usr/bin/env bash
# Regression: a wedged model lifecycle lock holder must not block later
# installer/upgrader/CLI operations forever. The lock wait is bounded by
# ODS_MODEL_LIFECYCLE_LOCK_WAIT_SECONDS (default 3600) and the timeout
# reports the last recorded holder.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK_LIB="$ROOT_DIR/installers/lib/model-lifecycle-lock.sh"

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

[[ -f "$LOCK_LIB" ]] || fail "missing model lifecycle lock helper"
# shellcheck source=installers/lib/model-lifecycle-lock.sh
. "$LOCK_LIB"

# Static contract: the blocking fallback must carry a bounded wait.
grep -n 'flock -x -w ' "$LOCK_LIB" >/dev/null \
    || fail "model lifecycle lock fallback must bound flock with -w"
grep -n 'ODS_MODEL_LIFECYCLE_LOCK_WAIT_SECONDS' "$LOCK_LIB" >/dev/null \
    || fail "lock wait bound must be operator-tunable"
pass "lock wait is bounded and tunable"

if ! command -v flock >/dev/null 2>&1; then
    echo "[SKIP] flock is unavailable; static contracts passed"
    exit 0
fi
if [[ "$(uname -s)" != "Linux" ]]; then
    echo "[SKIP] lifecycle lock is Linux-only; static contracts passed"
    exit 0
fi

tmp="$(mktemp -d)"
holder_pid=""
cleanup() {
    [[ -n "$holder_pid" ]] && kill "$holder_pid" 2>/dev/null || true
    rm -rf "$tmp"
}
trap cleanup EXIT

install_dir="$tmp/install"
mkdir -p "$install_dir"

export ODS_MODEL_LIFECYCLE_LOCK_ROOT="$tmp/locks"
export ODS_TEST_LOCK_LIB="$LOCK_LIB"
export ODS_TEST_INSTALL_DIR="$install_dir"
export ODS_TEST_EVENTS="$tmp/events"
export ODS_TEST_WAIT_SECONDS=2

# Holder: acquires the lock and sleeps past the wait bound, simulating a
# wedged model download/activation.
bash -c '
    set -euo pipefail
    . "$ODS_TEST_LOCK_LIB"
    ods_model_lifecycle_lock_acquire "$ODS_TEST_INSTALL_DIR" "wedged holder"
    printf "holder-acquired\n" >> "$ODS_TEST_EVENTS"
    sleep 30
' &
holder_pid=$!

for _wait in $(seq 1 100); do
    grep -q "holder-acquired" "$tmp/events" 2>/dev/null && break
    sleep 0.05
done
grep -q "holder-acquired" "$tmp/events" \
    || fail "test holder did not acquire the lifecycle lock"

# Holder identity must be recorded in the lock file for diagnostics.
lock_file="$(ods_model_lifecycle_lock_file "$install_dir")"
holder_line="$(tail -n 1 "$lock_file")"
[[ "$holder_line" == pid=*"actor=wedged holder"* ]] \
    || fail "acquisition must record holder identity in the lock file (got: $holder_line)"
pass "lock file records holder pid/actor/timestamp"

# Waiter: must fail within the bound instead of hanging on `flock -x`.
# Outer `timeout` catches an unbounded implementation (exit 124).
wait_start="$SECONDS"
set +e
waiter_output="$(timeout 15 env ODS_MODEL_LIFECYCLE_LOCK_WAIT_SECONDS="$ODS_TEST_WAIT_SECONDS" bash -c '
    set -euo pipefail
    . "$ODS_TEST_LOCK_LIB"
    ods_model_lifecycle_lock_acquire "$ODS_TEST_INSTALL_DIR" "waiting operation"
' 2>&1)"
waiter_rc=$?
set -e
elapsed=$((SECONDS - wait_start))

[[ "$waiter_rc" -eq 1 ]] \
    || fail "waiter must fail cleanly once the bound expires (rc=$waiter_rc, output: $waiter_output)"
(( elapsed < 15 )) \
    || fail "waiter outlived the bound; lock wait is unbounded (elapsed=${elapsed}s)"
[[ "$waiter_output" == *"Timed out after ${ODS_TEST_WAIT_SECONDS}s"* ]] \
    || fail "timeout must be reported to the operator (got: $waiter_output)"
[[ "$waiter_output" == *"actor=wedged holder"* ]] \
    || fail "timeout must name the last recorded lock holder (got: $waiter_output)"
pass "wedged holder produces a bounded failure naming the holder"

# Serialization must still work: a waiter inside the bound acquires after release.
kill "$holder_pid" 2>/dev/null || true
wait "$holder_pid" 2>/dev/null || true
holder_pid=""

ODS_MODEL_LIFECYCLE_LOCK_WAIT_SECONDS=10 bash -c '
    set -euo pipefail
    . "$ODS_TEST_LOCK_LIB"
    ods_model_lifecycle_lock_acquire "$ODS_TEST_INSTALL_DIR" "post-release waiter"
    ods_model_lifecycle_lock_release
'
pass "lock still serializes normally once the holder releases"

# Releasing the lock must not leave stderr permanently redirected: the fd-close
# redirect is scoped to the close itself, not the rest of the caller's life.
stderr_after_release="$(bash -c '
    set -euo pipefail
    . "$ODS_TEST_LOCK_LIB"
    ods_model_lifecycle_lock_acquire "$ODS_TEST_INSTALL_DIR" "stderr check"
    ods_model_lifecycle_lock_release
    echo "stderr-still-alive" >&2
' 2>&1 >/dev/null)"
[[ "$stderr_after_release" == *"stderr-still-alive"* ]] \
    || fail "lock release must not permanently redirect the caller's stderr"
pass "lock release leaves stderr intact"

# A non-numeric bound falls back to the default instead of crashing.
bash -c '
    set -euo pipefail
    . "$ODS_TEST_LOCK_LIB"
    export ODS_MODEL_LIFECYCLE_LOCK_WAIT_SECONDS="bogus"
    ods_model_lifecycle_lock_acquire "$ODS_TEST_INSTALL_DIR" "sanity waiter"
    ods_model_lifecycle_lock_release
'
pass "non-numeric wait bound falls back to the default"
