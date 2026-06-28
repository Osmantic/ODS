#!/usr/bin/env bats
# ============================================================================
# BATS tests for installers/lib/logging.sh
# ============================================================================
# Tests: install_elapsed(), log(), success(), warn(), error()

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    # Define color vars as empty strings (no ANSI escape codes in test output)
    export GRN=""
    export BGRN=""
    export AMB=""
    export RED=""
    export NC=""

    # LOG_FILE as a temp file
    export LOG_FILE="$BATS_TEST_TMPDIR/logging-test.log"
    touch "$LOG_FILE"

    # INSTALL_START_EPOCH set to a known value
    export INSTALL_START_EPOCH=$(date +%s)
    export INSTALL_NOW_EPOCH="$INSTALL_START_EPOCH"

    # Source the library under test
    source "$BATS_TEST_DIRNAME/../../installers/lib/logging.sh"
}

# ── install_elapsed ─────────────────────────────────────────────────────────

@test "install_elapsed: returns 0m 00s when just started" {
    INSTALL_START_EPOCH=1700000000
    INSTALL_NOW_EPOCH=1700000000
    run install_elapsed
    assert_success
    assert_output "0m 00s"
}

@test "install_elapsed: returns correct elapsed time for 65 seconds" {
    INSTALL_START_EPOCH=1700000000
    INSTALL_NOW_EPOCH=1700000065
    run install_elapsed
    assert_success
    assert_output "1m 05s"
}

@test "install_elapsed: returns correct elapsed time for 0 seconds" {
    INSTALL_START_EPOCH=1700000000
    INSTALL_NOW_EPOCH=1700000000
    run install_elapsed
    assert_success
    assert_output "0m 00s"
}

@test "install_elapsed: returns correct elapsed time for 120 seconds" {
    INSTALL_START_EPOCH=1700000000
    INSTALL_NOW_EPOCH=1700000120
    run install_elapsed
    assert_success
    assert_output "2m 00s"
}

# ── log ─────────────────────────────────────────────────────────────────────

@test "log: writes message to stdout" {
    run log "test message"
    assert_success
    assert_output --partial "[INFO]"
    assert_output --partial "test message"
}

@test "log: appends message to LOG_FILE" {
    log "file message"
    run cat "$LOG_FILE"
    assert_output --partial "[INFO]"
    assert_output --partial "file message"
}

# ── success ─────────────────────────────────────────────────────────────────

@test "success: writes message to stdout with OK tag" {
    run success "all good"
    assert_success
    assert_output --partial "[OK]"
    assert_output --partial "all good"
}

@test "success: appends message to LOG_FILE" {
    success "all good"
    run cat "$LOG_FILE"
    assert_output --partial "[OK]"
    assert_output --partial "all good"
}

# ── warn ────────────────────────────────────────────────────────────────────

@test "warn: writes message to stdout with WARN tag" {
    run warn "be careful"
    assert_success
    assert_output --partial "[WARN]"
    assert_output --partial "be careful"
}

@test "warn: appends message to LOG_FILE" {
    warn "be careful"
    run cat "$LOG_FILE"
    assert_output --partial "[WARN]"
    assert_output --partial "be careful"
}

# ── error ───────────────────────────────────────────────────────────────────

@test "error: writes to stderr and exits 1" {
    run error "something broke"
    assert_failure 1
    assert_output --partial "[ERROR]"
    assert_output --partial "something broke"
}

@test "error: appends message to LOG_FILE" {
    # Run in subshell to catch exit
    run error "crash now"
    # Even though error exits, tee should have written before exiting
    run cat "$LOG_FILE"
    assert_output --partial "[ERROR]"
    assert_output --partial "crash now"
}
