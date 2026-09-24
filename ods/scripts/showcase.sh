#!/bin/bash
# ODS Interactive Showcase
# Demonstrates all capabilities in an interactive menu

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_DIR="$(dirname "$SCRIPT_DIR")"

# Source service registry for port resolution
# Declared unconditionally so the ${SERVICE_*[x]:-default} fallbacks below work
# without the registry — unset subscripts abort under set -u before they apply.
declare -A SERVICE_PORTS SERVICE_HEALTH SERVICE_NAMES
declare -a SERVICE_IDS
if [[ -f "$ODS_DIR/lib/service-registry.sh" ]]; then
    export SCRIPT_DIR="$ODS_DIR"
    . "$ODS_DIR/lib/service-registry.sh"
    sr_load
    [[ -f "$ODS_DIR/lib/safe-env.sh" ]] && . "$ODS_DIR/lib/safe-env.sh"
    load_env_file "$ODS_DIR/.env"
    sr_resolve_ports
fi

# URLs — resolved from registry
LLM_URL="${LLM_URL:-http://localhost:${SERVICE_PORTS[llama-server]:-11434}}"
WHISPER_URL="${WHISPER_URL:-http://localhost:${SERVICE_PORTS[whisper]:-9000}}"
TTS_URL="${TTS_URL:-http://localhost:${SERVICE_PORTS[tts]:-8880}}"
QDRANT_URL="${QDRANT_URL:-http://localhost:${SERVICE_PORTS[qdrant]:-6333}}"
EXAMPLES_DIR="$ODS_DIR/examples"

# Prefer the installed model id so requests never name a checkpoint the server
# doesn't have — llama.cpp rejects unknown model names on /v1/chat/completions.
_detected_model="$(curl -s --max-time 5 "${LLM_URL}/v1/models" 2>/dev/null \
    | jq -r '.data[0].id // empty' 2>/dev/null)" || _detected_model=""
DEMO_MODEL="${DEMO_MODEL:-${_detected_model:-local}}"

clear_screen() {
    printf "\033[2J\033[H"
}

print_header() {
    echo ""
    echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}${CYAN}║               ODS Interactive Showcase              ║${NC}"
    echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

print_menu() {
    echo -e "${BOLD}What would you like to try?${NC}"
    echo ""
    echo -e "  ${CYAN}[1]${NC} 💬 Chat with AI"
    echo -e "  ${CYAN}[2]${NC} 🎤 Voice-to-Voice Demo"
    echo -e "  ${CYAN}[3]${NC} 📚 Document Q&A (RAG)"
    echo -e "  ${CYAN}[4]${NC} 💻 Code Assistant"
    echo -e "  ${CYAN}[5]${NC} 📊 System Status"
    echo -e "  ${CYAN}[Q]${NC} 🚪 Quit"
    echo ""
    echo -ne "${BOLD}Select an option: ${NC}"
}

check_service() {
    local url=$1
    local endpoint=$2
    curl -sf "${url}${endpoint}" > /dev/null 2>&1
}

demo_chat() {
    clear_screen
    echo -e "${BOLD}${MAGENTA}💬 Chat with AI${NC}"
    echo -e "${DIM}────────────────────────────────────────${NC}"
    echo ""
    
    if ! check_service "$LLM_URL" "/health"; then
        echo -e "${RED}Error: LLM is not running${NC}"
        echo "Start ODS first: docker compose up -d"
        return
    fi
    
    echo -e "${DIM}Type your message (or 'back' to return to menu):${NC}"
    echo ""
    
    while true; do
        echo -ne "${GREEN}You: ${NC}"
        read -r user_input || return
        
        if [[ "${user_input,,}" == "back" ]]; then
            return
        fi
        
        if [[ -z "$user_input" ]]; then
            continue
        fi
        
        echo -ne "${CYAN}AI: ${NC}"

        # curl and jq run as separate commands: a `curl | jq` pipeline aborts the
        # script under pipefail on a failed request, so the error fallback would
        # never render.
        raw=$(curl -sf "${LLM_URL}/v1/chat/completions" \
            -H "Content-Type: application/json" \
            -d "$(jq -n --arg model "$DEMO_MODEL" --arg msg "$user_input" '{
                model: $model,
                messages: [{role: "user", content: $msg}],
                max_tokens: 512,
                temperature: 0.7
            }')" 2>/dev/null) || raw=""
        response=$(printf '%s' "$raw" | jq -r '.choices[0].message.content // empty' 2>/dev/null) || response=""

        echo "${response:-Error getting response}"
        echo ""
    done
}

demo_voice() {
    clear_screen
    echo -e "${BOLD}${MAGENTA}🎤 Voice-to-Voice Demo${NC}"
    echo -e "${DIM}────────────────────────────────────────${NC}"
    echo ""
    
    if ! check_service "$WHISPER_URL" "/health"; then
        echo -e "${YELLOW}Whisper (STT) not running. Voice input disabled.${NC}"
        echo -e "${DIM}Enable with: docker compose ps whisper  # Voice services start with the stack${NC}"
        echo ""
    fi
    
    if ! check_service "$TTS_URL" "/health"; then
        echo -e "${YELLOW}Kokoro (TTS) not running. Voice output disabled.${NC}"
        echo -e "${DIM}Enable with: docker compose ps tts  # Voice services start with the stack${NC}"
        echo ""
    fi

    # Check for example audio
    if [[ -f "$EXAMPLES_DIR/sample-audio.wav" ]]; then
        echo -e "${GREEN}Found example audio file${NC}"
        echo ""
        echo "To test voice-to-voice:"
        echo ""
        echo -e "  ${CYAN}# Transcribe audio${NC}"
        echo "  curl -X POST ${WHISPER_URL}/v1/audio/transcriptions -F 'file=@${EXAMPLES_DIR}/sample-audio.wav' -F 'model=whisper-1'"
        echo ""
        echo -e "  ${CYAN}# Generate speech${NC}"
        echo "  curl -X POST ${TTS_URL}/v1/audio/speech -H 'Content-Type: application/json' -d '{\"model\": \"kokoro\", \"input\": \"Hello from ODS\", \"voice\": \"af_bella\", \"response_format\": \"wav\"}' -o output.wav"
    else
        echo "Voice demo requires audio recording."
        echo ""
        echo -e "${CYAN}Quick test:${NC}"
        echo ""
        echo "  # Record 5 seconds of audio (requires sox)"
        echo "  rec -r 16000 -c 1 test.wav trim 0 5"
        echo ""
        echo "  # Transcribe"
        echo "  curl -X POST ${WHISPER_URL}/v1/audio/transcriptions -F 'file=@test.wav' -F 'model=whisper-1'"
        echo ""
        echo "  # Text to speech"
        echo "  curl -X POST ${TTS_URL}/v1/audio/speech -H 'Content-Type: application/json' -d '{\"model\": \"kokoro\", \"input\": \"Your text here\", \"voice\": \"af_bella\", \"response_format\": \"wav\"}' -o output.wav"
    fi
    
    echo ""
    echo -e "${DIM}Press Enter to return to menu...${NC}"
    read -r || return
}

demo_rag() {
    clear_screen
    echo -e "${BOLD}${MAGENTA}📚 Document Q&A (RAG Demo)${NC}"
    echo -e "${DIM}────────────────────────────────────────${NC}"
    echo ""
    
    if ! check_service "$LLM_URL" "/health"; then
        echo -e "${RED}Error: LLM is not running${NC}"
        return
    fi
    
    if ! check_service "$QDRANT_URL" "/healthz"; then
        echo -e "${YELLOW}Qdrant not running. Enable with: docker compose ps qdrant  # RAG services start with the stack${NC}"
        echo ""
        echo -e "${DIM}Press Enter to return to menu...${NC}"
        read -r || return
        return
    fi

    # Use example doc if available
    if [[ -f "$EXAMPLES_DIR/sample-doc.txt" ]]; then
        echo -e "${GREEN}Using example document...${NC}"
        DOC_CONTENT=$(cat "$EXAMPLES_DIR/sample-doc.txt")
    else
        echo "Enter document text (or paste content, then Ctrl+D):"
        DOC_CONTENT=$(cat)
    fi
    
    if [[ -z "$DOC_CONTENT" ]]; then
        echo -e "${YELLOW}No content provided${NC}"
        return
    fi
    
    echo ""
    echo -e "${CYAN}Indexing document...${NC}"
    
    # Simple RAG demo using direct LLM (without full workflow for CLI demo)
    echo -e "${GREEN}✓ Document loaded (${#DOC_CONTENT} chars)${NC}"
    echo ""
    echo -e "${DIM}Ask questions about the document (or 'back' to return):${NC}"
    echo ""
    
    while true; do
        echo -ne "${GREEN}Question: ${NC}"
        read -r question || return
        
        if [[ "${question,,}" == "back" ]]; then
            return
        fi
        
        if [[ -z "$question" ]]; then
            continue
        fi
        
        echo -ne "${CYAN}Answer: ${NC}"

        # Use document as context (curl/jq split — see demo_chat for why)
        raw=$(curl -sf "${LLM_URL}/v1/chat/completions" \
            -H "Content-Type: application/json" \
            -d "$(jq -n --arg model "$DEMO_MODEL" --arg doc "$DOC_CONTENT" --arg q "$question" '{
                model: $model,
                messages: [
                    {role: "system", content: "Answer questions based on the provided document. Be concise and cite relevant parts."},
                    {role: "user", content: ("Document:\n" + $doc + "\n\nQuestion: " + $q)}
                ],
                max_tokens: 512,
                temperature: 0.3
            }')" 2>/dev/null) || raw=""
        response=$(printf '%s' "$raw" | jq -r '.choices[0].message.content // empty' 2>/dev/null) || response=""

        echo "${response:-Error getting response}"
        echo ""
    done
}

demo_code() {
    clear_screen
    echo -e "${BOLD}${MAGENTA}💻 Code Assistant${NC}"
    echo -e "${DIM}────────────────────────────────────────${NC}"
    echo ""
    
    if ! check_service "$LLM_URL" "/health"; then
        echo -e "${RED}Error: LLM is not running${NC}"
        return
    fi
    
    echo "What would you like to do?"
    echo ""
    echo -e "  ${CYAN}[1]${NC} Explain code"
    echo -e "  ${CYAN}[2]${NC} Improve code"
    echo -e "  ${CYAN}[3]${NC} Debug code"
    echo -e "  ${CYAN}[4]${NC} Add documentation"
    echo -e "  ${CYAN}[5]${NC} Generate tests"
    echo ""
    echo -ne "Select task: "
    read -r task_choice || return
    
    case $task_choice in
        1) task="explain" ;;
        2) task="improve" ;;
        3) task="debug" ;;
        4) task="document" ;;
        5) task="test" ;;
        *) task="explain" ;;
    esac
    
    echo ""
    
    # Use example code if available
    if [[ -f "$EXAMPLES_DIR/sample-code.py" ]]; then
        echo -e "${GREEN}Using example Python code...${NC}"
        CODE=$(cat "$EXAMPLES_DIR/sample-code.py")
        echo ""
        echo -e "${DIM}$CODE${NC}"
    else
        echo "Paste your code (then Ctrl+D):"
        CODE=$(cat)
    fi
    
    if [[ -z "$CODE" ]]; then
        echo -e "${YELLOW}No code provided${NC}"
        return
    fi
    
    echo ""
    echo -e "${CYAN}Analyzing...${NC}"
    echo ""
    
    prompt="Task: $task\n\nCode:\n\`\`\`\n$CODE\n\`\`\`"
    
    raw=$(curl -sf "${LLM_URL}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$(jq -n --arg model "$DEMO_MODEL" --arg p "$prompt" '{
            model: $model,
            messages: [
                {role: "system", content: "You are an expert code reviewer. Provide clear, actionable feedback."},
                {role: "user", content: $p}
            ],
            max_tokens: 2048,
            temperature: 0.3
        }')" 2>/dev/null) || raw=""
    response=$(printf '%s' "$raw" | jq -r '.choices[0].message.content // empty' 2>/dev/null) || response=""

    echo -e "${GREEN}Result:${NC}"
    echo "${response:-Error getting response}"
    
    echo ""
    echo -e "${DIM}Press Enter to return to menu...${NC}"
    read -r || return
}

show_status() {
    clear_screen
    echo -e "${BOLD}${MAGENTA}📊 System Status${NC}"
    echo -e "${DIM}────────────────────────────────────────${NC}"
    echo ""
    
    echo -e "${BOLD}Services:${NC}"
    echo ""
    
    for sid in "${SERVICE_IDS[@]}"; do
        _port="${SERVICE_PORTS[$sid]:-0}"
        _health="${SERVICE_HEALTH[$sid]:-/health}"
        _name="${SERVICE_NAMES[$sid]:-$sid}"
        [[ "$_port" == "0" ]] && continue
        _url="http://localhost:${_port}"
        if check_service "$_url" "$_health"; then
            echo -e "  ${GREEN}✓${NC} $_name ${DIM}($_url)${NC}"
        else
            echo -e "  ${RED}✗${NC} $_name ${DIM}($_url)${NC}"
        fi
    done
    
    echo ""
    echo -e "${BOLD}GPU:${NC}"
    echo ""
    
    if command -v nvidia-smi &> /dev/null; then
        # Process substitution keeps a failed nvidia-smi from tripping pipefail.
        while read -r line; do
            echo -e "  ${CYAN}$line${NC}"
        done < <(nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader 2>/dev/null)
    else
        echo -e "  ${DIM}nvidia-smi not available${NC}"
    fi
    
    echo ""
    echo -e "${BOLD}Quick Links:${NC}"
    echo ""
    echo -e "  Chat UI:    ${CYAN}http://localhost:${SERVICE_PORTS[open-webui]:-3000}${NC}"
    echo -e "  Workflows:  ${CYAN}http://localhost:${SERVICE_PORTS[n8n]:-5678}${NC}"
    echo -e "  API:        ${CYAN}http://localhost:${SERVICE_PORTS[llama-server]:-11434}/v1${NC}"
    
    echo ""
    echo -e "${DIM}Press Enter to return to menu...${NC}"
    read -r || return
}

# Main loop
while true; do
    clear_screen
    print_header
    print_menu

    read -r choice || break
    
    case "${choice,,}" in
        1) demo_chat ;;
        2) demo_voice ;;
        3) demo_rag ;;
        4) demo_code ;;
        5) show_status ;;
        q|quit|exit) 
            echo ""
            echo -e "${GREEN}Thanks for trying ODS! 🌙${NC}"
            echo ""
            exit 0
            ;;
        *)
            echo -e "${YELLOW}Invalid option${NC}"
            sleep 1
            ;;
    esac
done
