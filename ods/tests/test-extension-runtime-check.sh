#!/bin/bash
# ============================================================================
# ODS extension-runtime-check.sh Test Suite
# ============================================================================
# Ensures scripts/extension-runtime-check.sh is syntactically valid and runs
# without error against the repo (non-blocking when Docker is absent).
#
# Usage: bash tests/test-extension-runtime-check.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHK="$ROOT_DIR/scripts/extension-runtime-check.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0
SKIPPED=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILED=$((FAILED + 1)); }
skip() { echo -e "  ${YELLOW}⊘ SKIP${NC} $1"; SKIPPED=$((SKIPPED + 1)); }

echo ""
echo "╔═══════════════════════════════════════════════════╗"
echo "║   extension-runtime-check.sh Test Suite          ║"
echo "╚═══════════════════════════════════════════════════╝"
echo ""

if [[ ! -f "$CHK" ]]; then
    fail "scripts/extension-runtime-check.sh not found"
    echo ""; echo "Result: $PASSED passed, $FAILED failed"; [[ $FAILED -eq 0 ]]; exit $?
fi
pass "extension-runtime-check.sh exists"

if ! bash -n "$CHK"; then
    fail "bash -n reported syntax errors"
else
    pass "bash -n clean"
fi

set +e
out="$(cd "$ROOT_DIR" && bash "$CHK" "$ROOT_DIR" 2>&1)"
code=$?
set -e

if [[ $code -ne 0 ]]; then
    fail "default run exited $code (expected 0 — non-blocking)"
    echo "$out" | head -20
else
    pass "default run exits 0"
fi

if [[ "$out" != *"Extension runtime check"* ]]; then
    fail "expected header line in output"
else
    pass "output mentions extension runtime check"
fi

if docker info >/dev/null 2>&1; then
    if echo "$out" | grep -qE '\[OK\]|\[BAD\]|\[INFO\]'; then
        pass "docker available — check lines present"
    else
        skip "docker up but no OK/BAD/INFO lines (minimal stack is OK)"
    fi
else
    if echo "$out" | grep -qi docker; then
        pass "docker unavailable — script explains skip"
    else
        fail "docker unavailable but output did not mention docker"
    fi
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed, $SKIPPED skipped"
[[ $FAILED -eq 0 ]]
