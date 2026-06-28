#!/bin/bash
# ODS Validation Script
# Run after install to confirm everything is working

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Source service registry
export SCRIPT_DIR="$PROJECT_DIR"
. "$PROJECT_DIR/lib/service-registry.sh"
sr_load

# Safe .env loading (no eval; use lib/safe-env.sh)
[[ -f "$PROJECT_DIR/lib/safe-env.sh" ]] && . "$PROJECT_DIR/lib/safe-env.sh"
load_env_file "$PROJECT_DIR/.env"
sr_resolve_ports

# Resolve core ports from registry (honoring any env overrides)
LLM_PORT="${OLLAMA_PORT:-${LLAMA_SERVER_PORT:-${SERVICE_PORTS[llama-server]:-11434}}}"
LLM_HEALTH="${SERVICE_HEALTH[llama-server]:-/health}"
WEBUI_PORT="${WEBUI_PORT:-${SERVICE_PORTS[open-webui]:-3000}}"
WEBUI_HEALTH="${WEBUI_HEALTH:-${SERVICE_HEALTH[open-webui]:-/}}"

# Resolve compose flags to match actual stack
COMPOSE_FLAGS=""
if [[ -x "$PROJECT_DIR/scripts/resolve-compose-stack.sh" ]]; then
    # --gpu-count gates the multigpu-{backend}.yml overlay; without it,
    # validation runs against the wrong stack on multi-GPU machines.
    COMPOSE_FLAGS=$("$PROJECT_DIR/scripts/resolve-compose-stack.sh" \
        --script-dir "$PROJECT_DIR" --tier "${TIER:-1}" --gpu-backend "${GPU_BACKEND:-nvidia}" \
        --gpu-count "${GPU_COUNT:-1}")
fi

echo ""
echo "╔═══════════════════════════════════════════╗"
echo "║     ODS Validation Test          ║"
echo "╚═══════════════════════════════════════════╝"
echo ""

PASSED=0
FAILED=0

check() {
    local name="$1"
    local cmd="$2"
    printf "  %-30s " "$name..."
    # Run fixed command string via bash -c (no eval)
    if bash -c "$cmd" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ PASS${NC}"
        ((PASSED++)) || true
    else
        echo -e "${RED}✗ FAIL${NC}"
        ((FAILED++)) || true
    fi
}

echo "1. Container Status"
echo "───────────────────"
check "llama-server running" "docker compose $COMPOSE_FLAGS ps llama-server 2>/dev/null | grep -qE 'Up|running'"
check "Open WebUI running" "docker compose $COMPOSE_FLAGS ps open-webui 2>/dev/null | grep -qE 'Up|running'"

echo ""
echo "2. Health Endpoints"
echo "───────────────────"
check "llama-server health" "curl -sf --max-time 10 http://127.0.0.1:${LLM_PORT}${LLM_HEALTH}"
check "llama-server models" "curl -sf --max-time 10 http://127.0.0.1:${LLM_PORT}/v1/models | grep -q model"
check "WebUI reachable" "curl -sf --max-time 10 http://127.0.0.1:${WEBUI_PORT}${WEBUI_HEALTH} -o /dev/null"

echo ""
echo "3. Inference Test"
echo "─────────────────"
printf "  %-30s " "Chat completion..."
RESPONSE=$(curl -sf --max-time 30 "http://127.0.0.1:${LLM_PORT}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d '{
        "model": "'"$(curl -sf --max-time 10 "http://127.0.0.1:${LLM_PORT}/v1/models" | jq -r '.data[0].id // "local"')"'",
        "messages": [{"role": "user", "content": "Say OK"}],
        "max_tokens": 10
    }' 2>/dev/null) || RESPONSE=""

if echo "$RESPONSE" | grep -q "content"; then
    echo -e "${GREEN}✓ PASS${NC}"
    ((PASSED++)) || true
else
    echo -e "${RED}✗ FAIL${NC}"
    ((FAILED++)) || true
fi

# Check optional services
echo ""
echo "4. Optional Services (if enabled)"
echo "──────────────────────────────────"

SCRIPT_DIR_REG="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$SCRIPT_DIR_REG/lib/service-registry.sh"
sr_load

for sid in "${SERVICE_IDS[@]}"; do
    _cat="${SERVICE_CATEGORIES[$sid]}"
    [[ "$_cat" == "core" ]] && continue  # Core already checked above

    _container="${SERVICE_CONTAINERS[$sid]}"
    _health="${SERVICE_HEALTH[$sid]}"
    _port_env="${SERVICE_PORT_ENVS[$sid]}"
    _default_port="${SERVICE_PORTS[$sid]}"
    _name="${SERVICE_NAMES[$sid]:-$sid}"

    # Resolve port
    _port="$_default_port"
    [[ -n "$_port_env" ]] && _port="${!_port_env:-$_default_port}"

    # Skip if no health endpoint or port
    [[ -z "$_health" || "$_port" == "0" ]] && continue

    # Check if container is running
    if docker compose $COMPOSE_FLAGS ps "$sid" 2>/dev/null | grep -qE "Up|running"; then
        check "$_name" "curl -sf --max-time 10 http://127.0.0.1:${_port}${_health}"
    else
        printf "  %-30s ${YELLOW}○ SKIP (not enabled)${NC}\n" "$_name..."
    fi
done

# Summary
echo ""
echo "═══════════════════════════════════════════"
if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✅ ODS is ready! ($PASSED tests passed)${NC}"
    echo ""
    echo "   Open WebUI:  http://localhost:${WEBUI_PORT}"
    echo "   API:         http://localhost:${LLM_PORT}/v1/..."
    echo ""
else
    echo -e "${RED}⚠️  $FAILED test(s) failed, $PASSED passed${NC}"
    echo ""
    echo "   Troubleshooting:"
    echo "   - Check logs:  docker compose logs -f"
    echo "   - LLM logs:    docker compose logs -f llama-server"
    echo "   - Restart:     docker compose restart"
    echo ""
    exit 1
fi
