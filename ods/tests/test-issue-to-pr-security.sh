#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKFLOW="$ROOT_DIR/../.github/workflows/issue-to-pr.yml"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   issue-to-pr.yml Security Contract Tests     ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

if [[ ! -f "$WORKFLOW" ]]; then
    fail "issue-to-pr.yml not found at $WORKFLOW"
    exit 1
fi
pass "issue-to-pr.yml exists"

# Extract the implement job block (from '  implement:' to the start of '  guardrails:')
IMPLEMENT_BLOCK=$(awk '/^  implement:/, /^  guardrails:/' "$WORKFLOW")
if [[ -z "$IMPLEMENT_BLOCK" ]]; then
    fail "Could not locate implement job block in issue-to-pr.yml"
    exit 1
fi
pass "Located implement job block"

# 1. Check checkout step has persist-credentials: false
if echo "$IMPLEMENT_BLOCK" | grep -q "uses: actions/checkout"; then
    CHECKOUT_STEP=$(echo "$IMPLEMENT_BLOCK" | grep -A 5 "uses: actions/checkout")
    if echo "$CHECKOUT_STEP" | grep -q "persist-credentials: false"; then
        pass "Checkout in implement job correctly sets persist-credentials: false"
    else
        fail "Checkout in implement job does not set persist-credentials: false"
    fi
else
    fail "Could not find actions/checkout step in implement job"
fi

# 2. Check allowedTools contains only safe, non-executing tools
if echo "$IMPLEMENT_BLOCK" | grep -q "\-\-allowedTools"; then
    TOOLS_VAL=$(echo "$IMPLEMENT_BLOCK" | grep -A 2 "\-\-allowedTools")

    # Assert no Bash execution or other conditional execution capabilities are present
    if echo "$TOOLS_VAL" | grep -q "Bash"; then
        fail "Claude allowedTools contains Bash capabilities: $TOOLS_VAL"
    else
        pass "Claude allowedTools contains no Bash capabilities at all"
    fi

    # Verify presence of expected file-analysis/edit tools
    for tool in Read Edit Write Glob Grep; do
        if echo "$TOOLS_VAL" | grep -q "$tool"; then
            pass "Claude allowedTools includes expected tool: $tool"
        else
            fail "Claude allowedTools is missing expected tool: $tool"
        fi
    done
else
    fail "Could not locate --allowedTools configuration in implement job"
fi

# 3. Check implement job permissions
IMPLEMENT_HEADER=$(awk '/^  implement:/, /^    steps:/' "$WORKFLOW")
if [[ -z "$IMPLEMENT_HEADER" ]]; then
    fail "Could not locate implement job header in issue-to-pr.yml"
else
    if echo "$IMPLEMENT_HEADER" | grep -q "permissions:" && echo "$IMPLEMENT_HEADER" | grep -q "contents: read"; then
        pass "Implement job permissions contain contents: read"
    else
        fail "Implement job permissions missing contents: read"
    fi

    if echo "$IMPLEMENT_HEADER" | grep -q "issues: write"; then
        fail "Implement job permissions contain issues: write (must not have write access)"
    else
        pass "Implement job permissions do not contain issues: write"
    fi

    if echo "$IMPLEMENT_HEADER" | grep -q "pull-requests: write"; then
        fail "Implement job permissions contain pull-requests: write (must not have write access)"
    else
        pass "Implement job permissions do not contain pull-requests: write"
    fi
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
