#!/usr/bin/env bats
# ============================================================================
# BATS tests for installers/lib/background-tasks.sh
# ============================================================================
# Tests: bg_task_start(), bg_task_status(), bg_task_summary()

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    # Skip all tests if python3 is not available
    if ! command -v python3 &>/dev/null; then
        skip "python3 not available"
    fi

    # Stub UI functions that background-tasks.sh expects
    ai() { :; }; export -f ai
    ai_ok() { :; }; export -f ai_ok
    ai_warn() { :; }; export -f ai_warn
    ai_bad() { :; }; export -f ai_bad

    export LOG_FILE="$BATS_TEST_TMPDIR/bg-tasks-test.log"
    touch "$LOG_FILE"

    # Set BG_TASK_REGISTRY to a temp file (unique per test)
    export BG_TASK_REGISTRY="$BATS_TEST_TMPDIR/bg-tasks-registry.json"
    rm -f "$BG_TASK_REGISTRY"

    # Source the library under test
    source "$BATS_TEST_DIRNAME/../../installers/lib/background-tasks.sh"
}

# ── bg_task_start ───────────────────────────────────────────────────────────

@test "bg_task_start: creates registry file when it does not exist" {
    [[ ! -f "$BG_TASK_REGISTRY" ]]
    bg_task_start "test-task" "12345" "Test task" "/tmp/test.log"
    [[ -f "$BG_TASK_REGISTRY" ]]
}

@test "bg_task_start: adds entry to registry" {
    bg_task_start "my-task" "99999" "My background task" "/tmp/my-task.log"

    # Verify the registry contains the task
    run python3 -c "
import json
tasks = json.load(open('$BG_TASK_REGISTRY'))
assert len(tasks) == 1
assert tasks[0]['id'] == 'my-task'
assert tasks[0]['pid'] == 99999
assert tasks[0]['description'] == 'My background task'
assert tasks[0]['log_file'] == '/tmp/my-task.log'
assert tasks[0]['status'] == 'running'
print('OK')
"
    assert_success
    assert_output "OK"
}

@test "bg_task_start: appends multiple tasks to registry" {
    bg_task_start "task-1" "11111" "First task" "/tmp/t1.log"
    bg_task_start "task-2" "22222" "Second task" "/tmp/t2.log"
    bg_task_start "task-3" "33333" "Third task" "/tmp/t3.log"

    run python3 -c "
import json
tasks = json.load(open('$BG_TASK_REGISTRY'))
assert len(tasks) == 3
ids = [t['id'] for t in tasks]
assert 'task-1' in ids
assert 'task-2' in ids
assert 'task-3' in ids
print('OK')
"
    assert_success
    assert_output "OK"
}

# ── bg_task_status ──────────────────────────────────────────────────────────

@test "bg_task_status: returns 3 when no registry exists" {
    rm -f "$BG_TASK_REGISTRY"
    run bg_task_status "nonexistent"
    assert_failure
    [[ "$status" -eq 3 ]]
}

@test "bg_task_status: returns 3 for unknown task" {
    echo "[]" > "$BG_TASK_REGISTRY"
    run bg_task_status "unknown-task"
    assert_failure
    [[ "$status" -eq 3 ]]
}

@test "bg_task_status: returns 0 for a running process" {
    # Start a background sleep process
    sleep 60 &
    local bg_pid=$!

    bg_task_start "running-task" "$bg_pid" "Running task" "/tmp/running.log"
    run bg_task_status "running-task"
    assert_success  # exit code 0 = running

    # Clean up
    kill "$bg_pid" 2>/dev/null || true
    wait "$bg_pid" 2>/dev/null || true
}

@test "bg_task_status: returns non-zero for a completed process" {
    # Use a PID that is definitely not running (PID 1 is init, but
    # use a finished process instead)
    local temp_script="$BATS_TEST_TMPDIR/quick-exit.sh"
    echo '#!/bin/bash' > "$temp_script"
    echo 'exit 0' >> "$temp_script"
    chmod +x "$temp_script"

    "$temp_script" &
    local finished_pid=$!
    wait "$finished_pid" 2>/dev/null || true

    local task_log="$BATS_TEST_TMPDIR/completed-task.log"
    echo "Task completed successfully" > "$task_log"

    bg_task_start "done-task" "$finished_pid" "Done task" "$task_log"
    run bg_task_status "done-task"
    # Should return 1 (completed) since no ERROR in log
    [[ "$status" -eq 1 ]]
}

@test "bg_task_status: returns 2 for a failed process with ERROR in log" {
    # Use a process that has already exited
    local temp_script="$BATS_TEST_TMPDIR/fail-exit.sh"
    echo '#!/bin/bash' > "$temp_script"
    echo 'exit 0' >> "$temp_script"
    chmod +x "$temp_script"

    "$temp_script" &
    local finished_pid=$!
    wait "$finished_pid" 2>/dev/null || true

    local task_log="$BATS_TEST_TMPDIR/failed-task.log"
    echo "ERROR: something went wrong" > "$task_log"

    bg_task_start "failed-task" "$finished_pid" "Failed task" "$task_log"
    run bg_task_status "failed-task"
    [[ "$status" -eq 2 ]]
}

# ── bg_task_summary ─────────────────────────────────────────────────────────

@test "bg_task_summary: reports no tasks when registry missing" {
    rm -f "$BG_TASK_REGISTRY"
    run bg_task_summary
    assert_success
    assert_output "No background tasks registered"
}

@test "bg_task_summary: reports no tasks when registry is empty array" {
    echo "[]" > "$BG_TASK_REGISTRY"
    run bg_task_summary
    assert_success
    assert_output "No background tasks registered"
}

@test "bg_task_summary: lists registered tasks" {
    # Create a quick-exit process
    local temp_script="$BATS_TEST_TMPDIR/summary-exit.sh"
    echo '#!/bin/bash' > "$temp_script"
    echo 'exit 0' >> "$temp_script"
    chmod +x "$temp_script"

    "$temp_script" &
    local finished_pid=$!
    wait "$finished_pid" 2>/dev/null || true

    local task_log="$BATS_TEST_TMPDIR/summary-task.log"
    echo "All done" > "$task_log"

    bg_task_start "summary-task" "$finished_pid" "Summary test" "$task_log"
    run bg_task_summary
    assert_success
    assert_output --partial "Background tasks: 1"
    assert_output --partial "summary-task"
    assert_output --partial "Summary test"
}

@test "bg_task_summary: shows correct count for multiple tasks" {
    # Register two tasks with non-running PIDs
    local temp_script="$BATS_TEST_TMPDIR/multi-exit.sh"
    echo '#!/bin/bash' > "$temp_script"
    echo 'exit 0' >> "$temp_script"
    chmod +x "$temp_script"

    "$temp_script" &
    local pid1=$!
    wait "$pid1" 2>/dev/null || true

    "$temp_script" &
    local pid2=$!
    wait "$pid2" 2>/dev/null || true

    bg_task_start "task-a" "$pid1" "Task Alpha" "$BATS_TEST_TMPDIR/a.log"
    bg_task_start "task-b" "$pid2" "Task Beta" "$BATS_TEST_TMPDIR/b.log"
    run bg_task_summary
    assert_success
    assert_output --partial "Background tasks: 2"
    assert_output --partial "task-a"
    assert_output --partial "task-b"
}
