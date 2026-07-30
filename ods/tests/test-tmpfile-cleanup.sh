#!/bin/bash
# ============================================================================
# ODS Temporary File Cleanup Test Suite
# ============================================================================
# Tests that temporary files are properly cleaned up with trap handlers
#
# Usage: ./tests/test-tmpfile-cleanup.sh
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
echo "║   Temporary File Cleanup Test Suite      ║"
echo "╚═══════════════════════════════════════════╝"
echo ""

echo "1. Trap Override Safety Tests"
echo "───────────────────────────────"

# These phase scripts are sourced by install-core.sh, which already installs a
# deliberate SIGINT handler (double-tap Ctrl+C). We must not override that trap
# from a sourced phase.

# Test 1: Phase 05 does NOT set INT/TERM trap for tmpfile cleanup
printf "  %-50s " "Phase 05 does not override INT/TERM traps..."
if ! grep -A 3 'mktemp /tmp/install-docker' "$ROOT_DIR/installers/phases/05-docker.sh" | grep -q "trap .*INT"; then
    echo -e "${GREEN}✓ PASS${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ FAIL${NC}"
    FAILED=$((FAILED + 1))
fi

# Test 2: Phase 07 does NOT set INT/TERM trap for NodeSource tmpfile
printf "  %-50s " "Phase 07 NodeSource does not override INT/TERM..."
if ! grep -A 3 'mktemp /tmp/nodesource-setup' "$ROOT_DIR/installers/phases/07-devtools.sh" | grep -q "trap .*INT"; then
    echo -e "${GREEN}✓ PASS${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ FAIL${NC}"
    FAILED=$((FAILED + 1))
fi

# Test 3: Phase 07 does NOT set INT/TERM trap for OpenCode tmpfile
printf "  %-50s " "Phase 07 OpenCode does not override INT/TERM..."
if ! grep -A 3 'mktemp /tmp/opencode-install' "$ROOT_DIR/installers/phases/07-devtools.sh" | grep -q "trap .*INT"; then
    echo -e "${GREEN}✓ PASS${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ FAIL${NC}"
    FAILED=$((FAILED + 1))
fi

echo ""
echo "2. Explicit Cleanup Tests"
echo "─────────────────────────"

# Test 7: Phase 05 has explicit cleanup after success
printf "  %-50s " "Phase 05 has explicit cleanup after success..."
if grep -A 10 'mktemp /tmp/install-docker' "$ROOT_DIR/installers/phases/05-docker.sh" | grep -q "rm -f.*tmpfile"; then
    echo -e "${GREEN}✓ PASS${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ FAIL${NC}"
    FAILED=$((FAILED + 1))
fi

# Test 8: every exit from the download helper removes the temp file.
#
# The helper owns the temp file end to end: it mktemps, curls get.docker.com
# into it, runs it, and must delete it before returning on either path. The
# callers only see a return code, so an assertion anchored on the caller's
# `error "Docker installation failed"` line proves nothing about the file —
# it just happened to sit near the cleanup before the download moved into a
# function.
printf "  %-50s " "Phase 05 helper cleans up on every exit..."
helper_body=$(awk '
    /^_docker_install_from_script\(\) \{/ { inside = 1; next }
    inside && /^\}/ { exit }
    inside { print }
' "$ROOT_DIR/installers/phases/05-docker.sh")

# Every `return` must have a removal on it or within the two lines above it,
# and the helper must end by removing the file on the success path.
uncleaned_returns=$(awk '
    { line[NR] = $0 }
    /return/ {
        cleaned = 0
        for (i = NR; i >= NR - 2 && i >= 1; i--) {
            if (line[i] ~ /rm -f[[:space:]]+"\$tmpfile"/) cleaned = 1
        }
        if (!cleaned) print NR ": " $0
    }
' <<< "$helper_body")
last_statement=$(grep -v '^[[:space:]]*$' <<< "$helper_body" | tail -1)

if [[ -n "$helper_body" ]]     && grep -q 'mktemp /tmp/install-docker' <<< "$helper_body"     && [[ -z "$uncleaned_returns" ]]     && grep -q 'rm -f[[:space:]]\+"\$tmpfile"' <<< "$last_statement"; then
    echo -e "${GREEN}✓ PASS${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ FAIL${NC}"
    [[ -n "$uncleaned_returns" ]] && echo "    return without cleanup: $uncleaned_returns"
    FAILED=$((FAILED + 1))
fi

# Test 9: Phase 07 NodeSource has explicit cleanup
printf "  %-50s " "Phase 07 NodeSource has explicit cleanup..."
if grep -A 10 'mktemp /tmp/nodesource-setup' "$ROOT_DIR/installers/phases/07-devtools.sh" | grep -q "rm -f.*tmpfile"; then
    echo -e "${GREEN}✓ PASS${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ FAIL${NC}"
    FAILED=$((FAILED + 1))
fi

# Test 10: Phase 07 OpenCode has explicit cleanup
printf "  %-50s " "Phase 07 OpenCode has explicit cleanup..."
if grep -A 10 'mktemp /tmp/opencode-install' "$ROOT_DIR/installers/phases/07-devtools.sh" | grep -q "rm -f.*tmpfile"; then
    echo -e "${GREEN}✓ PASS${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ FAIL${NC}"
    FAILED=$((FAILED + 1))
fi

echo ""
echo "3. Trap Strategy Tests"
echo "──────────────────────"

# Test 11: No trap-based tmpfile cleanup (avoid overriding parent INT handler)
printf "  %-50s " "No tmpfile cleanup traps in phases..."
trap_count=$(grep -h "trap.*rm -f.*tmpfile" "$ROOT_DIR/installers/phases/05-docker.sh" "$ROOT_DIR/installers/phases/07-devtools.sh" 2>/dev/null | wc -l | tr -d ' ' || true)
if [[ "${trap_count:-0}" -eq 0 ]]; then
    echo -e "${GREEN}✓ PASS${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ FAIL${NC} (found $trap_count trap(s), expected 0)"
    FAILED=$((FAILED + 1))
fi

# Test 12: No EXIT traps for tmpfile cleanup
printf "  %-50s " "No EXIT traps for tmpfile cleanup..."
if ! grep -h "trap.*rm -f.*tmpfile.*EXIT" "$ROOT_DIR/installers/phases/05-docker.sh" "$ROOT_DIR/installers/phases/07-devtools.sh" 2>/dev/null; then
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
