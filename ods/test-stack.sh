#!/bin/bash
#=============================================================================
# test-stack.sh — Complete Local AI Stack Validation
#
# One command to verify your entire ODS installation is working.
# Run after install, after updates, or when troubleshooting.
#
# Usage:
#   ./test-stack.sh                    # Full test suite
#   ./test-stack.sh --quick            # Fast checks only (no inference)
#   ./test-stack.sh --stress           # Include stress tests
#   ./test-stack.sh --voice            # Voice-specific deep test
#
# Exit codes:
#   0 — All tests passed
#   1 — Some tests failed
#   2 — Critical failure (can't continue)
#=============================================================================

# Note: Intentionally NOT using set -e here — test functions may return non-zero
# and we want to continue running all tests, tracking results via PASSED/FAILED counters
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TESTS_DIR="$SCRIPT_DIR/tests"

source "$(dirname "${BASH_SOURCE[0]}")/tests/lib/auth-env.sh"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Options
QUICK=false
STRESS=false
VOICE=false
VERBOSE=false

# Parse args
for arg in "$@"; do
    case $arg in
        --quick|-q) QUICK=true ;;
        --stress|-s) STRESS=true ;;
        --voice|-v) VOICE=true ;;
        --verbose) VERBOSE=true ;;
        --help|-h)
            cat << EOF
ODS Stack Test

Usage: $0 [options]

Options:
  --quick, -q     Fast health checks only (skip inference tests)
  --stress, -s    Include stress tests (takes ~2 minutes)
  --voice, -v     Deep voice pipeline testing
  --verbose       Show detailed output
  --help, -h      Show this help

Examples:
  $0              # Standard test (recommended after install)
  $0 --quick      # Fast sanity check
  $0 --stress     # Full stress test (capacity validation)
EOF
            exit 0
            ;;
    esac
done

# Banner
echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${NC}  ${BOLD}ODS Stack Test${NC}                                     ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  Validating your local AI installation                        ${CYAN}║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Track results
SUITE_PASSED=0
SUITE_FAILED=0
START_TIME=$(date +%s)

run_suite() {
    local name="$1"
    local script="$2"
    local args="${3:-}"
    
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}Running: $name${NC}"
    echo ""
    
    if [[ -x "$script" ]]; then
        if $script $args; then
            SUITE_PASSED=$((SUITE_PASSED + 1))
            echo ""
            echo -e "${GREEN}✓ $name passed${NC}"
        else
            SUITE_FAILED=$((SUITE_FAILED + 1))
            echo ""
            echo -e "${RED}✗ $name failed${NC}"
        fi
    else
        echo -e "${YELLOW}○ $name skipped (script not found)${NC}"
    fi
    echo ""
}

# ═══════════════════════════════════════════════════════════════
# Phase 1: Integration Tests
# ═══════════════════════════════════════════════════════════════

INTEGRATION_ARGS=""
$QUICK && INTEGRATION_ARGS="--quick"
$VERBOSE && INTEGRATION_ARGS="$INTEGRATION_ARGS --verbose"

run_suite "Integration Tests" "$TESTS_DIR/test-integration.sh" "$INTEGRATION_ARGS"

# ═══════════════════════════════════════════════════════════════
# Phase 2: Dashboard Tests
# ═══════════════════════════════════════════════════════════════

if [[ -x "$TESTS_DIR/test-dashboard-integration.sh" ]]; then
    run_suite "Dashboard Integration" "$TESTS_DIR/test-dashboard-integration.sh"
fi

# ═══════════════════════════════════════════════════════════════
# Phase 3: Voice Tests (optional)
# ═══════════════════════════════════════════════════════════════

if $VOICE || $STRESS; then
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}Voice Pipeline Health Check${NC}"
    echo ""
    
    # Quick voice health
    if _ae_require_key; then
        voice_url="http://127.0.0.1:${DASHBOARD_API_PORT}/api/voice/status"
        if curl -sf "${AE_AUTH_HEADER[@]}" "$voice_url" | grep -q '"available":true'; then
            echo -e "${GREEN}✓ Voice services available${NC}"

            # Check individual services
            for svc in stt tts livekit; do
                if curl -sf "${AE_AUTH_HEADER[@]}" "$voice_url" | jq -e ".services.$svc.status == \"healthy\"" >/dev/null 2>&1; then
                    echo -e "  ${GREEN}✓${NC} $svc healthy"
                else
                    echo -e "  ${RED}✗${NC} $svc unhealthy"
                fi
            done
            SUITE_PASSED=$((SUITE_PASSED + 1))
        else
            echo -e "${RED}✗ Voice services unavailable${NC}"
            SUITE_FAILED=$((SUITE_FAILED + 1))
        fi
    else
        SUITE_PASSED=$((SUITE_PASSED + 1))
    fi
    echo ""
fi

# ═══════════════════════════════════════════════════════════════
# Phase 3.5: Phase C (P1 - Broken Features) Tests
# Prioritized in CI pipeline after P0 (Phase A/B) completion
# ═══════════════════════════════════════════════════════════════

if [[ -x "$TESTS_DIR/test-phase-c-p1.sh" ]]; then
    run_suite "Phase C (P1) - Broken Features" "$TESTS_DIR/test-phase-c-p1.sh"
else
    echo -e "${YELLOW}○ Phase C (P1) tests skipped (script not found)${NC}"
    echo ""
fi

# ═══════════════════════════════════════════════════════════════
# Phase 4: Stress Tests (optional)
# ═══════════════════════════════════════════════════════════════

if $STRESS; then
    PYTHON_CMD="python3"
    if command -v python3 &>/dev/null && python3 -c 'import sys; sys.exit(0)' &>/dev/null; then
        PYTHON_CMD="python3"
    elif command -v python &>/dev/null && python -c 'import sys; sys.exit(0)' &>/dev/null; then
        PYTHON_CMD="python"
    fi

    if [[ -f "$TESTS_DIR/voice-stress-test.py" ]] && command -v "$PYTHON_CMD" &>/dev/null; then
        echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${BOLD}Stress Test (10 concurrent, 1 round)${NC}"
        echo ""
        
        cd "$TESTS_DIR"
        if "$PYTHON_CMD" voice-stress-test.py --concurrent 10 --rounds 1 --skip-check; then
            SUITE_PASSED=$((SUITE_PASSED + 1))
        else
            SUITE_FAILED=$((SUITE_FAILED + 1))
        fi
        cd - >/dev/null
        echo ""
    else
        echo -e "${YELLOW}○ Stress test skipped (python3 or script not available)${NC}"
    fi
fi

# ═══════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

echo -e "${CYAN}══════════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}Summary${NC}"
echo ""
echo -e "  Test suites: ${GREEN}$SUITE_PASSED passed${NC} / ${RED}$SUITE_FAILED failed${NC}"
echo -e "  Duration: ${DURATION}s"
echo ""

if [[ $SUITE_FAILED -eq 0 ]]; then
    echo -e "${GREEN}${BOLD}✓ All tests passed!${NC}"
    echo -e "  Your ODS stack is working correctly."
    exit 0
else
    echo -e "${RED}${BOLD}✗ Some tests failed${NC}"
    echo -e "  Check the output above for details."
    echo -e "  Run with --verbose for more information."
    exit 1
fi
