#!/bin/bash
# ============================================================================
# ODS CLI enable GPU backend validation test
# ============================================================================
# Tests that ods-cli enable command validates GPU backend compatibility
# before enabling extensions.
#
# Usage: ./tests/test-enable-gpu-validation.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   GPU Backend Validation Test                 ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# 1. SERVICE_GPU_BACKENDS array is declared in service-registry.sh
if grep -q "declare -A SERVICE_GPU_BACKENDS" "$ROOT_DIR/lib/service-registry.sh"; then
    pass "SERVICE_GPU_BACKENDS array declared"
else
    fail "SERVICE_GPU_BACKENDS array not declared"
fi

# 2. GPU backends are parsed from manifests
if grep -q "gpu_backends.*get.*gpu_backends" "$ROOT_DIR/lib/service-registry.sh"; then
    pass "GPU backends parsing logic present"
else
    fail "GPU backends parsing logic missing"
fi

# 3. GPU backends are emitted to bash
if grep -q 'SERVICE_GPU_BACKENDS.*_esc' "$ROOT_DIR/lib/service-registry.sh"; then
    pass "GPU backends emitted to bash registry"
else
    fail "GPU backends emission missing"
fi

# 4. cmd_enable checks GPU backend compatibility
if grep -q "SERVICE_GPU_BACKENDS.*service_id" "$ROOT_DIR/ods-cli"; then
    pass "cmd_enable reads GPU backends from registry"
else
    fail "cmd_enable missing GPU backend check"
fi

# 5. cmd_enable warns about incompatible backends
if grep -q "may not work with GPU backend" "$ROOT_DIR/ods-cli"; then
    pass "GPU backend warning message present"
else
    fail "GPU backend warning message missing"
fi

# 6. cmd_enable shows supported backends list
if grep -q "Supported backends" "$ROOT_DIR/ods-cli"; then
    pass "Supported backends message present"
else
    fail "Supported backends message missing"
fi

# 7. User can cancel if backend incompatible
if grep -A5 "may not work with GPU backend" "$ROOT_DIR/ods-cli" | grep -q "Continue anyway"; then
    pass "User confirmation prompt present"
else
    fail "User confirmation prompt missing"
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
