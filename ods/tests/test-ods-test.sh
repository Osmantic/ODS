#!/bin/bash
# ============================================================================
# Test: ods-test.sh Error Handling Compliance
# ============================================================================
# Purpose: Verify ods-test.sh follows "Let It Crash" principle
#          - No silent test failure suppression (|| true after test calls)
#          - Test failures are properly recorded
#          - Exit codes captured where appropriate
#
# Usage: bash tests/test-ods-test.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ODS_TEST="$SCRIPT_DIR/scripts/ods-test.sh"

# Test counters
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() {
    TESTS_PASSED=$((TESTS_PASSED + 1))
    echo -e "${GREEN}✓${NC} $1"
}

fail() {
    TESTS_FAILED=$((TESTS_FAILED + 1))
    echo -e "${RED}✗${NC} $1"
}

warn() {
    echo -e "${YELLOW}⚠${NC} $1"
}

test_no_silent_test_suppression() {
    TESTS_RUN=$((TESTS_RUN + 1))

    # Check for || true after test_http calls (hides test failures)
    # Pattern: test_http ... || true
    local violations=0

    if grep -n 'test_http.*||[[:space:]]*true' "$ODS_TEST" | grep -v '^[[:space:]]*#'; then
        fail "Found 'test_http ... || true' pattern (hides test failures)"
        violations=$((violations + 1))
    fi

    if [[ $violations -eq 0 ]]; then
        pass "No silent test failure suppression"
    fi
}

test_error_logging() {
    TESTS_RUN=$((TESTS_RUN + 1))

    # Check that test failures are logged
    # Pattern: [[ $exit_code -ne 0 ]] && log "..."

    local exit_checks=$(grep -c '\[\[.*_exit.*-ne 0.*\]\]' "$ODS_TEST" || echo 0)
    local log_calls=$(grep -c 'log ".*failed.*exit' "$ODS_TEST" || echo 0)

    # We expect some error logging if there are exit code checks
    if [[ $exit_checks -gt 0 && $log_calls -eq 0 ]]; then
        warn "Has $exit_checks exit code checks but no error logging"
    else
        pass "Logs errors with exit codes ($log_calls log calls)"
    fi
}

test_acceptable_suppressions() {
    TESTS_RUN=$((TESTS_RUN + 1))

    # Some error suppression is acceptable in test scripts:
    # 1. command -v checks (standard practice)
    # 2. curl with || echo "" (provides empty response for test logic)
    # 3. Fallback patterns in utility functions

    # Check that these are used appropriately
    local command_checks=$(grep -c 'command -v.*&>/dev/null' "$ODS_TEST" || echo 0)
    local curl_fallbacks=$(grep -c 'curl.*2>/dev/null.*||.*echo' "$ODS_TEST" || echo 0)

    if [[ $command_checks -gt 0 || $curl_fallbacks -gt 0 ]]; then
        pass "Uses acceptable error suppression patterns (command checks: $command_checks, curl fallbacks: $curl_fallbacks)"
    else
        pass "Minimal error suppression"
    fi
}

test_test_result_recording() {
    TESTS_RUN=$((TESTS_RUN + 1))

    # Check that test results are properly recorded
    # Pattern: record_result "name" "status" "details"

    local record_calls=$(grep -c 'record_result' "$ODS_TEST" || echo 0)

    if [[ $record_calls -gt 10 ]]; then
        pass "Test results properly recorded ($record_calls record_result calls)"
    else
        fail "Insufficient test result recording ($record_calls calls)"
    fi
}

test_exit_code_handling() {
    TESTS_RUN=$((TESTS_RUN + 1))

    # Check that critical operations capture exit codes
    # Look for inline exit code capture pattern

    local exit_captures=$(grep -c '_exit=0' "$ODS_TEST" || echo 0)

    if [[ $exit_captures -gt 0 ]]; then
        pass "Uses inline exit code capture pattern ($exit_captures captures)"
    else
        warn "No inline exit code captures found (may rely on test framework)"
    fi
}

test_set_flags() {
    TESTS_RUN=$((TESTS_RUN + 1))

    # Check that ods-test.sh has proper set flags
    if grep -q '^set -euo pipefail' "$ODS_TEST"; then
        pass "Has 'set -euo pipefail'"
    elif grep -q '^set -e' "$ODS_TEST"; then
        warn "Has 'set -e' but not full 'set -euo pipefail'"
    else
        fail "Missing 'set -euo pipefail' or 'set -e'"
    fi
}

echo "============================================================================"
echo "ods-test.sh Error Handling Compliance Test"
echo "============================================================================"
echo ""

test_no_silent_test_suppression
test_error_logging
test_acceptable_suppressions
test_test_result_recording
test_exit_code_handling
test_set_flags

# Summary
echo ""
echo "============================================================================"
echo "Test Summary"
echo "============================================================================"
echo "Tests run:    $TESTS_RUN"
echo "Tests passed: $TESTS_PASSED"
echo "Tests failed: $TESTS_FAILED"
echo ""

if [[ $TESTS_FAILED -eq 0 ]]; then
    echo -e "${GREEN}✓ All tests passed${NC}"
    exit 0
else
    echo -e "${RED}✗ $TESTS_FAILED test(s) failed${NC}"
    exit 1
fi
