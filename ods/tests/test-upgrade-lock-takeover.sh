#!/usr/bin/env bash
# Copyright (C) 2026 Lingga Louis Channels
# Licensed under the GNU Affero General Public License v3.0 (AGPLv3)
#
# Regression test: acquire_upgrade_lock in scripts/bootstrap-upgrade.sh must
# guarantee a single owner when several contenders race to reclaim a stale
# lock. The old rm -rf-then-mkdir sequence was not atomic: two contenders
# could both observe a dead pid, both delete the directory, and both mkdir —
# each believing it held the lock while a concurrent model swap ran.
#
# The fixture extracts the real functions and slows `rm` via a PATH stub so
# the vulnerable window is held open deterministically.

set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_ROOT="$(cd "${TEST_DIR}/.." && pwd)"
SCRIPT="${ODS_ROOT}/scripts/bootstrap-upgrade.sh"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

# --- extract the functions under test ---------------------------------------
EXTRACT="$TMP_ROOT/lock-fns.sh"
sed -n '/^release_upgrade_lock()/,/^}/p' "$SCRIPT" >"$EXTRACT"
sed -n '/^acquire_upgrade_lock()/,/^}/p' "$SCRIPT" >>"$EXTRACT"
grep -q 'acquire_upgrade_lock()' "$EXTRACT" || {
    echo "FAIL: could not extract acquire_upgrade_lock" >&2
    exit 1
}

INSTALL_DIR="$TMP_ROOT/install"
mkdir -p "$INSTALL_DIR"
export TMPDIR="$TMP_ROOT/locks"
mkdir -p "$TMPDIR"
FULL_GGUF_FILE=test.gguf
# shellcheck disable=SC2034  # consumed by the extracted functions under test
STATUS_FILE="$TMP_ROOT/status.json"
# shellcheck disable=SC2034
UPGRADE_LOCK_DIR=""

log() { :; }
write_existing_upgrade_status() { :; }
release_model_router_swap_gate() { :; }
release_model_lifecycle_lock() { :; }

# shellcheck source=/dev/null
. "$EXTRACT"

LOCK_KEY="$(printf '%s\0%s' "$INSTALL_DIR" "$FULL_GGUF_FILE" | cksum | awk '{print $1}')"
LOCK_DIR="$TMPDIR/ods-bootstrap-upgrade-${LOCK_KEY}.lock"

fail() { echo "FAIL: $*" >&2; exit 1; }

seed_dead_lock() {
    rm -rf "$LOCK_DIR"
    mkdir -p "$LOCK_DIR"
    sleep 0.01 &
    local dead_pid=$!
    wait "$dead_pid" 2>/dev/null || true
    printf '%s\n' "$dead_pid" >"$LOCK_DIR/pid"
}

echo "=== bootstrap-upgrade lock takeover ==="

# Case 1: a stale lock (dead pid) is reclaimed and carries the caller's pid.
seed_dead_lock
(
    acquire_upgrade_lock
    [[ -f "$LOCK_DIR/pid" ]] && cat "$LOCK_DIR/pid" >"$TMP_ROOT/case1.pid"
)
[[ -f "$TMP_ROOT/case1.pid" ]] || fail "stale lock was not reclaimed"
[[ "$(cat "$TMP_ROOT/case1.pid")" == "$$" ]] \
    || fail "reclaimed lock holds wrong pid: $(cat "$TMP_ROOT/case1.pid")"
echo "PASS: stale lock is reclaimed by the caller"

# Case 2: a live owner's lock is respected — contender exits without stealing.
rm -rf "$LOCK_DIR"
mkdir -p "$LOCK_DIR"
printf '%s\n' "$$" >"$LOCK_DIR/pid"
(
    acquire_upgrade_lock
    echo "stole" >"$TMP_ROOT/case2.stole"
)
[[ ! -f "$TMP_ROOT/case2.stole" ]] || fail "contender proceeded past a live lock"
[[ "$(cat "$LOCK_DIR/pid")" == "$$" ]] || fail "live lock was modified"
echo "PASS: live lock is respected"

# Case 3: a lock dir without a pid file is eventually reclaimed (owner died
# mid-acquire) instead of looping forever.
rm -rf "$LOCK_DIR"
mkdir -p "$LOCK_DIR"
(
    acquire_upgrade_lock
    echo ok >"$TMP_ROOT/case3.ok"
)
[[ -f "$TMP_ROOT/case3.ok" ]] || fail "pid-less lock was never reclaimed"
echo "PASS: pid-less lock is reclaimed"

# Case 4: concurrent contenders on a stale lock produce exactly one owner.
# The stubbed `rm` sleeps a per-contender RM_DELAY: contender 1 reclaims the
# stale lock quickly while contenders 2-4 are still parked inside their own
# `rm -rf` call — so their removals land on the lock contender 1 just
# acquired. Without an atomic takeover every contender ends up holding the
# "lock" at once.
RACE_BIN="$TMP_ROOT/race-bin"
mkdir -p "$RACE_BIN"
RM_REAL="$(command -v rm)"
# shellcheck disable=SC2016  # ${RM_DELAY} must stay literal inside the stub
printf '#!/usr/bin/env bash\nsleep "${RM_DELAY:-0}"\nexec %s "$@"\n' "$RM_REAL" >"$RACE_BIN/rm"
chmod +x "$RACE_BIN/rm"

MARKERS="$TMP_ROOT/markers"
mkdir -p "$MARKERS"
rm -rf "$LOCK_DIR"
seed_dead_lock
for i in 1 2 3 4; do
    delay=3
    [[ "$i" -eq 1 ]] && delay=0.5
    (
        PATH="$RACE_BIN:$PATH"
        # shellcheck disable=SC2034  # consumed by the rm stub via env
        RM_DELAY="$delay"
        acquire_upgrade_lock
        touch "$MARKERS/winner-$i"
    ) &
done
wait
winners=$(find "$MARKERS" -type f | wc -l)
[[ "$winners" -eq 1 ]] \
    || fail "$winners contenders believed they held the upgrade lock"
echo "PASS: exactly one contender owns the lock under a stale-lock race"

echo "PASS: all upgrade-lock takeover cases"
