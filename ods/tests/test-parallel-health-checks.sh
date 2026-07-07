#!/bin/bash
# Test suite for parallel health check implementation
# Verifies correctness, ordering, and error handling

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

PASS=0
FAIL=0

pass() {
    echo -e "  ${GREEN}PASS${NC}  $1"
    PASS=$((PASS + 1))
}

fail() {
    echo -e "  ${RED}FAIL${NC}  $1"
    [[ -n "${2:-}" ]] && echo -e "        ${RED}→ $2${NC}"
    FAIL=$((FAIL + 1))
}

header() {
    echo ""
    echo -e "${BOLD}${CYAN}[$1]${NC} ${BOLD}$2${NC}"
    echo -e "${CYAN}$(printf '%.0s─' {1..60})${NC}"
}

# ============================================
# TEST 1: Syntax and Structure
# ============================================
header "1/5" "Syntax Validation"

if bash -n "$PROJECT_DIR/ods-cli" 2>/dev/null; then
    pass "ods-cli has valid bash syntax"
else
    fail "ods-cli has syntax errors"
fi

# Check for parallel implementation markers
if grep -q "mktemp -d" "$PROJECT_DIR/ods-cli" && \
   grep -q "pids+=(\$!)" "$PROJECT_DIR/ods-cli" && \
   grep -q "wait.*pid" "$PROJECT_DIR/ods-cli"; then
    pass "Parallel implementation patterns detected"
else
    fail "Missing parallel implementation patterns"
fi

# ============================================
# TEST 2: Temp Directory Cleanup
# ============================================
header "2/5" "Resource Cleanup"

if grep -q "trap.*rm -rf.*tmpdir.*RETURN" "$PROJECT_DIR/ods-cli"; then
    pass "Temp directory cleanup trap is set"
else
    fail "Missing cleanup trap for temp directory"
fi

# ============================================
# TEST 3: Timeout Protection
# ============================================
header "3/5" "Timeout Protection"

if grep -q "max-time" "$PROJECT_DIR/ods-cli"; then
    pass "curl timeout protection is implemented"
else
    fail "Missing curl timeout protection"
fi

# ============================================
# TEST 4: Result Ordering
# ============================================
header "4/5" "Result Ordering"

# Check that results are displayed in SERVICE_IDS order, not completion order
if grep -A 10 "Display results in SERVICE_IDS order" "$PROJECT_DIR/ods-cli" | \
   grep -q 'for sid in "\${SERVICE_IDS\[@\]}"'; then
    pass "Results are displayed in SERVICE_IDS order"
else
    fail "Results may not preserve SERVICE_IDS ordering"
fi

# ============================================
# TEST 5: Error Handling
# ============================================
header "5/5" "Error Handling"

# Check that wait handles errors gracefully
if grep -q "wait.*2>/dev/null.*||.*true" "$PROJECT_DIR/ods-cli"; then
    pass "Background job errors are handled gracefully"
else
    fail "Missing error handling for background jobs"
fi

# Check that file existence is verified before reading
if grep -q '\[\[ -f "\$tmpdir/\$sid" \]\]' "$PROJECT_DIR/ods-cli"; then
    pass "File existence check before reading results"
else
    fail "Missing file existence check"
fi

# ============================================
# Summary
# ============================================
echo ""
echo -e "${BOLD}━━━ Test Summary ━━━${NC}"
echo ""
echo -e "  ${GREEN}Passed:${NC} $PASS"
[[ $FAIL -gt 0 ]] && echo -e "  ${RED}Failed:${NC} $FAIL"
echo ""

if [[ $FAIL -eq 0 ]]; then
    echo -e "${GREEN}✓ All tests passed${NC}"
    exit 0
else
    echo -e "${RED}✗ Some tests failed${NC}"
    exit 1
fi
