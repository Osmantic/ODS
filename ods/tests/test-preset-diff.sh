#!/bin/bash
# test-preset-diff.sh
# Tests preset diff command

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ODS_CLI="$ODS_DIR/ods-cli"

# Test counters
PASS=0
FAIL=0

# Test helper functions
pass() { echo "✓ $1"; PASS=$((PASS + 1)); }
fail() { echo "✗ $1"; FAIL=$((FAIL + 1)); }

# Setup test environment
setup() {
    export ODS_HOME="$ODS_DIR"
    export INSTALL_DIR="$ODS_DIR"
    export SCRIPT_DIR="$ODS_DIR"
    # PRESETS_DIR is set by ods-cli to ${INSTALL_DIR}/presets
    mkdir -p "$ODS_DIR/presets"
    touch "$ODS_DIR/docker-compose.base.yml" 2>/dev/null || true
}

# Cleanup
cleanup() {
    rm -rf "$ODS_DIR/presets/test-preset-"* 2>/dev/null || true
    # Don't remove docker-compose.base.yml if it existed before tests
}

# Test 1: Diff command requires two arguments
test_diff_requires_args() {
    local output
    output=$("$ODS_CLI" preset diff 2>&1 || true)
    if echo "$output" | grep -q "Usage:"; then
        pass "Diff requires two arguments"
    else
        fail "Diff should require two arguments"
    fi
}

# Test 2: Diff fails if preset doesn't exist
test_diff_nonexistent_preset() {
    local output
    output=$("$ODS_CLI" preset diff nonexistent1 nonexistent2 2>&1 || true)
    if echo "$output" | grep -q "not found"; then
        pass "Diff fails for nonexistent presets"
    else
        fail "Diff should fail for nonexistent presets"
    fi
}

# Test 3: Diff shows no differences for identical presets
test_diff_identical() {
    local presets_dir="$ODS_DIR/presets"
    mkdir -p "$presets_dir/test-preset-a" "$presets_dir/test-preset-b"
    echo "TIER=3" > "$presets_dir/test-preset-a/env"
    echo "TIER=3" > "$presets_dir/test-preset-b/env"
    echo "enabled:llama-server" > "$presets_dir/test-preset-a/extensions.list"
    echo "enabled:llama-server" > "$presets_dir/test-preset-b/extensions.list"

    local output
    output=$("$ODS_CLI" preset diff test-preset-a test-preset-b 2>&1 || true)
    if echo "$output" | grep -q "no differences"; then
        pass "Diff shows no differences for identical presets"
    else
        fail "Diff should show no differences for identical presets"
    fi
}

# Test 4: Diff detects environment variable changes
test_diff_env_changes() {
    local presets_dir="$ODS_DIR/presets"
    mkdir -p "$presets_dir/test-preset-c" "$presets_dir/test-preset-d"
    echo "TIER=3" > "$presets_dir/test-preset-c/env"
    echo "TIER=4" > "$presets_dir/test-preset-d/env"

    local output
    output=$("$ODS_CLI" preset diff test-preset-c test-preset-d 2>&1 || true)
    if echo "$output" | grep -q "TIER"; then
        pass "Diff detects environment variable changes"
    else
        fail "Diff should detect environment variable changes"
    fi
}

# Test 5: Diff detects service state changes
test_diff_service_changes() {
    local presets_dir="$ODS_DIR/presets"
    mkdir -p "$presets_dir/test-preset-e" "$presets_dir/test-preset-f"
    echo "enabled:llama-server" > "$presets_dir/test-preset-e/extensions.list"
    echo "disabled:llama-server" > "$presets_dir/test-preset-f/extensions.list"

    local output
    output=$("$ODS_CLI" preset diff test-preset-e test-preset-f 2>&1 || true)
    if echo "$output" | grep -q "llama-server"; then
        pass "Diff detects service state changes"
    else
        fail "Diff should detect service state changes"
    fi
}

# Test 6: Diff masks sensitive values
test_diff_masks_secrets() {
    local presets_dir="$ODS_DIR/presets"
    mkdir -p "$presets_dir/test-preset-g" "$presets_dir/test-preset-h"
    echo "API_KEY=secret123" > "$presets_dir/test-preset-g/env"
    echo "API_KEY=secret456" > "$presets_dir/test-preset-h/env"
    echo "enabled:llama-server" > "$presets_dir/test-preset-g/extensions.list"
    echo "enabled:llama-server" > "$presets_dir/test-preset-h/extensions.list"

    local output
    output=$("$ODS_CLI" preset diff test-preset-g test-preset-h 2>&1 || true)
    if echo "$output" | grep -q "API_KEY" && echo "$output" | grep -q "\*\*\*"; then
        pass "Diff masks sensitive values"
    else
        fail "Diff should mask sensitive values"
    fi
}

# Test 7: Diff preserves '=' inside values (JWT/base64 secrets, URLs)
test_diff_preserves_equals_in_value() {
    local presets_dir="$ODS_DIR/presets"
    mkdir -p "$presets_dir/test-preset-i" "$presets_dir/test-preset-j"
    echo "JWT=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhPT1iIn0.signature" > "$presets_dir/test-preset-i/env"
    echo "LLM_API_URL=http://localhost:8080/v1/?key=a%3Db&x=1" >> "$presets_dir/test-preset-i/env"
    echo "JWT=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhPT1iIn0.changed" > "$presets_dir/test-preset-j/env"
    echo "LLM_API_URL=http://localhost:8080/v1/?key=a%3Db&x=2" >> "$presets_dir/test-preset-j/env"

    local output
    output=$("$ODS_CLI" preset diff test-preset-i test-preset-j 2>&1 || true)
    if echo "$output" | grep -q "JWT" && echo "$output" | grep -q "LLM_API_URL"; then
        pass "Diff detects differences in values containing '='"
    else
        fail "Diff should detect differences after the first '=' in values"
    fi
}

# Test 8: Diff compares quoted values as their unquoted content
test_diff_unquotes_values() {
    local presets_dir="$ODS_DIR/presets"
    mkdir -p "$presets_dir/test-preset-k" "$presets_dir/test-preset-l"
    echo "TOKEN=\"abc=def=ghi\"" > "$presets_dir/test-preset-k/env"
    echo "TOKEN=\"abc=def=ghi\"" > "$presets_dir/test-preset-l/env"

    local output
    output=$("$ODS_CLI" preset diff test-preset-k test-preset-l 2>&1 || true)
    if echo "$output" | grep -q "no differences"; then
        pass "Diff treats identically quoted values as equal"
    else
        fail "Diff should strip matching quotes before comparing"
    fi
}

# Test 9: Diff tolerates CRLF env files (Windows editors)
test_diff_crlf_env() {
    local presets_dir="$ODS_DIR/presets"
    mkdir -p "$presets_dir/test-preset-m" "$presets_dir/test-preset-n"
    printf 'TIER=3\r\nMODEL=llama-3.1-8b\r\n' > "$presets_dir/test-preset-m/env"
    printf 'TIER=3\r\nMODEL=llama-3.1-8b\r\n' > "$presets_dir/test-preset-n/env"

    local output
    output=$("$ODS_CLI" preset diff test-preset-m test-preset-n 2>&1 || true)
    if echo "$output" | grep -q "no differences"; then
        pass "Diff tolerates CRLF line endings"
    else
        fail "Diff should strip CR so identical CRLF env files compare equal"
    fi
}

# Run tests
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Preset Diff Tests"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

setup
test_diff_requires_args
test_diff_nonexistent_preset
test_diff_identical
test_diff_env_changes
test_diff_service_changes
test_diff_masks_secrets
test_diff_preserves_equals_in_value
test_diff_unquotes_values
test_diff_crlf_env
cleanup

# Summary
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Test Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]] && exit 0 || exit 1
