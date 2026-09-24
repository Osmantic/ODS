#!/usr/bin/env bash
# Locking protocol regression coverage for memory-shepherd/memory-shepherd.sh.
#
# Two shipped units drive the same script — memory-shepherd-workspace.timer
# every 60s (running it twice per activation) and memory-shepherd-memory.timer
# every 3h — so overlapping invocations are routine, and the lock is the only
# thing keeping two runs off the same MEMORY.md.
#
# Run: bash tests/test-memory-shepherd-lock.sh

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHEPHERD="$ROOT_DIR/memory-shepherd/memory-shepherd.sh"

PASS=0
FAIL=0

pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; echo "       $2"; FAIL=$((FAIL + 1)); }

check_eq() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        pass "$label"
    else
        fail "$label" "expected [$expected] got [$actual]"
    fi
}

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

LOCKFILE="$WORKDIR/shepherd.lock"
CONF="$WORKDIR/memory-shepherd.conf"
MEMORY_FILE="$WORKDIR/MEMORY.md"

mkdir -p "$WORKDIR/baselines"
cat > "$WORKDIR/baselines/test-agent-MEMORY.md" <<'EOF'
# Test agent baseline

Durable instructions that survive every reset.
EOF

cat > "$CONF" <<EOF
[general]
baseline_dir=$WORKDIR/baselines
archive_dir=$WORKDIR/archives
min_baseline_size=10
separator=---

[test-agent]
memory_file=$MEMORY_FILE
baseline=test-agent-MEMORY.md
EOF

seed_memory() {
    cat > "$MEMORY_FILE" <<'EOF'
# Test agent baseline

Durable instructions that survive every reset.
---
## Scratch Notes
transient note that should be archived
EOF
}

run_shepherd() {
    MEMORY_SHEPHERD_CONF="$CONF" \
    MEMORY_SHEPHERD_LOCKFILE="$LOCKFILE" \
        bash "$SHEPHERD" all > "$1" 2>&1
    echo $?
}

# ── 1. A lock held by another run must survive this run's exit ────────────
#
# The regression: the EXIT trap was armed before the "is another run already
# going?" check, so the losing invocation deleted the winner's lockfile on its
# way out and the next invocation started alongside the run still in progress.

seed_memory
echo "424242" > "$LOCKFILE"          # someone else's pid, mtime = now
OUT="$WORKDIR/out1.txt"
rc="$(run_shepherd "$OUT")"

check_eq "busy lock: exits 0" "0" "$rc"

if grep -q "Another reset running" "$OUT"; then
    pass "busy lock: reports that another reset is running"
else
    fail "busy lock: reports that another reset is running" "$(cat "$OUT")"
fi

if [[ -f "$LOCKFILE" ]]; then
    pass "busy lock: the other run's lockfile still exists"
else
    fail "busy lock: the other run's lockfile still exists" "lockfile was deleted"
fi

check_eq "busy lock: the other run's pid is untouched" "424242" "$(cat "$LOCKFILE" 2>/dev/null || echo MISSING)"

if grep -q "transient note" "$MEMORY_FILE"; then
    pass "busy lock: memory file is left alone"
else
    fail "busy lock: memory file is left alone" "memory file was reset while another run held the lock"
fi

# ── 2. A stale lock is reclaimed and the run proceeds ─────────────────────

seed_memory
echo "424242" > "$LOCKFILE"
touch -t 202001010000 "$LOCKFILE"    # far past the 120s staleness window
OUT="$WORKDIR/out2.txt"
rc="$(run_shepherd "$OUT")"

check_eq "stale lock: exits 0" "0" "$rc"

if grep -q "Stale lock" "$OUT"; then
    pass "stale lock: reports the stale lock"
else
    fail "stale lock: reports the stale lock" "$(cat "$OUT")"
fi

if grep -q "transient note" "$MEMORY_FILE"; then
    fail "stale lock: run proceeds and resets the memory file" "scratch notes still present"
else
    pass "stale lock: run proceeds and resets the memory file"
fi

if [[ -f "$LOCKFILE" ]]; then
    fail "stale lock: run releases its own lock on exit" "lockfile still present"
else
    pass "stale lock: run releases its own lock on exit"
fi

# ── 3. Uncontended run acquires and releases the lock ─────────────────────

seed_memory
rm -f "$LOCKFILE"
OUT="$WORKDIR/out3.txt"
rc="$(run_shepherd "$OUT")"

check_eq "clean run: exits 0" "0" "$rc"

if [[ -f "$LOCKFILE" ]]; then
    fail "clean run: releases its own lock on exit" "lockfile still present"
else
    pass "clean run: releases its own lock on exit"
fi

if grep -q "Durable instructions" "$MEMORY_FILE" && ! grep -q "transient note" "$MEMORY_FILE"; then
    pass "clean run: memory file is reset to the baseline"
else
    fail "clean run: memory file is reset to the baseline" "$(cat "$MEMORY_FILE")"
fi

if compgen -G "$WORKDIR/archives/test-agent/*.md" > /dev/null; then
    pass "clean run: scratch notes are archived before the reset"
else
    fail "clean run: scratch notes are archived before the reset" "no archive file written"
fi

# ── Summary ───────────────────────────────────────────────────────────────

echo ""
echo "Passed: $PASS  Failed: $FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
echo "[PASS] memory-shepherd locking protocol"
