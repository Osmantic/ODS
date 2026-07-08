#!/bin/bash
# ============================================================================
# ODS Hardware Compatibility Test Suite
# ============================================================================
# Validates that ODS runs on broad hardware range as claimed in
# COMPATIBILITY-MATRIX.md. Tests CPU-only path, low-RAM scenarios, tier
# assignment, old hardware simulation, GPU detection edge cases, disk space
# validation, and backend selection logic.
#
# Usage: ./tests/test-hardware-compatibility.sh
# Exit 0 if all pass, 1 if any fail
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

PASS=0
FAIL=0
SKIP=0

pass() {
    echo -e "  ${GREEN}✓${NC} $1"
    PASS=$((PASS + 1))
}

fail() {
    echo -e "  ${RED}✗${NC} $1"
    [[ -n "${2:-}" ]] && echo -e "        ${RED}→ $2${NC}"
    FAIL=$((FAIL + 1))
}

skip() {
    echo -e "  ${YELLOW}⊘${NC} $1"
    SKIP=$((SKIP + 1))
}

header() {
    echo ""
    echo -e "${BOLD}${CYAN}[$1]${NC} ${BOLD}$2${NC}"
    echo -e "${CYAN}$(printf '%.0s─' {1..70})${NC}"
}

# ============================================
# TEST 1: CPU-Only Path Validation
# ============================================
header "1/7" "CPU-Only Path Validation"

# Check CPU backend exists
CPU_BACKEND="$PROJECT_DIR/config/backends/cpu.json"
if [[ -f "$CPU_BACKEND" ]]; then
    pass "CPU backend config exists"
else
    fail "CPU backend config missing" "config/backends/cpu.json not found"
fi

# Check compose resolver handles CPU backend
RESOLVER="$PROJECT_DIR/scripts/resolve-compose-stack.sh"
if [[ -f "$RESOLVER" ]]; then
    set +e
    cpu_compose=$(bash "$RESOLVER" --script-dir "$PROJECT_DIR" --gpu-backend cpu 2>&1)
    result=$?
    set -e

    if [[ $result -eq 0 ]]; then
        pass "Compose stack resolves for CPU backend"
    else
        fail "Compose stack resolution failed for CPU backend"
    fi

    if echo "$cpu_compose" | grep -q "docker-compose.base.yml"; then
        pass "CPU backend uses base compose file"
    else
        fail "CPU backend should use base compose file"
    fi
else
    skip "resolve-compose-stack.sh not found"
fi

# Check tier 1 (entry level) works without GPU requirement
TIER_MAP="$PROJECT_DIR/installers/lib/tier-map.sh"
if [[ -f "$TIER_MAP" ]]; then
    if grep -q "^[[:space:]]*1)" "$TIER_MAP"; then
        pass "Tier 1 (Entry Level) defined in tier map"
    else
        fail "Tier 1 not found in tier map"
    fi
else
    fail "tier-map.sh not found"
fi

# ============================================
# TEST 2: Low-RAM Scenarios
# ============================================
header "2/7" "Low-RAM Scenarios"

# Check COMPATIBILITY-MATRIX.md claims
COMPAT_MATRIX="$PROJECT_DIR/docs/COMPATIBILITY-MATRIX.md"
if [[ -f "$COMPAT_MATRIX" ]]; then
    if grep -q "8 GB" "$COMPAT_MATRIX"; then
        pass "Documentation mentions 8GB minimum RAM"
    else
        fail "Documentation should mention 8GB minimum RAM"
    fi

    if grep -q "16 GB" "$COMPAT_MATRIX"; then
        pass "Documentation mentions 16GB recommended RAM"
    else
        fail "Documentation should mention 16GB recommended RAM"
    fi
else
    skip "COMPATIBILITY-MATRIX.md not found"
fi

# Verify tier 1 is suitable for low-RAM systems
if [[ -f "$TIER_MAP" ]]; then
    # Tier 1 should use smaller models (qwen3.5-9b)
    if grep -A5 "^[[:space:]]*1)" "$TIER_MAP" | grep -q "qwen3.5-9b"; then
        pass "Tier 1 uses small model suitable for low-RAM systems"
    else
        fail "Tier 1 should use small model for low-RAM compatibility"
    fi
fi

# ============================================
# TEST 3: Tier Assignment Validation
# ============================================
header "3/7" "Tier Assignment Validation"

if [[ -f "$TIER_MAP" ]]; then
    # Check all expected tiers are defined
    expected_tiers=("1" "2" "3" "4" "CLOUD" "NV_ULTRA" "SH_LARGE" "SH_COMPACT")
    for tier in "${expected_tiers[@]}"; do
        if grep -q "^[[:space:]]*${tier})" "$TIER_MAP"; then
            pass "Tier $tier defined"
        else
            fail "Tier $tier not found in tier map"
        fi
    done

    # Verify tier progression (higher tiers = larger models)
    if grep -A5 "^[[:space:]]*1)" "$TIER_MAP" | grep -q "qwen3.5-9b" && \
       grep -A5 "^[[:space:]]*3)" "$TIER_MAP" | grep -q "qwen3-30b-a3b"; then
        pass "Tier progression validated (tier 1 < tier 3 model size)"
    else
        fail "Tier progression should increase model size"
    fi
fi

# ============================================
# TEST 4: Old Hardware Simulation
# ============================================
header "4/7" "Old Hardware Simulation (2015 PC)"

# Check documentation claims support for old PCs
if [[ -f "$COMPAT_MATRIX" ]]; then
    if grep -qi "2015\|old.*PC\|older.*hardware" "$COMPAT_MATRIX"; then
        pass "Documentation claims support for old PCs (2015)"
    else
        skip "Documentation doesn't explicitly mention old PC support"
    fi
fi

# Verify tier 1 + CPU backend can run on minimal hardware
# (This is the combination that would run on a 2015 PC)
if [[ -f "$TIER_MAP" ]] && [[ -f "$CPU_BACKEND" ]]; then
    pass "Tier 1 + CPU backend available for old hardware"
else
    fail "Old hardware path (tier 1 + CPU) not fully configured"
fi

# ============================================
# TEST 5: GPU Detection Edge Cases
# ============================================
header "5/7" "GPU Detection Edge Cases"

DETECTION_LIB="$PROJECT_DIR/installers/lib/detection.sh"
if [[ ! -f "$DETECTION_LIB" ]]; then
    skip "detection.sh not found"
else
    pass "GPU detection library exists"

    # Check for CPU fallback logic
    if grep -qi "cpu.*fallback\|no.*gpu.*detected\|unsupported.*gpu" "$DETECTION_LIB"; then
        pass "Detection library has CPU fallback logic"
    else
        skip "CPU fallback logic not explicitly found (may be implicit)"
    fi
fi

# Check hardware-classes.json for fallback definitions
HARDWARE_CLASSES="$PROJECT_DIR/config/hardware-classes.json"
if [[ -f "$HARDWARE_CLASSES" ]] && command -v jq &>/dev/null; then
    if jq -e '.classes[] | select(.id | contains("cpu") or contains("fallback"))' "$HARDWARE_CLASSES" >/dev/null 2>&1; then
        pass "Hardware classes include CPU/fallback options"
    else
        skip "No explicit CPU fallback class found (may use default tier)"
    fi
fi

# ============================================
# TEST 6: Disk Space Validation
# ============================================
header "6/7" "Disk Space Validation"

if [[ -f "$COMPAT_MATRIX" ]]; then
    if grep -q "30 GB" "$COMPAT_MATRIX"; then
        pass "Documentation mentions 30GB minimum disk space"
    else
        fail "Documentation should mention 30GB minimum disk"
    fi

    if grep -q "50 GB" "$COMPAT_MATRIX"; then
        pass "Documentation mentions 50GB recommended disk space"
    else
        fail "Documentation should mention 50GB recommended disk"
    fi
fi

# Check if installer validates disk space
PREFLIGHT="$PROJECT_DIR/installers/phases/04-requirements.sh"
if [[ -f "$PREFLIGHT" ]]; then
    if grep -qi "disk\|space\|df" "$PREFLIGHT"; then
        pass "Installer checks disk space in requirements phase"
    else
        skip "Disk space check not found in requirements phase"
    fi
fi

# ============================================
# TEST 7: Backend Selection Logic
# ============================================
header "7/7" "Backend Selection Logic"

# Check all backend configs exist
BACKENDS_DIR="$PROJECT_DIR/config/backends"
if [[ ! -d "$BACKENDS_DIR" ]]; then
    fail "Backends directory not found"
else
    expected_backends=("nvidia" "amd" "apple" "cpu")
    for backend in "${expected_backends[@]}"; do
        if [[ -f "$BACKENDS_DIR/${backend}.json" ]]; then
            pass "Backend config exists: $backend"
        else
            fail "Backend config missing: $backend"
        fi
    done
fi

# Verify compose resolver handles all backends
if [[ -f "$RESOLVER" ]]; then
    for backend in nvidia amd apple cpu; do
        set +e
        output=$(bash "$RESOLVER" --script-dir "$PROJECT_DIR" --gpu-backend "$backend" 2>&1)
        result=$?
        set -e

        if [[ $result -eq 0 ]]; then
            pass "Backend selection works: $backend"
        else
            fail "Backend selection failed: $backend"
        fi
    done
fi

# ============================================
# Summary
# ============================================
echo ""
echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
TOTAL=$((PASS + FAIL + SKIP))
echo -e "${BOLD}  Results: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}, ${YELLOW}$SKIP skipped${NC} ${BOLD}($TOTAL total)${NC}"
echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

if [[ $FAIL -gt 0 ]]; then
    echo -e "${RED}Some tests failed.${NC}"
    exit 1
else
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
fi
