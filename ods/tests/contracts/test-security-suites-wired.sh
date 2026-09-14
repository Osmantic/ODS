#!/bin/bash
# ============================================================================
# Contract: every security suite SECURITY_AUDIT.md claims must actually run
# ============================================================================
# SECURITY_AUDIT.md presents a set of test suites as the project's standing
# security checks. A suite that no make target and no workflow invokes is a
# documented guarantee that is never enforced: the regression it exists to
# catch lands silently.
#
# No workflow runs `make test`, so Makefile wiring alone is not sufficient —
# a suite counts as executed only if the Makefile or a workflow names it.
#
# Usage: ./tests/contracts/test-security-suites-wired.sh
# Exit 0 if every documented suite is wired, 1 otherwise.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
REPO_ROOT="$(cd "$ODS_DIR/.." && pwd)"

AUDIT_DOC="$REPO_ROOT/SECURITY_AUDIT.md"
MAKEFILE="$ODS_DIR/Makefile"
WORKFLOW_DIR="$REPO_ROOT/.github/workflows"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0
pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }

echo ""
echo "== Documented security suites are actually executed =="

[[ -f "$AUDIT_DOC" ]] || { fail "SECURITY_AUDIT.md not found at $AUDIT_DOC"; exit 1; }
[[ -f "$MAKEFILE" ]] || { fail "Makefile not found at $MAKEFILE"; exit 1; }
[[ -d "$WORKFLOW_DIR" ]] || { fail "workflow dir not found at $WORKFLOW_DIR"; exit 1; }

# Suites the audit document points at, e.g. ods/tests/test-secret-security.sh
suites=$(grep -oE 'ods/tests/[A-Za-z0-9_./-]+\.(sh|py)' "$AUDIT_DOC" | sort -u)

if [[ -z "$suites" ]]; then
    fail "SECURITY_AUDIT.md references no test suites; the contract would be vacuous"
    exit 1
fi

while IFS= read -r suite; do
    [[ -n "$suite" ]] || continue
    rel="${suite#ods/}"

    if [[ ! -f "$ODS_DIR/$rel" ]]; then
        fail "$suite is documented but missing from the tree"
        continue
    fi

    # -F: the path contains dots; match it literally, not as a regex.
    if grep -qF -- "$rel" "$MAKEFILE" 2>/dev/null; then
        pass "$suite runs via the Makefile"
    elif grep -rqF -- "$rel" "$WORKFLOW_DIR" 2>/dev/null; then
        pass "$suite runs via a workflow"
    else
        fail "$suite is documented in SECURITY_AUDIT.md but no make target or workflow runs it"
    fi
done <<< "$suites"

echo ""
echo "== results: $PASSED passed, $FAILED failed =="
[[ "$FAILED" -eq 0 ]]
