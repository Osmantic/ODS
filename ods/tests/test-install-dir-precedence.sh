#!/bin/bash
# Regression test: install-directory precedence chain
#
# Tests that INSTALL_DIR is resolved in the correct order:
#   --install-dir flag > $INSTALL_DIR env > $ODS_INSTALL_DIR env > $HOME/ods
#
# Run: bash ods/tests/test-install-dir-precedence.sh

set -euo pipefail

ODS_DIR="${ODS_DIR:-$(dirname "$(dirname "$(realpath "$0")")")}"
cd "$ODS_DIR"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

pass() { echo -e "${GREEN}✓ PASS${NC}: $1"; }
fail() { echo -e "${RED}✗ FAIL${NC}: $1"; exit 1; }
info() { echo -e "${YELLOW}→${NC} $1"; }

echo "═══════════════════════════════════════════════════════════════"
echo "  Install-Directory Precedence Test Suite"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# Track total tests
TESTS_RUN=0
TESTS_PASSED=0

# Helper: test the precedence logic by invoking the real get-ods.sh with --print-install-dir
# Usage: test_precedence "description" expected [env vars...]
test_precedence() {
    local desc="$1"
    local expected="$2"
    shift 2

    local result
    result=$(ODS_BOOTSTRAP_ROOT="$HOME" "$@" bash "$ODS_DIR/ods/get-ods.sh" --print-install-dir 2>/dev/null || true)

    TESTS_RUN=$((TESTS_RUN + 1))
    if [[ "$result" == "$expected" ]]; then
        TESTS_PASSED=$((TESTS_PASSED + 1))
        pass "$desc (got: $result)"
    else
        fail "$desc — expected: $expected, got: $result"
    fi
}

# ===== Test 1: Default when nothing is set =====
info "Test group: Default"
test_precedence \
    "No env vars set — uses default \$HOME/ods" \
    "$HOME/ods"

# ===== Test 2: ODS_INSTALL_DIR env var =====
info "Test group: ODS_INSTALL_DIR"
test_precedence \
    "ODS_INSTALL_DIR is respected" \
    "/custom/ods" \
    ODS_INSTALL_DIR="/custom/ods"

# ===== Test 3: INSTALL_DIR env var overrides ODS_INSTALL_DIR =====
info "Test group: INSTALL_DIR overrides ODS_INSTALL_DIR"
test_precedence \
    "INSTALL_DIR overrides ODS_INSTALL_DIR" \
    "/env/ods" \
    INSTALL_DIR="/env/ods" ODS_INSTALL_DIR="/custom/ods"

# ===== Test 4: --install-dir flag overrides everything =====
info "Test group: --install-dir flag"
test_precedence \
    "--install-dir overrides INSTALL_DIR" \
    "/flag/ods" \
    INSTALL_DIR="/env/ods" ODS_INSTALL_DIR="/custom/ods" \
    --install-dir=/flag/ods

test_precedence \
    "--install-dir overrides ODS_INSTALL_DIR (no INSTALL_DIR)" \
    "/flag/ods" \
    ODS_INSTALL_DIR="/custom/ods" \
    --install-dir=/flag/ods

# ===== Test 5: No env, no flag =====
info "Test group: Fallback chain"
test_precedence \
    "ODS_INSTALL_DIR used when INSTALL_DIR unset and no flag" \
    "/legacy/ods" \
    ODS_INSTALL_DIR="/legacy/ods"

test_precedence \
    "INSTALL_DIR used when no flag" \
    "/direct/ods" \
    INSTALL_DIR="/direct/ods"

# ===== Test 6: Edge cases =====
info "Test group: Edge cases"
# Note: empty-string fallback test is handled by not passing the env var
test_precedence \
    "ODS_INSTALL_DIR used when no INSTALL_DIR and no flag" \
    "/fallback/ods" \
    ODS_INSTALL_DIR="/fallback/ods"

test_precedence \
    "All empty — uses default" \
    "$HOME/ods"

# ===== Summary =====
echo ""
echo "═══════════════════════════════════════════════════════════════"
if [[ "$TESTS_RUN" -eq "$TESTS_PASSED" ]]; then
    echo -e "  ${GREEN}All $TESTS_RUN tests passed${NC}"
else
    echo -e "  ${RED}$TESTS_PASSED/$TESTS_RUN tests passed${NC}"
    exit 1
fi
echo "═══════════════════════════════════════════════════════════════"
