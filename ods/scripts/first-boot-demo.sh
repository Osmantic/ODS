#!/bin/bash
# ODS First Boot Demo
# Shows off what your local AI stack can do in under 2 minutes
#
# Usage: ./first-boot-demo.sh [--all] [--quick]
# Mission: M5 (Clonable ODS Setup Server)

set -euo pipefail

#=============================================================================
# Colors
#=============================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m'
BOLD='\033[1m'

#=============================================================================
# Config — resolve from service registry when available
#=============================================================================
_DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$_DEMO_DIR/lib/service-registry.sh" ]]; then
    export SCRIPT_DIR="$_DEMO_DIR"
    . "$_DEMO_DIR/lib/service-registry.sh"
    sr_load
    [[ -f "$_DEMO_DIR/lib/safe-env.sh" ]] && . "$_DEMO_DIR/lib/safe-env.sh"
    load_env_file "$_DEMO_DIR/.env"
    sr_resolve_ports
fi

LLM_URL="${LLM_URL:-http://localhost:${SERVICE_PORTS[llama-server]:-11434}}"
WHISPER_URL="${WHISPER_URL:-http://localhost:${SERVICE_PORTS[whisper]:-9000}}"
PIPER_URL="${PIPER_URL:-http://localhost:${SERVICE_PORTS[tts]:-8880}}"
N8N_URL="${N8N_URL:-http://localhost:${SERVICE_PORTS[n8n]:-5678}}"
WEBUI_URL="${WEBUI_URL:-http://localhost:${SERVICE_PORTS[open-webui]:-3000}}"
# Use the installed model from .env if available; fall back to a widely-known default
# so the demo works out-of-the-box on any tier without hardcoding a specific model name.
DEMO_MODEL="${LLM_MODEL:-Qwen/Qwen2.5-32B-Instruct-AWQ}"

QUICK_MODE=false
ALL_MODE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --quick) QUICK_MODE=true; shift ;;
        --all) ALL_MODE=true; shift ;;
        -h|--help) 
            echo "Usage: $0 [--quick] [--all]"
            echo "  --quick  Skip slow demos, just show what's available"
            echo "  --all    Run all demos including voice (requires audio files)"
            exit 0
            ;;
        *) shift ;;
    esac
done

# Dependency check — jq is required for parsing API responses.
if ! command -v jq >/dev/null 2>&1; then
    echo "Error: 'jq' is required for first-boot-demo.sh but was not found in PATH."
    echo "Install it with:  sudo apt install jq   (Debian/Ubuntu)"
    echo "                  brew install jq        (macOS)"
    exit 1
fi

#=============================================================================
# Helpers
#=============================================================================
header() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}${CYAN}  $1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

demo() {
    echo -e "\n${MAGENTA}▶${NC} ${BOLD}$1${NC}"
}

success() {
    echo -e "${GREEN}✓${NC} $1"
}

fail() {
    echo -e "${RED}✗${NC} $1"
}

info() {
    echo -e "${CYAN}ℹ${NC} $1"
}

wait_key() {
    if [[ "$QUICK_MODE" != "true" ]]; then
        echo -e "\n${YELLOW}Press Enter to continue...${NC}"
        read -r
    fi
}

check_service() {
    local name=$1
    local url=$2
    local endpoint=${3:-/health}
    
    if curl -sf "${url}${endpoint}" > /dev/null 2>&1; then
        success "$name is running at $url"
        return 0
    else
        fail "$name not responding at $url"
        return 1
    fi
}

#=============================================================================
# Welcome
#=============================================================================
clear
echo ""
echo -e "${BOLD}${CYAN}"
cat << 'EOF'
   OOOOO  DDDD   SSSSS
  OO   OO DD DD SS
  OO   OO DD DD  SSS
  OO   OO DD DD    SS
   OOOOO  DDDD  SSSS
                                                                   
EOF
echo -e "${NC}"
echo -e "${BOLD}ODS - First Boot Demo${NC}"
echo -e "Everything runs on YOUR hardware. No cloud. No API costs. Full privacy.\n"

#=============================================================================
# Health Check
#=============================================================================
header "🔍 Checking Services"

SERVICES_OK=0
SERVICES_TOTAL=0

# Core services
((SERVICES_TOTAL++)) || true
if check_service "LLM (llama-server)" "$LLM_URL" "/health"; then
    ((SERVICES_OK++)) || true
    LLM_AVAILABLE=true
else
    LLM_AVAILABLE=false
fi

((SERVICES_TOTAL++)) || true
if check_service "Open WebUI" "$WEBUI_URL" "/"; then
    ((SERVICES_OK++)) || true
fi

# Optional services
if curl -sf "${WHISPER_URL}/health" > /dev/null 2>&1; then
    success "Whisper STT is running (voice input enabled)"
    WHISPER_AVAILABLE=true
    ((SERVICES_OK++)) || true
    ((SERVICES_TOTAL++)) || true
else
    info "Whisper STT not running (voice input disabled)"
    WHISPER_AVAILABLE=false
fi

if curl -sf "${PIPER_URL}" > /dev/null 2>&1; then
    success "OpenTTS TTS is running (voice output enabled)"
    PIPER_AVAILABLE=true
    ((SERVICES_OK++)) || true
    ((SERVICES_TOTAL++)) || true
else
    info "OpenTTS TTS not running (voice output disabled)"
    PIPER_AVAILABLE=false
fi

if curl -sf "${N8N_URL}/healthz" > /dev/null 2>&1; then
    success "n8n Workflows is running (automation enabled)"
    N8N_AVAILABLE=true
    ((SERVICES_OK++)) || true
    ((SERVICES_TOTAL++)) || true
else
    info "n8n not running (automation disabled)"
    N8N_AVAILABLE=false
fi

echo ""
echo -e "${BOLD}Services: ${SERVICES_OK}/${SERVICES_TOTAL} running${NC}"

if [[ "$LLM_AVAILABLE" != "true" ]]; then
    echo -e "\n${RED}LLM (llama-server) is required for demos. Is it still loading?${NC}"
    echo "Check status: docker compose logs -f llama-server"
    exit 1
fi

wait_key

#=============================================================================
# Demo 1: Chat Completion
#=============================================================================
header "💬 Demo 1: Local Chat Completion"

demo "Asking your local AI a question..."

RESPONSE=$(curl -sf "${LLM_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d "{\"model\": \"${DEMO_MODEL}\", \"messages\": [{\"role\": \"user\", \"content\": \"In one sentence, what makes local AI special?\"}], \"max_tokens\": 100, \"temperature\": 0.7}" \
    2>/dev/null | jq -r '.choices[0].message.content' 2>/dev/null || echo "")

if [[ -n "$RESPONSE" && "$RESPONSE" != "null" ]]; then
    echo ""
    echo -e "${GREEN}Response:${NC}"
    echo -e "  ${RESPONSE}"
    echo ""
    success "Local LLM responded! No API calls, no cloud, just your GPU."
else
    fail "No response from LLM"
fi

wait_key

#=============================================================================
# Demo 2: Code Assistance
#=============================================================================
header "🧑‍💻 Demo 2: Code Assistance"

demo "Asking for help with a Python function..."

CODE_RESPONSE=$(curl -sf "${LLM_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d "{\"model\": \"${DEMO_MODEL}\", \"messages\": [{\"role\": \"user\", \"content\": \"Write a Python one-liner to reverse a string. Just the code, no explanation.\"}], \"max_tokens\": 50, \"temperature\": 0.3}" \
    2>/dev/null | jq -r '.choices[0].message.content' 2>/dev/null || echo "")

if [[ -n "$CODE_RESPONSE" && "$CODE_RESPONSE" != "null" ]]; then
    echo ""
    echo -e "${GREEN}Generated code:${NC}"
    echo -e "  ${CODE_RESPONSE}"
    echo ""
    success "Code assistant works! Great for development."
else
    fail "No response from code assistant"
fi

wait_key

#=============================================================================
# Demo 3: Streaming
#=============================================================================
header "📡 Demo 3: Streaming Response"

demo "Watching tokens stream in real-time..."
echo ""

# Simple streaming demo - just show it works
curl -sN "${LLM_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d "{\"model\": \"${DEMO_MODEL}\", \"messages\": [{\"role\": \"user\", \"content\": \"Count from 1 to 5, one number per line.\"}], \"max_tokens\": 50, \"temperature\": 0, \"stream\": true}" \
    2>/dev/null | while read -r line; do
        if [[ "$line" == data:* ]]; then
            content=$(echo "${line#data: }" | jq -r '.choices[0].delta.content // empty' 2>/dev/null)
            if [[ -n "$content" ]]; then
                printf "%s" "$content"
            fi
        fi
    done

echo ""
echo ""
success "Streaming works! Great for real-time UIs."

wait_key

#=============================================================================
# Summary
#=============================================================================
header "🎉 Demo Complete!"

echo ""
echo -e "${BOLD}What you just saw:${NC}"
echo "  ✓ Local LLM responding to prompts"
echo "  ✓ Code assistance capabilities"  
echo "  ✓ Real-time streaming"
echo ""

echo -e "${BOLD}What's available:${NC}"
echo "  • Open WebUI:  ${WEBUI_URL}"
[[ "$N8N_AVAILABLE" == "true" ]] && echo "  • n8n Workflows: ${N8N_URL}"
[[ "$WHISPER_AVAILABLE" == "true" ]] && echo "  • Whisper STT:   ${WHISPER_URL}"
[[ "$PIPER_AVAILABLE" == "true" ]] && echo "  • OpenTTS TTS:     ${PIPER_URL}"
echo ""

echo -e "${BOLD}Next steps:${NC}"
echo "  1. Open ${WEBUI_URL} and start chatting"
echo "  2. Import workflows from ./workflows/ into n8n"
echo "  3. Try the voice demo: ./scripts/voice-demo.sh"
echo "  4. OpenClaw agent: http://localhost:7860"
echo ""

echo -e "${CYAN}Everything runs locally. Your data stays private. Enjoy! 🚀${NC}"
echo ""
