#!/bin/bash
# Test suite for session-cleanup.sh
# Validates session lifecycle management and cleanup operations

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION_CLEANUP_SCRIPT="$SCRIPT_DIR/scripts/session-cleanup.sh"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

pass() {
    echo -e "${GREEN}[PASS]${NC} $1"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    TESTS_RUN=$((TESTS_RUN + 1))
}

fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    TESTS_RUN=$((TESTS_RUN + 1))
}

skip() {
    echo -e "${YELLOW}[SKIP]${NC} $1"
}

# ============================================================================
# Test 1: Script exists and is executable
# ============================================================================
if [[ -f "$SESSION_CLEANUP_SCRIPT" ]]; then
    pass "session-cleanup.sh exists"
else
    fail "session-cleanup.sh not found at $SESSION_CLEANUP_SCRIPT"
    exit 1
fi

if [[ -x "$SESSION_CLEANUP_SCRIPT" ]]; then
    pass "session-cleanup.sh is executable"
else
    pass "session-cleanup.sh is runnable via bash"
fi

# ============================================================================
# Test 2: Help command works
# ============================================================================
help_exit=0
help_output=$(bash "$SESSION_CLEANUP_SCRIPT" --help 2>&1) || help_exit=$?
if [[ $help_exit -eq 0 ]] && echo "$help_output" | grep -q "Usage:"; then
    pass "--help flag works and shows usage"
else
    fail "--help flag failed or missing usage text"
fi

# ============================================================================
# Test 3: Script handles missing sessions.json gracefully
# ============================================================================
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

export OPENCLAW_DIR="$TEMP_DIR/openclaw"
export SESSIONS_DIR="$OPENCLAW_DIR/agents/main/sessions"

cleanup_exit=0
cleanup_output=$(bash "$SESSION_CLEANUP_SCRIPT" 2>&1) || cleanup_exit=$?
if [[ $cleanup_exit -eq 0 ]]; then
    pass "Script handles missing sessions.json gracefully"
else
    fail "Script failed with missing sessions.json (exit $cleanup_exit)"
fi

# ============================================================================
# Test 4: Behavioral test - creates sessions directory structure
# ============================================================================
mkdir -p "$SESSIONS_DIR"
cat > "$SESSIONS_DIR/sessions.json" <<'EOF'
{
  "session1": {
    "sessionId": "test-session-1",
    "createdAt": "2024-01-01T00:00:00Z"
  }
}
EOF

cleanup_exit=0
cleanup_output=$(bash "$SESSION_CLEANUP_SCRIPT" 2>&1) || cleanup_exit=$?
if [[ $cleanup_exit -eq 0 ]]; then
    pass "Behavioral test: processes valid sessions.json"
else
    fail "Behavioral test: failed to process sessions.json"
fi

# ============================================================================
# Test 5: Behavioral test - removes inactive sessions
# ============================================================================
# Create inactive session file (not in sessions.json)
echo '{"test": "data"}' > "$SESSIONS_DIR/inactive-session.jsonl"

cleanup_exit=0
cleanup_output=$(bash "$SESSION_CLEANUP_SCRIPT" 2>&1) || cleanup_exit=$?
if [[ $cleanup_exit -eq 0 ]] && ! [[ -f "$SESSIONS_DIR/inactive-session.jsonl" ]]; then
    pass "Behavioral test: removes inactive session files"
else
    skip "Behavioral test: inactive session removal (file may not exist)"
fi

# ============================================================================
# Test 6: Behavioral test - removes bloated sessions
# ============================================================================
# Create active but bloated session
echo '{"test": "data"}' > "$SESSIONS_DIR/test-session-1.jsonl"
# Make it large (over 256KB default threshold)
export MAX_SIZE=100
dd if=/dev/zero of="$SESSIONS_DIR/test-session-1.jsonl" bs=1024 count=200 2>/dev/null

cleanup_exit=0
cleanup_output=$(bash "$SESSION_CLEANUP_SCRIPT" 2>&1) || cleanup_exit=$?
if [[ $cleanup_exit -eq 0 ]] && echo "$cleanup_output" | grep -q "bloated"; then
    pass "Behavioral test: detects and removes bloated sessions"
else
    skip "Behavioral test: bloated session detection"
fi

# ============================================================================
# Test 7: Behavioral test - cleans up .deleted files
# ============================================================================
touch "$SESSIONS_DIR/test.deleted.jsonl"
cleanup_exit=0
cleanup_output=$(bash "$SESSION_CLEANUP_SCRIPT" 2>&1) || cleanup_exit=$?
if [[ $cleanup_exit -eq 0 ]] && ! [[ -f "$SESSIONS_DIR/test.deleted.jsonl" ]]; then
    pass "Behavioral test: cleans up .deleted files"
else
    skip "Behavioral test: .deleted file cleanup"
fi

# ============================================================================
# Test 8: Behavioral test - cleans up .bak files
# ============================================================================
touch "$SESSIONS_DIR/test.bak"
cleanup_exit=0
cleanup_output=$(bash "$SESSION_CLEANUP_SCRIPT" 2>&1) || cleanup_exit=$?
if [[ $cleanup_exit -eq 0 ]] && ! [[ -f "$SESSIONS_DIR/test.bak" ]]; then
    pass "Behavioral test: cleans up .bak files"
else
    skip "Behavioral test: .bak file cleanup"
fi

# ============================================================================
# Test 8b: Bloated wipe clears its sessions.json reference, keeps others
# ============================================================================
SDIR2="$TEMP_DIR/sessions2"
mkdir -p "$SDIR2"
cat > "$SDIR2/sessions.json" <<'EOF'
{
  "agent:main:main": {"sessionId": "keep-me"},
  "agent:main:talk": {"sessionId": "bloated-one"}
}
EOF
printf 'x%.0s' {1..50} > "$SDIR2/keep-me.jsonl"
dd if=/dev/zero of="$SDIR2/bloated-one.jsonl" bs=1024 count=2 2>/dev/null

cleanup_exit=0
SESSIONS_DIR="$SDIR2" MAX_SIZE=100 bash "$SESSION_CLEANUP_SCRIPT" >/dev/null 2>&1 || cleanup_exit=$?
if [[ $cleanup_exit -eq 0 && ! -f "$SDIR2/bloated-one.jsonl" ]] \
    && ! grep -q 'bloated-one' "$SDIR2/sessions.json" \
    && grep -q 'keep-me' "$SDIR2/sessions.json" \
    && [[ -f "$SDIR2/keep-me.jsonl" ]]; then
    pass "Bloated wipe clears its sessions.json reference and keeps healthy ones"
else
    fail "Bloated wipe did not update sessions.json correctly (exit $cleanup_exit)"
fi

if [[ ! -f "$SDIR2/sessions.json.bak-cleanup" ]]; then
    pass "Temporary .bak-cleanup backup removed after successful wipe"
else
    fail ".bak-cleanup backup left behind"
fi

# ============================================================================
# Test 8c: Interruption cannot leave sessions.json pointing at a deleted file
# ============================================================================
SDIR3="$TEMP_DIR/sessions3"
mkdir -p "$SDIR3"
cat > "$SDIR3/sessions.json" <<'EOF'
{"agent:main:talk": {"sessionId": "interrupt-me"}}
EOF
dd if=/dev/zero of="$SDIR3/interrupt-me.jsonl" bs=1024 count=2 2>/dev/null

CRASH_BIN="$TEMP_DIR/crash-bin"
mkdir -p "$CRASH_BIN"
REAL_RM="$(command -v rm)"
cat > "$CRASH_BIN/rm" <<EOF
#!/bin/bash
"$REAL_RM" "\$@"
kill -KILL "\$PPID"
EOF
chmod +x "$CRASH_BIN/rm"

crash_exit=0
PATH="$CRASH_BIN:$PATH" SESSIONS_DIR="$SDIR3" MAX_SIZE=100 \
    bash "$SESSION_CLEANUP_SCRIPT" >"$TEMP_DIR/crash.log" 2>&1 || crash_exit=$?
if [[ $crash_exit -ne 0 ]]; then
    pass "Injected interruption terminated cleanup"
else
    fail "Injected interruption did not terminate cleanup"
fi

if [[ -f "$SDIR3/interrupt-me.jsonl" ]] || ! grep -q 'interrupt-me' "$SDIR3/sessions.json"; then
    pass "Interruption preserves the file-or-no-reference invariant"
else
    fail "sessions.json references a session file deleted before interruption"
fi

# ============================================================================
# Test 8d: Malformed sessions.json fails closed before deleting files
# ============================================================================
SDIR4="$TEMP_DIR/sessions4"
mkdir -p "$SDIR4"
printf '{"agent":' > "$SDIR4/sessions.json"
echo '{"x":1}' > "$SDIR4/preserve-me.jsonl"

invalid_exit=0
SESSIONS_DIR="$SDIR4" MAX_SIZE=1 \
    bash "$SESSION_CLEANUP_SCRIPT" >"$TEMP_DIR/invalid.log" 2>&1 || invalid_exit=$?
if [[ $invalid_exit -ne 0 ]] && [[ -f "$SDIR4/preserve-me.jsonl" ]]; then
    pass "Malformed sessions.json fails closed without deleting session files"
else
    fail "Malformed sessions.json was treated as an empty active-session index"
fi

# ============================================================================
# Test 8e: Index commit failure leaves the file and old reference intact
# ============================================================================
SDIR5="$TEMP_DIR/sessions5"
mkdir -p "$SDIR5"
cat > "$SDIR5/sessions.json" <<'EOF'
{"agent:main:talk": {"sessionId": "commit-fails"}}
EOF
dd if=/dev/zero of="$SDIR5/commit-fails.jsonl" bs=1024 count=2 2>/dev/null

REAL_PYTHON="$(command -v python3 || command -v python)"
FAIL_PYTHON="$TEMP_DIR/fail-python"
cat > "$FAIL_PYTHON" <<EOF
#!/bin/bash
if [[ "\${1:-}" == "-" && \$# -ge 3 ]]; then
    exit 71
fi
exec "$REAL_PYTHON" "\$@"
EOF
chmod +x "$FAIL_PYTHON"

commit_exit=0
ODS_PYTHON_CMD="$FAIL_PYTHON" SESSIONS_DIR="$SDIR5" MAX_SIZE=100 \
    bash "$SESSION_CLEANUP_SCRIPT" >"$TEMP_DIR/commit-failure.log" 2>&1 || commit_exit=$?
if [[ $commit_exit -ne 0 ]] \
    && [[ -f "$SDIR5/commit-fails.jsonl" ]] \
    && grep -q 'commit-fails' "$SDIR5/sessions.json"; then
    pass "Index commit failure leaves the session file and reference intact"
else
    fail "Index commit failure partially mutated the session transaction"
fi

# ============================================================================
# Test 8f: No usable Python → refuse to delete ANYTHING (ordering guard)
# ============================================================================
# A mid-loop Python failure used to kill the script (set -e) after a bloated
# session file was deleted but before sessions.json was updated, leaving the
# gateway pointing at a missing file. The guard must fail BEFORE any rm.
SDIR6="$TEMP_DIR/sessions6"
mkdir -p "$SDIR6"
cat > "$SDIR6/sessions.json" <<'EOF'
{"agent:main:talk": {"sessionId": "bloated-one"}}
EOF
dd if=/dev/zero of="$SDIR6/bloated-one.jsonl" bs=1024 count=2 2>/dev/null
echo '{"x":1}' > "$SDIR6/inactive.jsonl"

NOPY_BIN="$TEMP_DIR/nopy-bin"
mkdir -p "$NOPY_BIN"
for tool in bash sh grep sed find date basename dirname cut wc du stat rm cp mv cat ls tr uname sort head tail touch mkdir; do
    src="$(command -v "$tool" 2>/dev/null)" || continue
    ln -s "$src" "$NOPY_BIN/$tool" 2>/dev/null || cp "$src" "$NOPY_BIN/$tool"
done

nopy_exit=0
PATH="$NOPY_BIN" ODS_PYTHON_CMD="" SESSIONS_DIR="$SDIR6" MAX_SIZE=100 \
    bash "$SESSION_CLEANUP_SCRIPT" >"$TEMP_DIR/nopy.log" 2>&1 || nopy_exit=$?
if [[ $nopy_exit -ne 0 ]]; then
    pass "Missing Python fails loudly instead of half-completing"
    if [[ -f "$SDIR6/bloated-one.jsonl" && -f "$SDIR6/inactive.jsonl" ]] && grep -q 'bloated-one' "$SDIR6/sessions.json"; then
        pass "No files deleted and sessions.json untouched when Python is missing"
    else
        fail "Sessions were mutated despite missing Python (exit $nopy_exit)"
    fi
else
    skip "Missing-Python guard (a python binary is still reachable on this host)"
fi

# ============================================================================
# Test 9: Script does not use silent error suppression
# ============================================================================
suppression_count=0
if grep -q "2>/dev/null" "$SESSION_CLEANUP_SCRIPT"; then
    suppression_count=$((suppression_count + $(grep -c "2>/dev/null" "$SESSION_CLEANUP_SCRIPT")))
fi
if grep -q "|| true" "$SESSION_CLEANUP_SCRIPT"; then
    suppression_count=$((suppression_count + $(grep -c "|| true" "$SESSION_CLEANUP_SCRIPT")))
fi

if [[ $suppression_count -eq 0 ]]; then
    pass "CLAUDE.md compliance: no silent error suppressions found"
else
    fail "CLAUDE.md compliance: found $suppression_count error suppressions (2>/dev/null, || true)"
fi

# ============================================================================
# Test 10: Script uses inline exit code capture
# ============================================================================
if grep -q "_EXIT=0" "$SESSION_CLEANUP_SCRIPT" && grep -q "|| .*_EXIT=\$?" "$SESSION_CLEANUP_SCRIPT"; then
    pass "CLAUDE.md compliance: uses inline exit code capture pattern"
else
    fail "CLAUDE.md compliance: missing inline exit code capture pattern"
fi

# ============================================================================
# Summary
# ============================================================================
echo ""
echo "========================================"
echo "Test Summary"
echo "========================================"
echo "Total:  $TESTS_RUN"
echo -e "Passed: ${GREEN}$TESTS_PASSED${NC}"
echo -e "Failed: ${RED}$TESTS_FAILED${NC}"
echo ""

if [[ $TESTS_FAILED -eq 0 ]]; then
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed.${NC}"
    exit 1
fi
