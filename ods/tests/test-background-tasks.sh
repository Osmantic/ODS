#!/usr/bin/env bash
# ============================================================================
# ODS — Background Task Registry Tests
# ============================================================================
# Part of: tests/
# Purpose: The registry lives at a fixed path under /tmp and is shared by
#          every run on the host. Re-running the installer registers the same
#          well-known ids ("sdxl-download", "full-model-download") again, so
#          a lookup must answer for the run that is actually in flight.
#
# Usage: ./test-background-tasks.sh
# ============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$SCRIPT_DIR/../installers/lib/background-tasks.sh"

TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

assert_eq() {
    local label="$1" expected="$2" actual="$3"
    TESTS_RUN=$((TESTS_RUN + 1))
    if [[ "$expected" == "$actual" ]]; then
        echo "  [PASS] $label"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        echo "  [FAIL] $label: expected '$expected', got '$actual'"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
}

new_registry() {
    BG_TASK_REGISTRY="$(mktemp -u)"
    export BG_TASK_REGISTRY
}

registry_len() {
    python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' "$BG_TASK_REGISTRY"
}

dead_pid() {
    # A pid that has certainly exited: spawn, reap, reuse the number.
    local p
    sleep 0 &
    p=$!
    wait "$p" 2>/dev/null
    echo "$p"
}

# shellcheck source=../installers/lib/background-tasks.sh
. "$LIB"

# ── A re-run must not inherit the previous run's record ────────────────────

test_reregistering_replaces_stale_record() {
    echo "Testing: re-registering a task id replaces the stale record"
    new_registry

    bg_task_start "sdxl-download" "$(dead_pid)" "previous run" "/nonexistent.log"

    sleep 30 &
    local live=$!
    bg_task_start "sdxl-download" "$live" "current run" "/nonexistent.log"

    assert_eq "one record per task id" "1" "$(registry_len)"

    bg_task_status "sdxl-download"
    assert_eq "status reports the live task as running" "0" "$?"

    kill "$live" 2>/dev/null
    wait "$live" 2>/dev/null
    rm -f "$BG_TASK_REGISTRY"
}

test_legacy_duplicates_resolve_to_newest() {
    echo "Testing: a registry with duplicate ids answers for the newest record"
    new_registry
    local live
    sleep 30 &
    live=$!
    # Hand-built registry in the pre-dedup shape, stale record first.
    python3 - "$BG_TASK_REGISTRY" "$(dead_pid)" "$live" <<'PY'
import json, sys
json.dump(
    [
        {"id": "full-model-download", "pid": int(sys.argv[2]),
         "description": "previous run", "log_file": "/nonexistent.log",
         "status": "running"},
        {"id": "full-model-download", "pid": int(sys.argv[3]),
         "description": "current run", "log_file": "/nonexistent.log",
         "status": "running"},
    ],
    open(sys.argv[1], "w"),
)
PY

    bg_task_status "full-model-download"
    assert_eq "newest record wins" "0" "$?"

    kill "$live" 2>/dev/null
    wait "$live" 2>/dev/null
    rm -f "$BG_TASK_REGISTRY"
}

# ── A junk file at the shared /tmp path must not abort the installer ───────

test_corrupt_registry_is_recoverable() {
    echo "Testing: a corrupt registry does not abort the caller"
    new_registry
    printf 'not json at all' > "$BG_TASK_REGISTRY"

    bg_task_start "sdxl-download" "$$" "current run" "/nonexistent.log"
    assert_eq "bg_task_start succeeds over a corrupt registry" "0" "$?"
    assert_eq "registry rebuilt with just this task" "1" "$(registry_len)"

    rm -f "$BG_TASK_REGISTRY"
}

test_corrupt_registry_status_is_not_found() {
    echo "Testing: status over a corrupt registry reports not-found"
    new_registry
    printf '{"not": "a list"}' > "$BG_TASK_REGISTRY"

    bg_task_status "sdxl-download"
    assert_eq "status returns 3 (not found)" "3" "$?"

    rm -f "$BG_TASK_REGISTRY"
}

# ── Existing contract still holds ──────────────────────────────────────────

test_status_codes_unchanged() {
    echo "Testing: documented status codes are unchanged"
    new_registry

    bg_task_status "never-registered"
    assert_eq "missing registry -> 3" "3" "$?"

    local live
    sleep 30 &
    live=$!
    bg_task_start "running-task" "$live" "d" "/nonexistent.log"
    bg_task_status "running-task"
    assert_eq "running -> 0" "0" "$?"
    bg_task_status "other-task"
    assert_eq "unknown id -> 3" "3" "$?"
    kill "$live" 2>/dev/null
    wait "$live" 2>/dev/null

    bg_task_start "finished-task" "$(dead_pid)" "d" "/nonexistent.log"
    bg_task_status "finished-task"
    assert_eq "exited with no log -> 1" "1" "$?"

    local logfile
    logfile="$(mktemp)"
    echo "ERROR: download aborted" > "$logfile"
    bg_task_start "broken-task" "$(dead_pid)" "d" "$logfile"
    bg_task_status "broken-task"
    assert_eq "exited with an error log -> 2" "2" "$?"
    rm -f "$logfile" "$BG_TASK_REGISTRY"
}

echo "=== Background Task Registry Tests ==="
echo
test_reregistering_replaces_stale_record
echo
test_legacy_duplicates_resolve_to_newest
echo
test_corrupt_registry_is_recoverable
echo
test_corrupt_registry_status_is_not_found
echo
test_status_codes_unchanged

echo
echo "=== Test Summary ==="
echo "Tests run:    $TESTS_RUN"
echo "Tests passed: $TESTS_PASSED"
if [[ $TESTS_FAILED -gt 0 ]]; then
    echo "Tests failed: $TESTS_FAILED"
    exit 1
fi
echo "All tests passed!"
exit 0
