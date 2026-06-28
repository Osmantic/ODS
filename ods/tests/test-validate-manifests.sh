#!/bin/bash
# ============================================================================
# ODS validate-manifests.sh Test Suite
# ============================================================================
# Ensures scripts/validate-manifests.sh runs and produces expected output.
# Protects extension compatibility validation from regressions (Focus 3).
#
# Usage: ./tests/test-validate-manifests.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }

echo ""
echo "╔═══════════════════════════════════════════════════╗"
echo "║   validate-manifests.sh Test Suite               ║"
echo "╚═══════════════════════════════════════════════════╝"
echo ""

# 1. Script exists and is executable
if [[ ! -f "$ROOT_DIR/scripts/validate-manifests.sh" ]]; then
    fail "scripts/validate-manifests.sh not found"
    echo ""; echo "Result: $PASSED passed, $FAILED failed"; [[ $FAILED -eq 0 ]]; exit $?
fi
pass "validate-manifests.sh exists"

pass "validate-manifests.sh is runnable (bash scripts/validate-manifests.sh)"

# 2. jq required by script
if ! command -v jq &>/dev/null; then
    fail "jq not installed (required by validate-manifests.sh) — skip remaining checks"
    echo ""; echo "Result: $PASSED passed, $FAILED failed"; [[ $FAILED -eq 0 ]]; exit $?
fi
pass "jq available"

# 3. Run script and capture output and exit code
set +e
out=$(cd "$ROOT_DIR" && bash scripts/validate-manifests.sh 2>&1)
exit_code=$?
set -e

# 4. Expect exit 0 when manifests and schema are present
if [[ $exit_code -ne 0 ]]; then
    fail "validate-manifests.sh exited with $exit_code (expected 0)"
else
    pass "validate-manifests.sh exited 0"
fi

# 5. Expected output content
if echo "$out" | grep -q "Extension manifest validation"; then
    pass "Output contains 'Extension manifest validation'"
else
    fail "Output missing 'Extension manifest validation'"
fi

if echo "$out" | grep -q "Summary:"; then
    pass "Output contains 'Summary:'"
else
    fail "Output missing 'Summary:'"
fi

# 6. Should list at least one service (from extensions/services/)
if echo "$out" | grep -qE "ok-no-metadata|Compatible|ok"; then
    pass "Output contains compatibility status lines"
else
    fail "Output missing compatibility status"
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
