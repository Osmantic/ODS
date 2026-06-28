#!/bin/bash
# ============================================================================
# ODS Doctor extension diagnostics test
# ============================================================================
# Tests that ods-doctor.sh collects and reports extension diagnostics
#
# Usage: ./tests/test-doctor-extension-diagnostics.sh
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
echo "╔═══════════════════════════════════════════════╗"
echo "║   Doctor Extension Diagnostics Test           ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# 1. EXT_DIAGNOSTICS variable is initialized
if grep -q 'EXT_DIAGNOSTICS="\[\]"' "$ROOT_DIR/scripts/ods-doctor.sh"; then
    pass "EXT_DIAGNOSTICS variable initialized"
else
    fail "EXT_DIAGNOSTICS variable not initialized"
fi

# 2. Extension diagnostics collection loop exists
if grep -q "for sid in.*SERVICE_IDS" "$ROOT_DIR/scripts/ods-doctor.sh"; then
    pass "Extension diagnostics collection loop present"
else
    fail "Extension diagnostics collection loop missing"
fi

# 3. Container state checking in diagnostics
if grep -q "docker inspect.*State.Status" "$ROOT_DIR/scripts/ods-doctor.sh"; then
    pass "Container state checking present"
else
    fail "Container state checking missing"
fi

# 4. GPU backend compatibility checking
if grep -q "gpu_backend_incompatible" "$ROOT_DIR/scripts/ods-doctor.sh"; then
    pass "GPU backend compatibility check present"
else
    fail "GPU backend compatibility check missing"
fi

# 5. Dependency checking in diagnostics
if grep -q "missing_dependency" "$ROOT_DIR/scripts/ods-doctor.sh"; then
    pass "Dependency checking present"
else
    fail "Dependency checking missing"
fi

# 6. Extensions field added to report
if grep -q '"extensions":.*ext_diagnostics' "$ROOT_DIR/scripts/ods-doctor.sh"; then
    pass "Extensions field added to report"
else
    fail "Extensions field missing from report"
fi

# 7. Extension summary counters
if grep -q "extensions_total" "$ROOT_DIR/scripts/ods-doctor.sh" && \
   grep -q "extensions_healthy" "$ROOT_DIR/scripts/ods-doctor.sh" && \
   grep -q "extensions_issues" "$ROOT_DIR/scripts/ods-doctor.sh"; then
    pass "Extension summary counters present"
else
    fail "Extension summary counters missing"
fi

# 8. Extension-specific autofix hints
if grep -q "Extension.*container not running" "$ROOT_DIR/scripts/ods-doctor.sh"; then
    pass "Extension autofix hints present"
else
    fail "Extension autofix hints missing"
fi

# 9. Extension summary in output
if grep -q "ext_total" "$ROOT_DIR/scripts/ods-doctor.sh" && \
   grep -q "ext_healthy" "$ROOT_DIR/scripts/ods-doctor.sh"; then
    pass "Extension summary output present"
else
    fail "Extension summary output missing"
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
