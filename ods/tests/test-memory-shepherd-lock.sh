#!/bin/bash
# Contracts for the memory-shepherd run lock.
#
# memory-shepherd rewrites agent MEMORY.md files in place. Two runs overlapping
# can archive and reset the same file at the same time, so the lock is the only
# thing standing between a scheduled reset and lost scratch notes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHEPHERD="$SCRIPT_DIR/memory-shepherd/memory-shepherd.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASS=0
FAIL=0

pass() { echo -e "${GREEN}[PASS]${NC} $1"; PASS=$((PASS + 1)); }
fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    [[ -n "${2:-}" ]] && echo "       $2"
    FAIL=$((FAIL + 1))
}

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

LOCKFILE="$TMP_ROOT/shepherd.lock"
CONF="$TMP_ROOT/memory-shepherd.conf"
BASELINE_DIR="$TMP_ROOT/baselines"
mkdir -p "$BASELINE_DIR" "$TMP_ROOT/archives"

# One agent with a real baseline and memory file, so a run that gets past the
# lock does observable work.
head -c 2000 /dev/urandom | base64 > "$BASELINE_DIR/agent.md"
cp "$BASELINE_DIR/agent.md" "$TMP_ROOT/MEMORY.md"
cat > "$CONF" <<EOF
[general]
baseline_dir=$BASELINE_DIR
archive_dir=$TMP_ROOT/archives

[agent]
memory_file=$TMP_ROOT/MEMORY.md
baseline=agent.md
EOF

run_shepherd() {
    MEMORY_SHEPHERD_LOCKFILE="$LOCKFILE" \
    MEMORY_SHEPHERD_CONF="$CONF" \
        bash "$SHEPHERD" all 2>&1 || true
}

# ---------------------------------------------------------------------------
# A blocked run must not delete the lock it was blocked by
# ---------------------------------------------------------------------------
echo "12345" > "$LOCKFILE"
BEFORE="$(cat "$LOCKFILE")"

OUT="$(run_shepherd)"

if grep -q "Another reset running" <<<"$OUT"; then
    pass "a held lock blocks a second run"
else
    fail "a held lock did not block a second run" "$OUT"
fi

if [[ -f "$LOCKFILE" ]]; then
    pass "the blocked run leaves the held lock in place"
else
    fail "the blocked run deleted the lock belonging to the running reset" "$OUT"
fi

if [[ -f "$LOCKFILE" && "$(cat "$LOCKFILE")" == "$BEFORE" ]]; then
    pass "the blocked run does not rewrite the held lock"
else
    fail "the blocked run rewrote the held lock" \
         "expected '$BEFORE', got '$(cat "$LOCKFILE" 2>/dev/null || echo '<missing>')'"
fi

# The lock has to survive being hit repeatedly, not just once.
run_shepherd >/dev/null
run_shepherd >/dev/null
if [[ -f "$LOCKFILE" ]]; then
    pass "the lock survives repeated blocked invocations"
else
    fail "the lock was cleared by a later blocked invocation"
fi

if [[ "$(cat "$TMP_ROOT/MEMORY.md")" == "$(cat "$BASELINE_DIR/agent.md")" ]]; then
    pass "a blocked run performs no agent work"
else
    fail "a blocked run touched the agent memory file"
fi

# ---------------------------------------------------------------------------
# A stale lock is reclaimed, and the run cleans up after itself
# ---------------------------------------------------------------------------
echo "12345" > "$LOCKFILE"
touch -d "-10 minutes" "$LOCKFILE"

OUT="$(run_shepherd)"

if grep -q "Stale lock" <<<"$OUT"; then
    pass "a lock older than the timeout is reported stale"
else
    fail "a stale lock was not reclaimed" "$OUT"
fi

if grep -q "Loaded config" <<<"$OUT"; then
    pass "the run proceeds after reclaiming a stale lock"
else
    fail "the run did not proceed after reclaiming a stale lock" "$OUT"
fi

if [[ ! -e "$LOCKFILE" ]]; then
    pass "a run that owns the lock removes it on exit"
else
    fail "the lock was left behind after a completed run"
fi

# ---------------------------------------------------------------------------
# A clean start acquires and releases
# ---------------------------------------------------------------------------
rm -f "$LOCKFILE"
OUT="$(run_shepherd)"

if grep -q "Loaded config" <<<"$OUT" && [[ ! -e "$LOCKFILE" ]]; then
    pass "a clean run acquires the lock and releases it"
else
    fail "a clean run did not acquire/release cleanly" "$OUT"
fi

echo ""
echo "Passed: $PASS  Failed: $FAIL"
[[ "$FAIL" -eq 0 ]]
echo "[PASS] memory-shepherd lock contracts"
