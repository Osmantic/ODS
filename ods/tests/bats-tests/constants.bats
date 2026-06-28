#!/usr/bin/env bats
# ============================================================================
# BATS tests for installers/lib/constants.sh
# ============================================================================
# Tests: VERSION, color codes, _sed_i()

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    # constants.sh sources path-utils.sh via SCRIPT_DIR.
    # Point SCRIPT_DIR to the real ods directory so path-utils.sh
    # can be found at $SCRIPT_DIR/installers/lib/path-utils.sh.
    export SCRIPT_DIR="$BATS_TEST_DIRNAME/../.."

    # Prevent constants.sh from overwriting SCRIPT_DIR via its own detection
    # by pre-setting LOG_FILE so we can verify it's used
    export LOG_FILE="$BATS_TEST_TMPDIR/constants-test.log"

    # Source the library under test
    source "$BATS_TEST_DIRNAME/../../installers/lib/constants.sh"
}

# ── VERSION ─────────────────────────────────────────────────────────────────

@test "VERSION: is set and non-empty" {
    [[ -n "$VERSION" ]]
}

@test "VERSION: matches semver pattern" {
    [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

# ── Color codes ─────────────────────────────────────────────────────────────

@test "color codes: RED is defined" {
    [[ -n "$RED" ]]
}

@test "color codes: GRN is defined" {
    [[ -n "$GRN" ]]
}

@test "color codes: BGRN is defined" {
    [[ -n "$BGRN" ]]
}

@test "color codes: DGRN is defined" {
    [[ -n "$DGRN" ]]
}

@test "color codes: AMB is defined" {
    [[ -n "$AMB" ]]
}

@test "color codes: WHT is defined" {
    [[ -n "$WHT" ]]
}

@test "color codes: NC is defined" {
    [[ -n "$NC" ]]
}

@test "color codes: CURSOR is defined" {
    [[ -n "$CURSOR" ]]
}

# ── INSTALL_START_EPOCH ─────────────────────────────────────────────────────

@test "INSTALL_START_EPOCH: is a positive integer" {
    [[ "$INSTALL_START_EPOCH" =~ ^[0-9]+$ ]]
    [[ "$INSTALL_START_EPOCH" -gt 0 ]]
}

# ── SYSTEM_TZ ───────────────────────────────────────────────────────────────

@test "SYSTEM_TZ: is set and non-empty" {
    [[ -n "$SYSTEM_TZ" ]]
}

# ── _sed_i ──────────────────────────────────────────────────────────────────

@test "_sed_i: performs in-place substitution" {
    local test_file="$BATS_TEST_TMPDIR/sed-test.txt"
    echo "hello world" > "$test_file"
    _sed_i "s/hello/goodbye/g" "$test_file"
    run cat "$test_file"
    assert_output "goodbye world"
}

@test "_sed_i: handles multiple substitutions in a file" {
    local test_file="$BATS_TEST_TMPDIR/sed-multi.txt"
    printf "foo bar\nfoo baz\n" > "$test_file"
    _sed_i "s/foo/qux/g" "$test_file"
    run cat "$test_file"
    assert_line --index 0 "qux bar"
    assert_line --index 1 "qux baz"
}

@test "_sed_i: no-op when pattern does not match" {
    local test_file="$BATS_TEST_TMPDIR/sed-noop.txt"
    echo "unchanged" > "$test_file"
    _sed_i "s/missing/replaced/g" "$test_file"
    run cat "$test_file"
    assert_output "unchanged"
}
