#!/bin/bash
# Functional tests for scripts/llm-cold-storage.sh archive/restore safety:
#  - first-run archive creates the cold dir and verifies the move + symlink
#  - failed moves are reported (non-zero exit), not logged as ARCHIVED
#  - restore refuses to nest into an existing real directory

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COLD_SCRIPT="$SCRIPT_DIR/../scripts/llm-cold-storage.sh"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

pass() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }
info() { echo -e "${BLUE}ℹ${NC} $1"; }

[[ -f "$COLD_SCRIPT" ]] || fail "llm-cold-storage.sh not found at $COLD_SCRIPT"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

MODEL="models--Test--IdleModel"

# Fresh fixture: one idle model in the fake HF cache. Idle-ness comes from
# an atime far in the past (script archives models idle 7+ days).
make_fixture() {
    rm -rf "$TMP_ROOT/hub" "$TMP_ROOT/cold" "$TMP_ROOT/log"
    mkdir -p "$TMP_ROOT/hub/$MODEL/snapshots"
    echo "weights" > "$TMP_ROOT/hub/$MODEL/snapshots/model.bin"
    touch -a -t 202601010000 "$TMP_ROOT/hub/$MODEL/snapshots/model.bin"
    mkdir -p "$TMP_ROOT/log"
}

run_cold() {
    HF_CACHE="$TMP_ROOT/hub" \
    COLD_DIR="$TMP_ROOT/cold" \
    LOG_FILE="$TMP_ROOT/log/cold.log" \
        bash "$COLD_SCRIPT" "$@"
}

info "Test 1: --execute on a fresh system creates COLD_DIR and archives"
make_fixture
# COLD_DIR intentionally does not exist yet — first run must create it.
run_cold --execute >/dev/null
[[ -d "$TMP_ROOT/cold/$MODEL" ]] || fail "model not moved to cold storage"
[[ -L "$TMP_ROOT/hub/$MODEL" ]] || fail "cache symlink not created"
[[ "$(readlink "$TMP_ROOT/hub/$MODEL")" == "$TMP_ROOT/cold/$MODEL" ]] || fail "symlink points to wrong target"
pass "first-run archive moves model and leaves a symlink"

info "Test 2: re-running --execute skips the already-archived symlink"
run_cold --execute >/dev/null
[[ -d "$TMP_ROOT/cold/$MODEL" && ! -d "$TMP_ROOT/cold/$MODEL/$MODEL" ]] || fail "re-run nested or duplicated the archive"
pass "second run is idempotent"

info "Test 3: --restore moves the model back and removes the symlink"
run_cold --restore "Test/IdleModel" >/dev/null
[[ -d "$TMP_ROOT/hub/$MODEL" && ! -L "$TMP_ROOT/hub/$MODEL" ]] || fail "model not restored as a real directory"
[[ ! -e "$TMP_ROOT/cold/$MODEL" ]] || fail "cold copy still present after restore"
pass "restore round-trip works"

info "Test 4: archive fails loudly when the cold destination already exists"
make_fixture
mkdir -p "$TMP_ROOT/cold/$MODEL"
echo "stale" > "$TMP_ROOT/cold/$MODEL/marker"
set +e
run_cold --execute >/dev/null 2>&1
rc=$?
set -e
[[ $rc -ne 0 ]] || fail "expected non-zero exit when destination exists"
[[ -d "$TMP_ROOT/hub/$MODEL" && ! -L "$TMP_ROOT/hub/$MODEL" ]] || fail "model should be left in place on failure"
[[ -f "$TMP_ROOT/cold/$MODEL/marker" ]] || fail "existing cold entry must not be clobbered"
grep -q "ERROR" "$TMP_ROOT/log/cold.log" || fail "failure not logged as ERROR"
! grep -q "^.*ARCHIVED: $MODEL" "$TMP_ROOT/log/cold.log" || fail "failed archive must not log ARCHIVED"
pass "destination collision is a reported failure, not a fake success"

info "Test 5: restore refuses to overwrite a real directory in the cache"
make_fixture
run_cold --execute >/dev/null
# Simulate a partial re-download: replace the symlink with a real directory.
rm "$TMP_ROOT/hub/$MODEL"
mkdir -p "$TMP_ROOT/hub/$MODEL"
echo "partial" > "$TMP_ROOT/hub/$MODEL/partial.bin"
set +e
run_cold --restore "Test/IdleModel" >/dev/null 2>&1
rc=$?
set -e
[[ $rc -ne 0 ]] || fail "expected non-zero exit when cache path is a real directory"
[[ -d "$TMP_ROOT/cold/$MODEL" ]] || fail "cold copy must remain when restore is refused"
[[ ! -d "$TMP_ROOT/hub/$MODEL/$MODEL" ]] || fail "restore must not nest into the existing directory"
pass "restore refuses to nest into an existing directory"

echo ""
pass "All llm-cold-storage tests passed"
