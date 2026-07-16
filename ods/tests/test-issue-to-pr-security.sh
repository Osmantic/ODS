#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKFLOW_PR="$ROOT_DIR/../.github/workflows/issue-to-pr.yml"
WORKFLOW_TRIAGE="$ROOT_DIR/../.github/workflows/ai-issue-triage.yml"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║      GitHub Workflows Security Contract Tests  ║"
echo "╚═══════════════════════════════════════════════╝"

# ============================================================
# PART 1: issue-to-pr.yml
# ============================================================
echo ""
echo "--- Testing issue-to-pr.yml ---"

if [[ ! -f "$WORKFLOW_PR" ]]; then
    fail "issue-to-pr.yml not found at $WORKFLOW_PR"
    exit 1
fi
pass "issue-to-pr.yml exists"

# Extract the implement job block
IMPLEMENT_BLOCK=$(awk '/^  implement:/, /^  guardrails:/' "$WORKFLOW_PR")
if [[ -z "$IMPLEMENT_BLOCK" ]]; then
    fail "Could not locate implement job block in issue-to-pr.yml"
    exit 1
fi
pass "Located implement job block"

# Check if implement job uses claude-code-action
if echo "$IMPLEMENT_BLOCK" | grep -q "uses: anthropics/claude-code-action"; then
    # Check checkout step has persist-credentials: false
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

    # Check allowedTools contains only safe, non-executing tools
    if echo "$IMPLEMENT_BLOCK" | grep -q "\-\-allowedTools"; then
        TOOLS_VAL=$(echo "$IMPLEMENT_BLOCK" | grep -A 2 "\-\-allowedTools")

        if echo "$TOOLS_VAL" | grep -q "Bash"; then
            fail "Claude allowedTools contains Bash capabilities: $TOOLS_VAL"
        else
            pass "Claude allowedTools contains no Bash capabilities at all"
        fi

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
else
    echo "  - Skipped issue-to-pr.yml checks (not yet using claude-code-action)"
fi


# ============================================================
# PART 2: ai-issue-triage.yml
# ============================================================
echo ""
echo "--- Testing ai-issue-triage.yml ---"

if [[ ! -f "$WORKFLOW_TRIAGE" ]]; then
    fail "ai-issue-triage.yml not found at $WORKFLOW_TRIAGE"
    exit 1
fi
pass "ai-issue-triage.yml exists"

# Extract triage job block
TRIAGE_BLOCK=$(awk '/^  triage:/, /^  apply-labels:/' "$WORKFLOW_TRIAGE")
if [[ -z "$TRIAGE_BLOCK" ]]; then
    fail "Could not locate triage job block in ai-issue-triage.yml"
    exit 1
fi
pass "Located triage job block"

# Check permissions of the triage job
TRIAGE_HEADER=$(awk '/^  triage:/, /^    steps:/' "$WORKFLOW_TRIAGE")
if echo "$TRIAGE_HEADER" | grep -q "contents: read"; then
    pass "Triage job permissions contain contents: read"
else
    fail "Triage job permissions missing contents: read"
fi

if echo "$TRIAGE_HEADER" | grep -A 5 "permissions:" | grep -q "issues:"; then
    fail "Triage job permissions contains issues permission (must not have write access)"
else
    pass "Triage job does not have issues permission"
fi

# Check model-facing permission boundary invariant
AI_STEP=$(echo "$TRIAGE_BLOCK" | grep -A 8 "\- name: AI Triage")
if [[ -z "$AI_STEP" ]]; then
    fail "Could not locate AI Triage step in triage job"
else
    HAS_TOKEN=0
    if echo "$AI_STEP" | grep -q "github_token"; then
        HAS_TOKEN=1
    fi

    HAS_WRITE=0
    if echo "$TRIAGE_HEADER" | grep -A 5 "permissions:" | grep -q "issues: write"; then
        HAS_WRITE=1
    fi

    if [[ "$HAS_TOKEN" -eq 1 ]] && [[ "$HAS_WRITE" -eq 1 ]]; then
        fail "Model-facing permission boundary violation: AI Triage receives github_token and triage job has issues: write"
    else
        pass "Model-facing permission boundary locked (either no token or triage job lacks issues: write)"
    fi
fi

# Extract apply-labels job block
APPLY_BLOCK=$(awk '/^  apply-labels:/, 0' "$WORKFLOW_TRIAGE")
if [[ -z "$APPLY_BLOCK" ]]; then
    fail "Could not locate apply-labels job block in ai-issue-triage.yml"
    exit 1
fi
pass "Located apply-labels job block"

# Check apply-labels job permissions
APPLY_HEADER=$(awk '/^  apply-labels:/, /^    steps:/' "$WORKFLOW_TRIAGE")
if echo "$APPLY_HEADER" | grep -A 5 "permissions:" | grep -q "issues: write"; then
    pass "apply-labels job correctly has issues: write"
else
    fail "apply-labels job missing issues: write"
fi

# Verify issues: write is only configured on a single job in the workflow
WRITE_COUNT=$(grep -c "issues: write" "$WORKFLOW_TRIAGE" || true)
if [[ "$WRITE_COUNT" -eq 1 ]]; then
    pass "issues: write is configured exactly once in ai-issue-triage.yml (only on apply-labels)"
else
    fail "issues: write appears $WRITE_COUNT times in ai-issue-triage.yml (expected exactly 1)"
fi

# Check allowedTools contains only safe tools (no Bash)
if echo "$TRIAGE_BLOCK" | grep -q "\-\-allowedTools"; then
    TOOLS_VAL=$(echo "$TRIAGE_BLOCK" | grep -A 2 "\-\-allowedTools")
    if echo "$TOOLS_VAL" | grep -q "Bash"; then
        fail "AI Triage allowedTools contains Bash capabilities: $TOOLS_VAL"
    else
        pass "AI Triage allowedTools contains no Bash capabilities"
    fi
else
    fail "Could not find AI Triage step using claude-code-action in triage job"
fi

# Check deterministic github-script step performs mutations
if echo "$APPLY_BLOCK" | grep -q "uses: actions/github-script"; then
    if echo "$APPLY_BLOCK" | grep -q "github.rest.issues.addLabels"; then
        pass "Deterministic github-script step performs label mutations (uses github.rest.issues.addLabels)"
    else
        fail "github-script step does not call github.rest.issues.addLabels"
    fi
else
    fail "Could not find github-script step in apply-labels job"
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
