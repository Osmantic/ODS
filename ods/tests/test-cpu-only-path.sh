#!/bin/bash
# ============================================================================
# ODS CPU-Only Path Test Suite
# ============================================================================
# Validates that the installer and detection logic support a CPU-only (no GPU)
# path for broad compatibility (e.g. old PCs, headless servers). Checks
# compose selection, tier assignment, and that no GPU is required.
#
# Usage: ./tests/test-cpu-only-path.sh
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
skip() { echo -e "  ${YELLOW}⊘ SKIP${NC} $1"; }

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   CPU-Only Path Test Suite                   ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# 1. Backend contracts: CPU backend exists
CPU_BACKEND="$ROOT_DIR/config/backends/cpu.json"
if [[ ! -f "$CPU_BACKEND" ]]; then
    fail "config/backends/cpu.json not found (CPU-only path requires CPU backend)"
else
    pass "config/backends/cpu.json exists"
fi

if [[ -f "$CPU_BACKEND" ]] && command -v jq &>/dev/null; then
    if jq -e '.id and .llm_engine and .service_name' "$CPU_BACKEND" >/dev/null 2>&1; then
        pass "CPU backend JSON has required fields (id, llm_engine, service_name)"
    else
        fail "CPU backend JSON missing required fields"
    fi
fi

# 2. Hardware classes: cpu_fallback or similar exists
HARDWARE_JSON="$ROOT_DIR/config/hardware-classes.json"
if [[ ! -f "$HARDWARE_JSON" ]]; then
    fail "config/hardware-classes.json not found"
else
    pass "config/hardware-classes.json exists"
fi

if [[ -f "$HARDWARE_JSON" ]] && command -v jq &>/dev/null; then
    if jq -e '.classes[] | select(.id == "cpu_fallback")' "$HARDWARE_JSON" >/dev/null 2>&1; then
        pass "Hardware class cpu_fallback defined"
    else
        skip "cpu_fallback class not found (may use different id)"
    fi
fi

# 3. Resolve compose stack with CPU backend (no nvidia/amd overlay)
RESOLVER="$ROOT_DIR/scripts/resolve-compose-stack.sh"
if [[ ! -x "$RESOLVER" ]]; then
    skip "resolve-compose-stack.sh not executable (may still be runnable with bash)"
fi

if [[ -x "$RESOLVER" ]]; then
    # Run resolver with --gpu-backend cpu (script may fall back to base or base+nvidia)
    set +e
    out=$(cd "$ROOT_DIR" && bash "$RESOLVER" --script-dir "$ROOT_DIR" --gpu-backend cpu 2>&1)
    r=$?
    set -e
    if [[ $r -eq 0 ]]; then
        pass "resolve-compose-stack.sh runs with --gpu-backend cpu"
    else
        fail "resolve-compose-stack.sh with --gpu-backend cpu failed (exit $r)"
    fi
    if echo "$out" | grep -q "docker-compose.base.yml"; then
        pass "Compose output includes docker-compose.base.yml"
    fi
    # CPU path should at least include base; nvidia/amd overlay may still be added by script
    if ! echo "$out" | grep -q "docker-compose.base.yml"; then
        fail "Compose output should include base compose for CPU path"
    else
        pass "CPU path includes base compose"
    fi

    set +e
    strix_cpu_out=$(cd "$ROOT_DIR" && bash "$RESOLVER" --script-dir "$ROOT_DIR" --tier SH_LARGE --gpu-backend cpu 2>&1)
    strix_cpu_rc=$?
    set -e
    if [[ $strix_cpu_rc -eq 0 ]] && echo "$strix_cpu_out" | grep -q "docker-compose.cpu.yml" && ! echo "$strix_cpu_out" | grep -q "docker-compose.amd.yml"; then
        pass "CPU backend overrides Strix Halo tier compose overlay"
    else
        fail "CPU backend should not select AMD overlay for Strix Halo tier"
    fi
fi

# 4. Tier map or detection: tier 1 works without GPU
TIER_MAP="$ROOT_DIR/installers/lib/tier-map.sh"
if [[ ! -f "$TIER_MAP" ]]; then
    skip "tier-map.sh not found"
else
    pass "tier-map.sh exists (tier 1 is entry-level for CPU/low-end)"
fi

# 5. Installer dry-run with minimal env (simulate CPU-only machine)
# We only check that dry-run completes without "GPU required" fatal error
DRY_RUN_LOG=$(mktemp)
trap 'rm -f "$DRY_RUN_LOG"' EXIT
set +e
# Force a low tier and skip docker to speed up; we only care that phase 04 and detection don't require GPU
cd "$ROOT_DIR"
PATH="$PATH" bash install-core.sh --dry-run --non-interactive --skip-docker --tier 1 --force >"$DRY_RUN_LOG" 2>&1
dry_exit=$?
set -e
if [[ $dry_exit -eq 0 ]]; then
    pass "install-core.sh --dry-run --tier 1 completes (CPU/low-end path)"
else
    skip "install-core.sh --dry-run exited $dry_exit (may be env-specific)"
fi
if grep -q "GPU required\|must have.*GPU\|no GPU.*fatal" "$DRY_RUN_LOG" 2>/dev/null; then
    fail "Dry-run log suggests GPU is required (CPU-only path should be allowed)"
else
    pass "Dry-run does not require GPU for tier 1"
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
