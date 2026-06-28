#!/bin/bash
# ============================================================================
# ODS Port and Ollama Detection Test Suite
# ============================================================================
# Tests that port conflict and Ollama detection work correctly
#
# Usage: ./tests/test-port-ollama-detection.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0

echo ""
echo "╔═══════════════════════════════════════════╗"
echo "║   Port & Ollama Detection Test Suite     ║"
echo "╚═══════════════════════════════════════════╝"
echo ""

# Source only the functions under test (phase scripts expect installer env).
source <(sed -n '/^check_port_conflict\s*()\s*{/,/^}/p;/^check_ollama_conflict\s*()\s*{/,/^}/p' \
  "$ROOT_DIR/installers/phases/04-requirements.sh") || {
    echo -e "${RED}✗ FAIL${NC} - Cannot load functions from 04-requirements.sh"
    exit 1
}

# Provide warn() to satisfy check_port_conflict() tool-missing path.
warn() { :; }

# Default warn-once guard expected by the function.
_port_check_warned=false

echo "1. Function Existence Tests"
echo "────────────────────────────"

# Test 1: check_port_conflict function exists
printf "  %-50s " "check_port_conflict function exists..."
if declare -f check_port_conflict >/dev/null; then
    echo -e "${GREEN}✓ PASS${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ FAIL${NC}"
    FAILED=$((FAILED + 1))
fi

# Test 2: check_ollama_conflict function exists
printf "  %-50s " "check_ollama_conflict function exists..."
if declare -f check_ollama_conflict >/dev/null; then
    echo -e "${GREEN}✓ PASS${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ FAIL${NC}"
    FAILED=$((FAILED + 1))
fi

echo ""
echo "2. Port Conflict Detection Tests"
echo "─────────────────────────────────"

# Test 3: check_port_conflict returns false for unused port
printf "  %-50s " "Unused port returns false..."
if ! check_port_conflict 59999; then
    echo -e "${GREEN}✓ PASS${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ FAIL${NC}"
    FAILED=$((FAILED + 1))
fi

# Test 4: check_port_conflict sets PORT_CONFLICT=false for unused port
printf "  %-50s " "PORT_CONFLICT=false for unused port..."
check_port_conflict 59998 || true
if [[ "$PORT_CONFLICT" == "false" ]]; then
    echo -e "${GREEN}✓ PASS${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ FAIL${NC}"
    FAILED=$((FAILED + 1))
fi

# Test 5: Start a test server and detect it
printf "  %-50s " "Detects port conflict with test server..."
# Start a simple HTTP server on port 59997
python3 -m http.server 59997 >/dev/null 2>&1 &
TEST_SERVER_PID=$!
sleep 1

if check_port_conflict 59997; then
    echo -e "${GREEN}✓ PASS${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ FAIL${NC}"
    FAILED=$((FAILED + 1))
fi

# Test 6: PORT_CONFLICT=true for used port
printf "  %-50s " "PORT_CONFLICT=true for used port..."
if [[ "$PORT_CONFLICT" == "true" ]]; then
    echo -e "${GREEN}✓ PASS${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ FAIL${NC}"
    FAILED=$((FAILED + 1))
fi

# Test 7: PORT_CONFLICT_PID is set
printf "  %-50s " "PORT_CONFLICT_PID is set..."
if [[ -n "$PORT_CONFLICT_PID" ]]; then
    echo -e "${GREEN}✓ PASS${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ FAIL${NC}"
    FAILED=$((FAILED + 1))
fi

# Test 8: PORT_CONFLICT_PROC is set
printf "  %-50s " "PORT_CONFLICT_PROC is set..."
if [[ -n "$PORT_CONFLICT_PROC" ]] && [[ "$PORT_CONFLICT_PROC" != "unknown" ]]; then
    echo -e "${GREEN}✓ PASS${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${YELLOW}⚠ SKIP${NC} (may require lsof)"
fi

# Cleanup test server
kill $TEST_SERVER_PID 2>/dev/null || true
wait $TEST_SERVER_PID 2>/dev/null || true

echo ""
echo "3. Ollama Detection Tests"
echo "─────────────────────────"

# Test 9: check_ollama_conflict runs without error
printf "  %-50s " "check_ollama_conflict runs without error..."
if check_ollama_conflict; then
    echo -e "${GREEN}✓ PASS${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ FAIL${NC}"
    FAILED=$((FAILED + 1))
fi

# Test 10: OLLAMA_RUNNING is set (true or false)
printf "  %-50s " "OLLAMA_RUNNING variable is set..."
if [[ "$OLLAMA_RUNNING" == "true" ]] || [[ "$OLLAMA_RUNNING" == "false" ]]; then
    echo -e "${GREEN}✓ PASS${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ FAIL${NC}"
    FAILED=$((FAILED + 1))
fi

# Test 11: If Ollama is running, OLLAMA_PID is set
printf "  %-50s " "OLLAMA_PID set when Ollama running..."
if [[ "$OLLAMA_RUNNING" == "true" ]]; then
    if [[ -n "$OLLAMA_PID" ]]; then
        echo -e "${GREEN}✓ PASS${NC}"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}✗ FAIL${NC}"
        FAILED=$((FAILED + 1))
    fi
else
    echo -e "${YELLOW}⚠ SKIP${NC} (Ollama not running)"
fi

echo ""
echo "4. Integration Tests"
echo "────────────────────"

# Test 12: Phase script has Ollama detection code
printf "  %-50s " "Phase script calls check_ollama_conflict..."
if grep -q "check_ollama_conflict" "$ROOT_DIR/installers/phases/04-requirements.sh"; then
    echo -e "${GREEN}✓ PASS${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ FAIL${NC}"
    FAILED=$((FAILED + 1))
fi

# Test 13: Phase script uses check_port_conflict
printf "  %-50s " "Phase script calls check_port_conflict..."
if grep -q "check_port_conflict" "$ROOT_DIR/installers/phases/04-requirements.sh"; then
    echo -e "${GREEN}✓ PASS${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ FAIL${NC}"
    FAILED=$((FAILED + 1))
fi

# Test 14: Phase script shows process details in warnings
printf "  %-50s " "Phase script shows PORT_CONFLICT_PROC..."
if grep -q "PORT_CONFLICT_PROC" "$ROOT_DIR/installers/phases/04-requirements.sh"; then
    echo -e "${GREEN}✓ PASS${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ FAIL${NC}"
    FAILED=$((FAILED + 1))
fi

echo ""
echo "═══════════════════════════════════════════"
if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ All tests passed${NC} ($PASSED/$((PASSED + FAILED)))"
    echo ""
    exit 0
else
    echo -e "${RED}✗ Some tests failed${NC} ($PASSED passed, $FAILED failed)"
    echo ""
    exit 1
fi
