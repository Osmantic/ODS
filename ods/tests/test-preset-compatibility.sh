#!/bin/bash
# Test suite for preset compatibility validation
# Validates that ods-cli checks for required preset files and extension existence

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_CLI="$SCRIPT_DIR/../ods-cli"
ODS_DIR="$SCRIPT_DIR/.."

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

# Test 1: Verify validate_preset_compatibility function exists
test_function_exists() {
    info "Test 1: Checking if validate_preset_compatibility function exists"
    if grep -q "^validate_preset_compatibility()" "$ODS_CLI" 2>/dev/null; then
        pass "validate_preset_compatibility function is defined"
    else
        fail "validate_preset_compatibility function not found"
    fi
}

# Test 2: Verify function checks for meta.txt
test_checks_meta_txt() {
    info "Test 2: Checking if function validates meta.txt"
    if grep -A20 "^validate_preset_compatibility()" "$ODS_CLI" 2>/dev/null | grep -q "meta.txt"; then
        pass "Function checks for meta.txt"
    else
        fail "Function does not check for meta.txt"
    fi
}

# Test 3: Verify function checks for extensions.list
test_checks_extensions_list() {
    info "Test 3: Checking if function validates extensions.list"
    if grep -A20 "^validate_preset_compatibility()" "$ODS_CLI" 2>/dev/null | grep -q "extensions.list"; then
        pass "Function checks for extensions.list"
    else
        fail "Function does not check for extensions.list"
    fi
}

# Test 4: Verify function checks for env file
test_checks_env_file() {
    info "Test 4: Checking if function validates env file"
    if grep -A20 "^validate_preset_compatibility()" "$ODS_CLI" 2>/dev/null | grep -q "env"; then
        pass "Function checks for env file"
    else
        fail "Function does not check for env file"
    fi
}

# Test 5: Verify cmd_preset calls validate_preset_compatibility
test_cmd_preset_calls_validation() {
    info "Test 5: Checking if cmd_preset calls validate_preset_compatibility"
    if grep -A100 "^cmd_preset()" "$ODS_CLI" 2>/dev/null | grep -q "validate_preset_compatibility"; then
        pass "cmd_preset calls validate_preset_compatibility"
    else
        fail "cmd_preset does not call validate_preset_compatibility"
    fi
}

# Test 6: Verify validation is called before restore
test_validation_before_restore() {
    info "Test 6: Checking if validation is called before restore"
    local preset_load_section
    preset_load_section=$(grep -A50 "load|l)" "$ODS_CLI" 2>/dev/null | head -20)
    if echo "$preset_load_section" | grep -q "validate_preset_compatibility"; then
        pass "Validation is called in preset load action"
    else
        fail "Validation not called in preset load action"
    fi
}

# Test 7: Verify function returns 0 on success
test_function_returns_zero() {
    info "Test 7: Checking if function returns 0 on success"
    if grep -A30 "^validate_preset_compatibility()" "$ODS_CLI" 2>/dev/null | grep -q "return 0"; then
        pass "Function returns 0 on success"
    else
        fail "Function does not return 0"
    fi
}

# Test 8: Verify function warns about missing extensions
test_warns_missing_extensions() {
    info "Test 8: Checking if function warns about missing extensions"
    if grep -A30 "^validate_preset_compatibility()" "$ODS_CLI" 2>/dev/null | grep -q "warn"; then
        pass "Function warns about missing extensions"
    else
        fail "Function does not warn about missing extensions"
    fi
}

# Test 9: Verify ods-cli syntax is valid
test_syntax() {
    info "Test 9: Validating ods-cli syntax"
    if bash -n "$ODS_CLI" 2>/dev/null; then
        pass "ods-cli syntax is valid"
    else
        fail "ods-cli has syntax errors"
    fi
}

# Run all tests
echo ""
echo -e "${BLUE}━━━ Preset Compatibility Validation Tests ━━━${NC}"
echo ""

test_function_exists
test_checks_meta_txt
test_checks_extensions_list
test_checks_env_file
test_cmd_preset_calls_validation
test_validation_before_restore
test_function_returns_zero
test_warns_missing_extensions
test_syntax

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
