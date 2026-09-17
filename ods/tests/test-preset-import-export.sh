#!/bin/bash
# Test suite for preset import/export functionality
# Validates export and import commands work correctly

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_CLI="$SCRIPT_DIR/../ods-cli"
TEST_DIR="$(mktemp -d)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Test counters
PASSED=0
FAILED=0

# Cleanup on exit
cleanup() {
    rm -rf "$TEST_DIR"
}
trap cleanup EXIT

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

# Print the body of one cmd_preset case, from its `<label>)` line to the
# terminating `;;`. Scoping the assertions below to the exact case block keeps
# them from silently failing as the block grows: a fixed `grep -A<N>` window
# rots the moment the case body gets longer than N (e.g. adding archive
# staging pushed `cd "$PRESETS_DIR"` and `tar czf` past the old -A15/-A20
# windows), and it can also leak into the next case if the body shrinks.
ods_cli_case_body() {
    local label="$1"  # e.g. 'export|e)'
    awk -v label="$label" '
        index($0, label) { capture = 1 }
        capture { print }
        capture && /;;[[:space:]]*$/ { exit }
    ' "$ODS_CLI"
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

# Test 2: Verify export command exists in help
test_export_in_help() {
    info "Test 2: Checking if 'export' appears in preset help"
    if grep -q "preset export" "$ODS_CLI" 2>/dev/null; then
        pass "'preset export' command documented"
        return 0
    else
        fail "'preset export' command not documented"
        return 0
    fi
}

# Test 3: Verify import command exists in help
test_import_in_help() {
    info "Test 3: Checking if 'import' appears in preset help"
    if grep -q "preset import" "$ODS_CLI" 2>/dev/null; then
        pass "'preset import' command documented"
        return 0
    else
        fail "'preset import' command not documented"
        return 0
    fi
}

# Test 4: Verify export case exists
test_export_case() {
    info "Test 4: Checking if 'export' case exists in cmd_preset"
    if ods_cli_case_body "export|e)" | grep -q "preset export"; then
        pass "'export' case exists in cmd_preset"
        return 0
    else
        fail "'export' case missing from cmd_preset"
        return 0
    fi
}

# Test 5: Verify import case exists
test_import_case() {
    info "Test 5: Checking if 'import' case exists in cmd_preset"
    if ods_cli_case_body "import|i)" | grep -q "preset import"; then
        pass "'import' case exists in cmd_preset"
        return 0
    else
        fail "'import' case missing from cmd_preset"
        return 0
    fi
}

# Test 6: Verify export uses tar
test_export_uses_tar() {
    info "Test 6: Checking if export uses tar for archiving"
    if ods_cli_case_body "export|e)" | grep -q "tar czf"; then
        pass "Export uses tar for archiving"
        return 0
    else
        fail "Export does not use tar"
        return 0
    fi
}

# Test 7: Verify import validates path traversal
test_import_security() {
    info "Test 7: Checking if import validates against path traversal"
    if ods_cli_case_body "import|i)" | grep -q "path traversal"; then
        pass "Import checks for path traversal attacks"
        return 0
    else
        fail "Import missing path traversal validation"
        return 0
    fi
}

# Test 8: Verify export validates preset exists
test_export_validation() {
    info "Test 8: Checking if export validates preset exists"
    if ods_cli_case_body "export|e)" | grep -q "Preset not found"; then
        pass "Export validates preset existence"
        return 0
    else
        fail "Export missing preset validation"
        return 0
    fi
}

# Test 9: Verify import validates archive structure
test_import_validation() {
    info "Test 9: Checking if import validates archive structure"
    if ods_cli_case_body "import|i)" | grep -q "meta.txt"; then
        pass "Import validates archive structure"
        return 0
    else
        fail "Import missing structure validation"
        return 0
    fi
}

# Test 10: Verify export creates relative paths
test_export_relative_paths() {
    info "Test 10: Checking if export avoids absolute paths"
    if ods_cli_case_body "export|e)" | grep -q "cd.*PRESETS_DIR"; then
        pass "Export creates relative paths"
        return 0
    else
        fail "Export may create absolute paths"
        return 0
    fi
}

# Test 11: Verify import handles overwrite confirmation
test_import_overwrite() {
    info "Test 11: Checking if import handles existing presets"
    if ods_cli_case_body "import|i)" | grep -q "already exists"; then
        pass "Import handles overwrite confirmation"
        return 0
    else
        fail "Import missing overwrite handling"
        return 0
    fi
}

# Test 12: Verify usage messages updated
test_usage_updated() {
    info "Test 12: Checking if usage message includes export/import"
    if grep "preset.*export.*import" "$ODS_CLI" 2>/dev/null; then
        pass "Usage message includes export/import"
        return 0
    else
        fail "Usage message not updated"
        return 0
    fi
}

# Run all tests
echo ""
echo -e "${BLUE}━━━ Preset Import/Export Tests ━━━${NC}"
echo ""

test_syntax
test_export_in_help
test_import_in_help
test_export_case
test_import_case
test_export_uses_tar
test_import_security
test_export_validation
test_import_validation
test_export_relative_paths
test_import_overwrite
test_usage_updated

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
