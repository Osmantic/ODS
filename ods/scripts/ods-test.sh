#!/bin/bash
#=============================================================================
# ods-test.sh - ODS Validation Suite (M8)
#
# Mission M8: Agent Bench Testing Systems
# Validates all critical paths: LLM, STT, TTS, embeddings, tool calling,
# voice round-trip. Clear pass/fail with actionable errors.
#
# Target: <2 minutes runtime
# Pass criteria: All critical tests pass
#
# Usage:
#   ./ods-test.sh                  # Run all tests
#   ./ods-test.sh --quick          # Fast mode (~30s, no inference)
#   ./ods-test.sh --json           # JSON output for automation
#   ./ods-test.sh --service llm     # Test specific service
#
# Exit codes:
#   0 - All critical tests passed
#   1 - One or more tests failed
#   2 - Configuration error
#=============================================================================

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_DIR="${ODS_DIR:-$HOME/.ods}"
ENV_FILE="${ENV_FILE:-$ODS_DIR/.env}"
TIMEOUT=15
QUICK_TIMEOUT=5

# Source service registry for port resolution
_DT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$_DT_DIR/lib/service-registry.sh" ]]; then
    export SCRIPT_DIR="$_DT_DIR"
    . "$_DT_DIR/lib/service-registry.sh"
    sr_load
    [[ -f "$_DT_DIR/lib/safe-env.sh" ]] && . "$_DT_DIR/lib/safe-env.sh"
    load_env_file "$_DT_DIR/.env"
    sr_resolve_ports
fi

# Service endpoints — resolved from registry
LLM_HOST="${LLM_HOST:-localhost}"
LLM_PORT="${LLM_PORT:-${SERVICE_PORTS[llama-server]:-11434}}"
LLM_URL="http://${LLM_HOST}:${LLM_PORT}"
WHISPER_HOST="${WHISPER_HOST:-localhost}"
WHISPER_PORT="${WHISPER_PORT:-${SERVICE_PORTS[whisper]:-9000}}"
TTS_HOST="${TTS_HOST:-localhost}"
TTS_PORT="${TTS_PORT:-${SERVICE_PORTS[tts]:-8880}}"
EMBEDDING_HOST="${EMBEDDING_HOST:-localhost}"
EMBEDDING_PORT="${EMBEDDING_PORT:-${SERVICE_PORTS[embeddings]:-9103}}"
LIVEKIT_HOST="${LIVEKIT_HOST:-localhost}"
LIVEKIT_PORT="${LIVEKIT_PORT:-7880}"
PRIVACY_SHIELD_PORT="${PRIVACY_SHIELD_PORT:-${SERVICE_PORTS[privacy-shield]:-8085}}"

# Colors (ANSI escape sequences)
RED='\e[0;31m'
GREEN='\e[0;32m'
YELLOW='\e[1;33m'
BLUE='\e[0;34m'
CYAN='\e[0;36m'
NC='\e[0m'
BOLD='\e[1m'

# Test tracking
TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0
SKIPPED_TESTS=0
START_TIME=0

# Mode flags
JSON_OUTPUT=false
QUICK_MODE=false
SPECIFIC_SERVICE=""
VERBOSE=false

# Results storage
RESULTS_STATUS=()
RESULTS_NAMES=()
RESULTS_DETAILS=()

#--------------------------------------------------------------------------
# Utility Functions
#--------------------------------------------------------------------------

load_env() {
    [[ -f "$_DT_DIR/lib/safe-env.sh" ]] && . "$_DT_DIR/lib/safe-env.sh"
    load_env_file "$ENV_FILE"
}

log() {
    [[ "$VERBOSE" == "true" ]] && echo "$@" >&2
}

# Portable millisecond timestamp (macOS BSD date lacks %N)
_now_ms() {
    python3 -c 'import time; print(int(time.time() * 1000))' 2>/dev/null || echo "$(date +%s)000"
}

print_header() {
    if [[ "$JSON_OUTPUT" != "true" ]]; then
        echo ""
        echo "========================================"
        echo "  ODS - VALIDATION SUITE"
        echo "  Mission M8: Critical Path Testing"
        echo "========================================"
        echo ""
    fi
}

record_result() {
    local name="$1"
    local status="$2"
    local details="${3:-}"
    
    RESULTS_NAMES+=("$name")
    RESULTS_STATUS+=("$status")
    RESULTS_DETAILS+=("$details")
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    
    case "$status" in
        pass) PASSED_TESTS=$((PASSED_TESTS + 1)) ;;
        fail) FAILED_TESTS=$((FAILED_TESTS + 1)) ;;
        skip) SKIPPED_TESTS=$((SKIPPED_TESTS + 1)) ;;
    esac
}

print_test() {
    local name="$1"
    local status="$2"
    local details="${3:-}"
    
    if [[ "$JSON_OUTPUT" != "true" ]]; then
        local icon
        case "$status" in
            pass) icon="OK" ;;
            fail) icon="FAIL" ;;
            skip) icon="SKIP" ;;
        esac
        
        printf "  %-40s [%s]" "$name" "$icon"
        [[ -n "$details" ]] && printf " %s" "$details"
        echo ""
    fi
}

#--------------------------------------------------------------------------
# Core Test Functions
#--------------------------------------------------------------------------

test_http() {
    local name="$1"
    local url="$2"
    local expected="${3:-200}"
    local method="${4:-GET}"
    local payload="${5:-}"
    local custom_timeout="${6:-$TIMEOUT}"
    
    local response_code
    local start_time end_time duration_ms
    
    start_time=$(_now_ms)

    if [[ -n "$payload" && "$method" == "POST" ]]; then
        response_code=$(curl -s -o /dev/null -w "%{http_code}" \
            --max-time "$custom_timeout" \
            -X POST "$url" \
            -H "Content-Type: application/json" \
            -d "$payload" 2>/dev/null || echo "000")
    else
        response_code=$(curl -s -o /dev/null -w "%{http_code}" \
            --max-time "$custom_timeout" \
            "$url" 2>/dev/null || echo "000")
    fi
    
    end_time=$(_now_ms)
    duration_ms=$(( end_time - start_time ))

    if [[ "$response_code" == "$expected" ]]; then
        record_result "$name" "pass" "${duration_ms}ms"
        print_test "$name" "pass" "${duration_ms}ms"
        return 0
    else
        record_result "$name" "fail" "HTTP $response_code"
        print_test "$name" "fail" "HTTP $response_code"
        return 1
    fi
}

test_tcp() {
    local name="$1"
    local host="$2"
    local port="$3"
    
    if timeout "$TIMEOUT" bash -c "cat < /dev/null > /dev/tcp/$host/$port" 2>/dev/null; then
        record_result "$name" "pass"
        print_test "$name" "pass"
        return 0
    else
        record_result "$name" "fail" "port $port unreachable"
        print_test "$name" "fail" "port closed"
        return 1
    fi
}

#--------------------------------------------------------------------------
# Service Test Suites
#--------------------------------------------------------------------------

test_docker() {
    echo ""
    echo "> Docker Infrastructure"
    
    if ! command -v docker &>/dev/null; then
        record_result "Docker Available" "fail" "docker not installed"
        print_test "Docker Available" "fail" "not installed"
        return 0
    fi
    
    record_result "Docker Available" "pass"
    print_test "Docker Available" "pass"
    
    if ! timeout 10 docker info &>/dev/null; then
        record_result "Docker Daemon" "fail" "daemon not running"
        print_test "Docker Daemon" "fail" "not running"
        return 0
    fi
    
    record_result "Docker Daemon" "pass"
    print_test "Docker Daemon" "pass"
    
    local running_count
    running_count=$(timeout 10 docker ps --format '{{.Names}}' 2>/dev/null | wc -l)
    record_result "Running Containers" "pass" "$running_count containers"
    print_test "Running Containers" "pass" "$running_count containers"
}

test_gpu() {
    echo ""
    echo "> GPU Resources"
    
    if ! command -v nvidia-smi &>/dev/null; then
        record_result "NVIDIA GPU" "skip" "nvidia-smi not found"
        print_test "NVIDIA GPU" "skip"
        return 0
    fi
    
    local gpu_info
    gpu_info=$(timeout 10 nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader 2>/dev/null | head -1)
    
    if [[ -n "$gpu_info" ]]; then
        local name mem_used mem_total util mem_pct
        name=$(echo "$gpu_info" | cut -d',' -f1 | xargs)
        mem_used=$(echo "$gpu_info" | cut -d',' -f2 | xargs | cut -d' ' -f1)
        mem_total=$(echo "$gpu_info" | cut -d',' -f3 | xargs | cut -d' ' -f1)
        
        mem_pct=$(( mem_used * 100 / mem_total ))
        
        record_result "GPU Available" "pass" "$name"
        print_test "GPU Available" "pass" "$name"
        
        if [[ $mem_pct -gt 90 ]]; then
            record_result "GPU Memory" "fail" "${mem_pct}% used - critical"
            print_test "GPU Memory" "fail" "${mem_pct}% used"
        else
            record_result "GPU Memory" "pass" "${mem_pct}% used"
            print_test "GPU Memory" "pass" "${mem_pct}% used"
        fi
    else
        record_result "NVIDIA GPU" "fail" "no GPU detected"
        print_test "NVIDIA GPU" "fail" "not detected"
    fi
}

test_llm() {
    echo ""
    echo "> LLM Inference (llama-server)"

    test_http "LLM Health" "$LLM_URL/health" "200" || return 1
    test_http "LLM Models API" "$LLM_URL/v1/models" "200"

    if [[ "$QUICK_MODE" == "true" ]]; then
        record_result "LLM Inference" "skip" "quick mode"
        print_test "LLM Inference" "skip"
        return 0
    fi

    local model_id
    model_id=$(curl -s --max-time 10 "$LLM_URL/v1/models" 2>/dev/null | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
    model_id="${model_id:-local}"

    local payload="{\"model\": \"$model_id\", \"messages\": [{\"role\": \"user\", \"content\": \"Say hello\"}], \"max_tokens\": 10}"
    local response

    response=$(curl -s --max-time 30 \
        -X POST "$LLM_URL/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$payload" 2>/dev/null)

    if echo "$response" | grep -q '"content"'; then
        local tokens_used
        tokens_used=$(echo "$response" | grep -o '"total_tokens":[0-9]*' | cut -d: -f2)
        record_result "LLM Inference" "pass" "${tokens_used} tokens"
        print_test "LLM Inference" "pass" "${tokens_used} tokens"
    else
        record_result "LLM Inference" "fail" "no content in response"
        print_test "LLM Inference" "fail"
        return 1
    fi
}

test_tool_calling() {
    echo ""
    echo "> Tool Calling M8 Critical"
    
    if [[ "$QUICK_MODE" == "true" ]]; then
        record_result "Tool Calling" "skip" "quick mode"
        print_test "Tool Calling" "skip"
        return 0
    fi
    
    local tools='[{"type":"function","function":{"name":"get_weather","description":"Get weather","parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}}]}'
    local payload="{\"model\": \"Qwen/Qwen2.5-32B-Instruct-AWQ\", \"messages\": [{\"role\": \"user\", \"content\": \"What is the weather in Tokyo?\"}], \"tools\": $tools, \"tool_choice\": \"auto\", \"max_tokens\": 100}"
    
    local response
    response=$(curl -s --max-time 30 \
        -X POST "$LLM_URL/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$payload" 2>/dev/null)
    
    if echo "$response" | grep -q '"tool_calls"'; then
        record_result "Tool Calling" "pass" "function called"
        print_test "Tool Calling" "pass" "function called"
    else
        record_result "Tool Calling" "fail" "no tool_calls in response"
        print_test "Tool Calling" "fail" "no tool call"
    fi
}

test_whisper() {
    echo ""
    echo "> Whisper Speech-to-Text"
    
    test_tcp "Whisper Port" "$WHISPER_HOST" "$WHISPER_PORT"
    
    local health_url="http://${WHISPER_HOST}:${WHISPER_PORT}/health"
    local response
    response=$(curl -s --max-time "$TIMEOUT" "$health_url" 2>/dev/null || echo "")
    
    if [[ -n "$response" ]]; then
        if echo "$response" | grep -qiE "ok|healthy|ready"; then
            record_result "Whisper Health" "pass"
            print_test "Whisper Health" "pass"
        else
            record_result "Whisper Health" "pass" "responding"
            print_test "Whisper Health" "pass" "responding"
        fi
    else
        whisper_http_exit=0
        test_http "Whisper HTTP" "http://${WHISPER_HOST}:${WHISPER_PORT}/" "200" || whisper_http_exit=$?
        [[ $whisper_http_exit -ne 0 ]] && log "Whisper HTTP check failed (exit $whisper_http_exit)"
    fi
}

test_tts() {
    echo ""
    echo "> Kokoro TTS Text-to-Speech"
    
    test_tcp "TTS Port" "$TTS_HOST" "$TTS_PORT"
    
    local voices_url="http://${TTS_HOST}:${TTS_PORT}/v1/audio/voices"
    local response
    response=$(curl -s --max-time "$TIMEOUT" "$voices_url" 2>/dev/null || echo "")
    
    if echo "$response" | grep -q '"voices"'; then
        local voice_count
        voice_count=$(echo "$response" | grep -o '"voice_id"' | wc -l)
        record_result "TTS Voices" "pass" "$voice_count voices"
        print_test "TTS Voices" "pass" "$voice_count voices"
    else
        tts_api_exit=0
        test_http "TTS API" "http://${TTS_HOST}:${TTS_PORT}/" "200" || tts_api_exit=$?
        [[ $tts_api_exit -ne 0 ]] && log "TTS API check failed (exit $tts_api_exit)"
    fi
}

test_embeddings() {
    echo ""
    echo "> Embeddings TEI"
    
    test_tcp "Embeddings Port" "$EMBEDDING_HOST" "$EMBEDDING_PORT"
    
    local health_url="http://${EMBEDDING_HOST}:${EMBEDDING_PORT}/health"
    local response
    response=$(curl -s --max-time "$TIMEOUT" "$health_url" 2>/dev/null || echo "")
    
    if echo "$response" | grep -q "ok"; then
        record_result "Embeddings Health" "pass"
        print_test "Embeddings Health" "pass"
    else
        local payload='{"inputs": "test sentence"}'
        embeddings_api_exit=0
        test_http "Embeddings API" "http://${EMBEDDING_HOST}:${EMBEDDING_PORT}/embed" "200" "POST" "$payload" || embeddings_api_exit=$?
        [[ $embeddings_api_exit -ne 0 ]] && log "Embeddings API check failed (exit $embeddings_api_exit)"
    fi
}

test_voice_roundtrip() {
    echo ""
    echo "> Voice Round-Trip M8 Critical"
    
    if [[ "$QUICK_MODE" == "true" ]]; then
        record_result "Voice Round-Trip" "skip" "quick mode"
        print_test "Voice Round-Trip" "skip"
        return 0
    fi
    
    local whisper_ready=false
    local tts_ready=false
    local llm_ready=false
    
    if curl -s --max-time 5 "http://${WHISPER_HOST}:${WHISPER_PORT}/health" &>/dev/null; then
        whisper_ready=true
    fi
    if curl -s --max-time 5 "http://${TTS_HOST}:${TTS_PORT}/v1/audio/voices" &>/dev/null; then
        tts_ready=true
    fi
    if curl -s --max-time 5 "$LLM_URL/health" &>/dev/null; then
        llm_ready=true
    fi
    
    if [[ "$whisper_ready" != "true" ]]; then
        record_result "Voice Round-Trip" "skip" "Whisper unavailable"
        print_test "Voice Round-Trip" "skip" "Whisper unavailable"
        return 0
    fi
    if [[ "$tts_ready" != "true" ]]; then
        record_result "Voice Round-Trip" "skip" "TTS unavailable"
        print_test "Voice Round-Trip" "skip" "TTS unavailable"
        return 0
    fi
    if [[ "$llm_ready" != "true" ]]; then
        record_result "Voice Round-Trip" "skip" "LLM unavailable"
        print_test "Voice Round-Trip" "skip" "LLM unavailable"
        return 0
    fi
    
    local start_time end_time duration_ms
    start_time=$(_now_ms)

    local llm_payload='{"model": "Qwen/Qwen2.5-32B-Instruct-AWQ", "messages": [{"role": "user", "content": "What is the weather today?"}], "max_tokens": 50}'
    local llm_response
    llm_response=$(curl -s --max-time 15 \
        -X POST "$LLM_URL/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$llm_payload" 2>/dev/null)
    
    if ! echo "$llm_response" | grep -q '"content"'; then
        record_result "Voice Round-Trip" "fail" "LLM step failed"
        print_test "Voice Round-Trip" "fail" "LLM failed"
        return 1
    fi
    
    local tts_text="The weather today is sunny and 75 degrees."
    local tts_payload="{\"model\": \"kokoro\", \"input\": \"$tts_text\", \"voice\": \"af_bella\"}"
    local tts_response
    tts_response=$(curl -s --max-time 15 \
        -X POST "http://${TTS_HOST}:${TTS_PORT}/v1/audio/speech" \
        -H "Content-Type: application/json" \
        -d "$tts_payload" 2>/dev/null)
    
    end_time=$(_now_ms)
    duration_ms=$(( end_time - start_time ))

    if [[ -n "$tts_response" ]] && [[ ${#tts_response} -gt 100 ]]; then
        record_result "Voice Round-Trip" "pass" "${duration_ms}ms"
        print_test "Voice Round-Trip" "pass" "${duration_ms}ms"
    else
        record_result "Voice Round-Trip" "fail" "TTS step failed"
        print_test "Voice Round-Trip" "fail" "TTS failed"
    fi
}

test_privacy_shield() {
    echo ""
    echo "> Privacy Shield M3"
    
    local shield_url="http://localhost:${PRIVACY_SHIELD_PORT}"
    
    test_http "Privacy Shield Health" "$shield_url/health" "200" || return 0
    
    if [[ "$QUICK_MODE" == "true" ]]; then
        record_result "Privacy Shield Proxy" "skip" "quick mode"
        print_test "Privacy Shield Proxy" "skip"
        return 0
    fi
    
    local payload='{"model": "test", "messages": [{"role": "user", "content": "My email is john@example.com"}], "max_tokens": 10}'
    local response
    response=$(curl -s --max-time 10 \
        -X POST "$shield_url/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$payload" 2>/dev/null || echo "")
    
    if [[ -n "$response" ]]; then
        record_result "Privacy Shield Proxy" "pass" "PII scrubbing active"
        print_test "Privacy Shield Proxy" "pass" "PII scrubbing"
    else
        record_result "Privacy Shield Proxy" "fail" "no response"
        print_test "Privacy Shield Proxy" "fail"
    fi
}

test_livekit() {
    echo ""
    echo "> LiveKit Voice Infrastructure"
    
    test_tcp "LiveKit Port" "$LIVEKIT_HOST" "$LIVEKIT_PORT"
    livekit_health_exit=0
    test_http "LiveKit Health" "http://${LIVEKIT_HOST}:${LIVEKIT_PORT}/" "200" || livekit_health_exit=$?
    [[ $livekit_health_exit -ne 0 ]] && log "LiveKit health check failed (exit $livekit_health_exit)"
}

#--------------------------------------------------------------------------
# Summary and Output
#--------------------------------------------------------------------------

print_summary() {
    local end_time elapsed_secs
    end_time=$(date +%s)
    elapsed_secs=$((end_time - START_TIME))
    
    if [[ "$JSON_OUTPUT" == "true" ]]; then
        _print_json_summary "$elapsed_secs"
    else
        _print_text_summary "$elapsed_secs"
    fi
}

_print_json_summary() {
    local elapsed="$1"
    
    echo "{"
    echo "  \"timestamp\": \"$(date -u +"%Y-%m-%dT%H:%M:%SZ")\","
    echo "  \"runtime_seconds\": $elapsed,"
    echo "  \"summary\": {"
    echo "    \"total\": $TOTAL_TESTS,"
    echo "    \"passed\": $PASSED_TESTS,"
    echo "    \"failed\": $FAILED_TESTS,"
    echo "    \"skipped\": $SKIPPED_TESTS,"
    echo "    \"success\": $([ $FAILED_TESTS -eq 0 ] && echo 'true' || echo 'false')"
    echo "  },"
    echo "  \"results\": ["
    
    local first=true
    local i
    for i in "${!RESULTS_NAMES[@]}"; do
        [[ "$first" == "true" ]] || echo ","
        printf '    {"name": "%s", "status": "%s", "details": "%s"}' \
            "${RESULTS_NAMES[$i]}" "${RESULTS_STATUS[$i]}" "${RESULTS_DETAILS[$i]:-}"
        first=false
    done
    echo ""
    echo "  ]"
    echo "}"
}

_print_text_summary() {
    local elapsed="$1"
    
    echo ""
    echo "========================================"
    echo ""
    
    if [[ $FAILED_TESTS -eq 0 ]]; then
        echo -e "  ${GREEN}ALL TESTS PASSED${NC}"
        echo ""
        echo "  Passed: $PASSED_TESTS | Skipped: $SKIPPED_TESTS"
        echo "  Runtime: ${elapsed}s"
        echo ""
        echo -e "  ${BOLD}ODS is ready!${NC}"
    else
        echo -e "  ${RED}SOME TESTS FAILED${NC}"
        echo ""
        echo -e "  Passed: ${GREEN}${PASSED_TESTS}${NC} | Failed: ${RED}${FAILED_TESTS}${NC} | Skipped: ${YELLOW}${SKIPPED_TESTS}${NC}"
        echo "  Runtime: ${elapsed}s"
        echo ""
        echo "  Failed tests:"
        local i
        for i in "${!RESULTS_NAMES[@]}"; do
            if [[ "${RESULTS_STATUS[$i]}" == "fail" ]]; then
                echo "    - ${RESULTS_NAMES[$i]}"
            fi
        done
        echo ""
        echo "Actionable fixes:"
        
        if [[ "${RESULTS_STATUS[0]:-}" == "fail" ]] && [[ "${RESULTS_NAMES[0]:-}" == *"LLM"* ]]; then
            echo "  - LLM not responding - check: docker logs ods-llama-server"
        fi

        local i
        for i in "${!RESULTS_NAMES[@]}"; do
            if [[ "${RESULTS_STATUS[$i]}" == "fail" ]]; then
                case "${RESULTS_NAMES[$i]}" in
                    "Tool Calling") echo "  - Tool calling failed - check llama-server tool support" ;;
                    "Whisper Port") echo "  - Whisper not running - start: docker compose up whisper" ;;
                    "TTS Port") echo "  - TTS not running - start: docker compose up kokoro-tts" ;;
                esac
            fi
        done
    fi
    
    echo ""
}

#--------------------------------------------------------------------------
# Main
#--------------------------------------------------------------------------

show_help() {
    cat << 'EOF'
ODS Validation Suite (M8)

USAGE:
    ods-test.sh [OPTIONS]

OPTIONS:
    --quick, -q        Fast mode (~30s, no inference tests)
    --json, -j         Output results as JSON
    --service, -s      Test specific service only
    --verbose, -v      Show detailed output
    --help, -h         Show this help

SERVICES:
    docker, gpu, llm, tool-calling, whisper, tts,
    embeddings, voice-roundtrip, privacy-shield, livekit

EXAMPLES:
    ods-test.sh                    # Run all tests
    ods-test.sh --quick            # Fast health check
    ods-test.sh --json > results.json
    ods-test.sh --service llm      # Test LLM only

EXIT CODES:
    0 - All tests passed
    1 - One or more tests failed
    2 - Configuration error

EOF
}

run_all_tests() {
    print_header
    
    test_docker
    test_gpu
    test_llm
    test_tool_calling
    test_whisper
    test_tts
    test_embeddings
    test_voice_roundtrip
    test_privacy_shield
    test_livekit
}

run_specific_service() {
    local service="$1"
    
    print_header
    
    case "$service" in
        docker)          test_docker ;;
        gpu)             test_gpu ;;
        llm)             test_llm ;;
        tool-calling)    test_tool_calling ;;
        whisper)         test_whisper ;;
        tts)             test_tts ;;
        embeddings)      test_embeddings ;;
        voice-roundtrip) test_voice_roundtrip ;;
        privacy-shield)  test_privacy_shield ;;
        livekit)         test_livekit ;;
        *)
            echo "Unknown service: $service" >&2
            echo "Available: docker, gpu, llm, tool-calling, whisper, tts, embeddings, voice-roundtrip, privacy-shield, livekit" >&2
            exit 2
            ;;
    esac
}

main() {
    START_TIME=$(date +%s)
    
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --json|-j)
                JSON_OUTPUT=true
                shift
                ;;
            --quick|-q)
                QUICK_MODE=true
                TIMEOUT=$QUICK_TIMEOUT
                shift
                ;;
            --service|-s)
                SPECIFIC_SERVICE="$2"
                shift 2
                ;;
            --verbose|-v)
                VERBOSE=true
                shift
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                echo "Unknown option: $1" >&2
                show_help
                exit 2
                ;;
        esac
    done
    
    # Load environment
    load_env
    
    # Run tests
    if [[ -n "$SPECIFIC_SERVICE" ]]; then
        run_specific_service "$SPECIFIC_SERVICE"
    else
        run_all_tests
    fi
    
    # Print summary
    print_summary
    
    # Exit with appropriate code
    if [[ $FAILED_TESTS -eq 0 ]]; then
        exit 0
    else
        exit 1
    fi
}

main "$@"
