#!/bin/bash
# Test suite for backup/restore CLI integration
# Validates that ods-cli properly delegates to ods-backup.sh and ods-restore.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_CLI="$SCRIPT_DIR/../ods-cli"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Test counters
PASSED=0
FAILED=0

# Test helpers
pass() {
    echo -e "${GREEN}✓${NC} $1"
    ((PASSED++))
}

fail() {
    echo -e "${RED}✗${NC} $1"
    ((FAILED++))
}

info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

# Test 1: Verify ods-cli syntax
test_syntax() {
    info "Test 1: Validating ods-cli syntax"
    if bash -n "$ODS_CLI" 2>/dev/null; then
        pass "ods-cli syntax is valid"
    else
        fail "ods-cli has syntax errors"
    fi
}

# Test 2: Verify backup command exists in help
test_backup_in_help() {
    info "Test 2: Checking if 'backup' appears in help text"
    if grep -q "backup.*Create a backup" "$ODS_CLI" 2>/dev/null; then
        pass "'backup' command listed in help"
        return 0
    else
        fail "'backup' command not found in help"
        return 0
    fi
}

# Test 3: Verify restore command exists in help
test_restore_in_help() {
    info "Test 3: Checking if 'restore' appears in help text"
    if grep -q "restore.*Restore from a backup" "$ODS_CLI" 2>/dev/null; then
        pass "'restore' command listed in help"
        return 0
    else
        fail "'restore' command not found in help"
        return 0
    fi
}

# Test 4: Verify backup examples in help
test_backup_examples() {
    info "Test 4: Checking for backup examples in help text"
    if grep -q "ods backup" "$ODS_CLI" 2>/dev/null; then
        pass "Backup examples present in help"
        return 0
    else
        fail "Backup examples missing from help"
        return 0
    fi
}

# Test 5: Verify cmd_backup function exists
test_cmd_backup_exists() {
    info "Test 5: Checking if cmd_backup function is defined"
    if grep -q "^cmd_backup()" "$ODS_CLI" 2>/dev/null; then
        pass "cmd_backup function is defined"
        return 0
    else
        fail "cmd_backup function not found"
        return 0
    fi
}

# Test 6: Verify cmd_restore function exists
test_cmd_restore_exists() {
    info "Test 6: Checking if cmd_restore function is defined"
    if grep -q "^cmd_restore()" "$ODS_CLI" 2>/dev/null; then
        pass "cmd_restore function is defined"
        return 0
    else
        fail "cmd_restore function not found"
        return 0
    fi
}

# Test 7: Verify backup case in main switch
test_backup_case() {
    info "Test 7: Checking if 'backup' is in main case statement"
    if grep -q "backup)" "$ODS_CLI" 2>/dev/null; then
        pass "'backup' case exists in main switch"
        return 0
    else
        fail "'backup' case missing from main switch"
        return 0
    fi
}

# Test 8: Verify restore case in main switch
test_restore_case() {
    info "Test 8: Checking if 'restore' is in main case statement"
    if grep -q "restore)" "$ODS_CLI" 2>/dev/null; then
        pass "'restore' case exists in main switch"
        return 0
    else
        fail "'restore' case missing from main switch"
        return 0
    fi
}

# Test 9: Verify backup delegates to ods-backup.sh
test_backup_delegation() {
    info "Test 9: Checking if cmd_backup delegates to ods-backup.sh"
    if grep -A5 "^cmd_backup()" "$ODS_CLI" 2>/dev/null | grep -q "ods-backup.sh"; then
        pass "cmd_backup delegates to ods-backup.sh"
        return 0
    else
        fail "cmd_backup does not delegate to ods-backup.sh"
        return 0
    fi
}

# Test 10: Verify restore delegates to ods-restore.sh
test_restore_delegation() {
    info "Test 10: Checking if cmd_restore delegates to ods-restore.sh"
    if grep -A5 "^cmd_restore()" "$ODS_CLI" 2>/dev/null | grep -q "ods-restore.sh"; then
        pass "cmd_restore delegates to ods-restore.sh"
        return 0
    else
        fail "cmd_restore does not delegate to ods-restore.sh"
        return 0
    fi
}

# Test 11: Verify argument passing
test_argument_passing() {
    info "Test 11: Checking if arguments are passed through"
    # cmd_backup may special-case subcommands (e.g. verify) but should still pass args through
    # in its default path.
    if grep -A25 "^cmd_backup()" "$ODS_CLI" 2>/dev/null | grep -q '\$@'; then
        pass "cmd_backup passes arguments correctly"
        return 0
    else
        fail "cmd_backup does not pass arguments"
        return 0
    fi
}

# Run all tests
echo ""
echo -e "${BLUE}━━━ Backup/Restore CLI Integration Tests ━━━${NC}"
echo ""

test_syntax
test_backup_in_help
test_restore_in_help
test_backup_examples
test_cmd_backup_exists
test_cmd_restore_exists
test_backup_case
test_restore_case
test_backup_delegation
test_restore_delegation
test_argument_passing

# Summary
echo ""
echo -e "${BLUE}━━━ Test Summary ━━━${NC}"
echo ""
echo -e "  ${GREEN}Passed:${NC} $PASSED"
if [[ $FAILED -gt 0 ]]; then
    echo -e "  ${RED}Failed:${NC} $FAILED"
fi
echo ""

if [[ $FAILED -eq 0 ]]; then
    echo -e "${GREEN}✓ All tests passed${NC}"
    exit 0
else
    echo -e "${RED}✗ Some tests failed${NC}"
    exit 1
fi
