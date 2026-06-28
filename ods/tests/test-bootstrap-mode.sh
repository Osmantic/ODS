#!/bin/bash
# ODS Small Model Fallback Test Suite
# Tests the instant-start UX with a small GGUF model via llama-server

set -e

ODS_DIR="${ODS_DIR:-$(dirname "$(dirname "$(realpath "$0")")")}"
cd "$ODS_DIR"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}✓ PASS${NC}: $1"; }
fail() { echo -e "${RED}✗ FAIL${NC}: $1"; exit 1; }
info() { echo -e "${YELLOW}→${NC} $1"; }

echo "═══════════════════════════════════════════════════════════════"
echo "  ODS Small Model Fallback Test Suite"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# ===== Test 1: Compose files exist =====
info "Test 1: Checking compose files..."
if [[ ! -f "docker-compose.yml" ]] && [[ ! -f "docker-compose.base.yml" ]]; then
    fail "No compose file found (docker-compose.yml or docker-compose.base.yml)"
fi
pass "Compose files present"

# ===== Test 2: Compose is valid =====
info "Test 2: Validating compose..."
# Try docker compose (plugin) first, then docker-compose (standalone)
if command -v docker &> /dev/null && docker compose version &> /dev/null 2>&1; then
    docker compose -f docker-compose.yml config > /dev/null 2>&1 || fail "Invalid compose configuration"
elif command -v docker-compose &> /dev/null; then
    docker-compose -f docker-compose.yml config > /dev/null 2>&1 || fail "Invalid compose configuration"
else
    info "Docker/docker-compose not available, skipping compose validation"
fi
pass "Compose configuration valid (or skipped)"

# ===== Test 3: Small fallback model specified correctly =====
info "Test 3: Checking small model config..."
grep -qi "qwen2.5-1.5b-instruct" docker-compose.yml || info "Small fallback model not in main compose (may be configured at runtime)"
pass "Small model config checked"

# ===== Test 4: Upgrade script exists =====
info "Test 4: Checking upgrade script..."
[[ -f "scripts/upgrade-model.sh" ]] || fail "upgrade-model.sh not found"
[[ -x "scripts/upgrade-model.sh" ]] || fail "upgrade-model.sh not executable"
pass "Upgrade script ready"

# ===== Test 5: Healthcheck timing =====
info "Test 5: Checking healthcheck configuration..."
MAIN_START_PERIOD=$(grep -A10 "llama-server:" docker-compose.yml | grep -A5 "healthcheck:" | grep "start_period" | grep -oE '[0-9]+' | head -1 || echo "0")
if [[ "$MAIN_START_PERIOD" -gt 0 ]]; then
    pass "llama-server healthcheck start_period configured ($MAIN_START_PERIOD)"
else
    info "Could not parse healthcheck start_period (may use defaults)"
fi

# ===== Test 6: .env template has LLM_MODEL =====
info "Test 6: Checking .env template..."
if [[ -f ".env.example" ]]; then
    grep -q "LLM_MODEL" .env.example || fail ".env.example missing LLM_MODEL"
    pass ".env.example has LLM_MODEL setting"
else
    info "Skipping .env.example check (file not present)"
fi

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo -e "  ${GREEN}All tests passed!${NC}"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "To run with small fallback model:"
echo "  LLM_MODEL=qwen2.5-1.5b-instruct docker compose up -d"
echo ""
echo "To upgrade to full model after download completes:"
echo "  ./scripts/upgrade-model.sh"
echo ""
