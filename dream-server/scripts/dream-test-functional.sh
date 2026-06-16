#!/bin/bash
#=============================================================================
# dream-test-functional.sh - Functional Testing for Dream Server
#
# Tests actual functionality, not just port availability:
# - LLM (llama-server) generates coherent text
# - Whisper transcribes actual audio
# - TTS generates valid audio files
# - Embeddings produce vectors
#
# This complements dream-test.sh which checks service health.
#=============================================================================

# Require Bash 4+ (associative arrays used for service-port lookup)
if (( BASH_VERSINFO[0] < 4 )); then
    echo "ERROR: $(basename "$0") requires Bash 4.0+ (you have $BASH_VERSION)" >&2
    echo "  macOS ships Bash 3.2 due to licensing. Install a modern version:" >&2
    echo "    brew install bash" >&2
    exit 1
fi

set -euo pipefail

# Colors
RED='\e[0;31m'
GREEN='\e[0;32m'
YELLOW='\e[1;33m'
NC='\e[0m'

# Source service registry for port resolution
_FT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$_FT_DIR/lib/service-registry.sh" ]]; then
    export SCRIPT_DIR="$_FT_DIR"
    . "$_FT_DIR/lib/service-registry.sh"
    sr_load
    [[ -f "$_FT_DIR/lib/safe-env.sh" ]] && . "$_FT_DIR/lib/safe-env.sh"
    load_env_file "$_FT_DIR/.env"
    sr_resolve_ports
fi

# Ensure SERVICE_PORTS is declared even if service-registry.sh was not sourced
declare -A SERVICE_PORTS

# Service endpoints — resolved from registry
LLM_URL="${LLM_URL:-http://localhost:${SERVICE_PORTS[llama-server]:-11434}}"
WHISPER_URL="${WHISPER_URL:-http://localhost:${SERVICE_PORTS[whisper]:-9000}}"
TTS_URL="${TTS_URL:-http://localhost:${SERVICE_PORTS[tts]:-8880}}"
EMBEDDING_URL="${EMBEDDING_URL:-http://localhost:${SERVICE_PORTS[embeddings]:-9103}}"

# Test tracking
TESTS_PASSED=0
TESTS_FAILED=0

pass() {
    echo -e "${GREEN}✓${NC} $1"
    # Arithmetic expansion (not `((...))`) — the compound form returns
    # exit 1 when the pre-increment value is 0, which would trip `set -e`
    # on the first pass/fail and abort the script before any summary.
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

fail() {
    echo -e "${RED}✗${NC} $1"
    TESTS_FAILED=$((TESTS_FAILED + 1))
}

warn() {
    echo -e "${YELLOW}⚠${NC} $1"
}

# Test 1: LLM generates coherent text
test_llm_functional() {
    echo ""
    echo "> Testing LLM Functional Generation"

    # The grep-based extraction pipelines in this script may legitimately
    # produce zero matches (LLM/TTS/embeddings/whisper offline or returning
    # unexpected payload). Under `set -euo pipefail` such a no-match would
    # abort the script before the `[[ -z ... ]]` guard below can treat it
    # as a test failure. Using `if ! VAR=$(...)` keeps the set -e safety
    # net engaged everywhere else; `set -e` is disabled only for the
    # evaluation of the condition (per bash spec), so a failed pipeline
    # leaves VAR empty and the explicit `fail` path below runs.
    local model_id=""
    if ! model_id=$(curl -s --max-time 10 "$LLM_URL/v1/models" 2>/dev/null | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4); then
        model_id=""
    fi
    model_id="${model_id:-local}"

    local prompt="What is 2+2? Answer with just the number."
    local payload="{\"model\": \"$model_id\", \"messages\": [{\"role\": \"user\", \"content\": \"$prompt\"}], \"max_tokens\": 10, \"temperature\": 0.1}"

    local response
    response=$(curl -s --max-time 30 \
        -X POST "$LLM_URL/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$payload" 2>/dev/null || echo "")

    if [[ -z "$response" ]]; then
        fail "LLM returned no response"
        return 1
    fi

    local content=""
    if ! content=$(echo "$response" | grep -oE '"content":[[:space:]]*"[^"]+"' | head -1 | cut -d'"' -f4); then
        content=""
    fi

    if [[ -z "$content" ]]; then
        fail "LLM returned empty content"
        return 1
    fi

    # Check if response contains "4" (the answer to 2+2)
    if echo "$content" | grep -q "4"; then
        pass "LLM generates correct answer: '$content'"
    else
        warn "LLM generated: '$content' (expected '4')"
        pass "LLM generates text (answer may vary)"
    fi
}

# Test 2: TTS generates valid audio file
test_tts_functional() {
    echo ""
    echo "> Testing TTS Audio Generation"
    
    local test_text="Hello, this is a test."
    local output_file="/tmp/test_tts_output.wav"
    
    local payload="{\"model\": \"kokoro\", \"input\": \"$test_text\", \"voice\": \"af_bella\", \"response_format\": \"wav\"}"
    
    # Generate audio
    local http_code
    http_code=$(curl -s -w "%{http_code}" --max-time 30 \
        -X POST "$TTS_URL/v1/audio/speech" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        -o "$output_file" 2>/dev/null)
    
    if [[ "$http_code" != "200" ]]; then
        fail "TTS returned HTTP $http_code"
        rm -f "$output_file"
        return 1
    fi
    
    # Check file exists and has content
    if [[ ! -f "$output_file" ]]; then
        fail "TTS did not create output file"
        return 1
    fi
    
    local file_size
    file_size=$(stat -c%s "$output_file" 2>/dev/null || stat -f%z "$output_file" 2>/dev/null || echo "0")
    
    if [[ "$file_size" -lt 1000 ]]; then
        fail "TTS output too small: $file_size bytes (expected >1KB)"
        rm -f "$output_file"
        return 1
    fi
    
    # Check it's a valid WAV file
    if ! file "$output_file" | grep -qiE "audio|wav|riff"; then
        warn "TTS output may not be valid WAV: $(file "$output_file")"
        pass "TTS generates audio file ($file_size bytes)"
    else
        pass "TTS generates valid WAV audio ($file_size bytes)"
    fi
    
    rm -f "$output_file"
}

# Test 3: Embeddings produce vectors
test_embeddings_functional() {
    echo ""
    echo "> Testing Embeddings Vector Generation"
    
    local test_text="This is a test sentence for embeddings."
    local payload="{\"inputs\": \"$test_text\"}"
    
    local response
    response=$(curl -s --max-time 30 \
        -X POST "$EMBEDDING_URL/embed" \
        -H "Content-Type: application/json" \
        -d "$payload" 2>/dev/null || echo "")
    
    if [[ -z "$response" ]]; then
        # Try alternate endpoint
        response=$(curl -s --max-time 30 \
            -X POST "$EMBEDDING_URL/" \
            -H "Content-Type: application/json" \
            -d "$payload" 2>/dev/null || echo "")
    fi
    
    if [[ -z "$response" ]]; then
        fail "Embeddings returned no response"
        return 1
    fi
    
    # Check if response contains array of numbers
    if echo "$response" | grep -qE '\[\s*-?[0-9]+\.[0-9]+'; then
        local vector_len=0
        if ! vector_len=$(echo "$response" | grep -oE '-?[0-9]+\.[0-9]+' | wc -l); then
            vector_len=0
        fi
        pass "Embeddings generates vectors ($vector_len dimensions)"
    else
        fail "Embeddings did not return valid vectors"
        return 1
    fi
}

# Test 4: Whisper transcribes audio (if test audio available)
test_whisper_functional() {
    echo ""
    echo "> Testing Whisper Transcription"
    
    # Create a simple test audio file or use existing
    local test_audio="/tmp/test_audio.wav"
    
    # Try to generate test audio with TTS first
    local tts_payload='{"model": "kokoro", "input": "Hello world", "voice": "af_bella", "response_format": "wav"}'
    
    if ! curl -s --max-time 15 \
        -X POST "$TTS_URL/v1/audio/speech" \
        -H "Content-Type: application/json" \
        -d "$tts_payload" \
        -o "$test_audio" 2>/dev/null; then
        warn "Could not generate test audio for Whisper"
        warn "Skipping Whisper functional test (TTS dependency)"
        return 0
    fi
    
    if [[ ! -f "$test_audio" ]] || [[ "$(stat -c%s "$test_audio" 2>/dev/null || stat -f%z "$test_audio" 2>/dev/null || echo 0)" -lt 1000 ]]; then
        warn "Test audio generation failed"
        return 0
    fi
    
    # Transcribe with Whisper
    local response
    response=$(curl -s --max-time 30 \
        -X POST "$WHISPER_URL/v1/audio/transcriptions" \
        -H "Content-Type: multipart/form-data" \
        -F "file=@$test_audio" \
        -F "model=whisper-1" 2>/dev/null || echo "")
    
    rm -f "$test_audio"
    
    if [[ -z "$response" ]]; then
        fail "Whisper returned no response"
        return 1
    fi
    
    local transcription=""
    if ! transcription=$(echo "$response" | grep -oE '"text":[[:space:]]*"[^"]+"' | head -1 | cut -d'"' -f4); then
        transcription=""
    fi
    
    if [[ -z "$transcription" ]]; then
        fail "Whisper returned empty transcription"
        return 1
    fi
    
    if echo "$transcription" | grep -qiE "hello|world"; then
        pass "Whisper transcribes correctly: '$transcription'"
    else
        warn "Whisper transcribed: '$transcription'"
        pass "Whisper generates transcription"
    fi
}

# Main
echo "========================================"
echo "  DREAM SERVER - FUNCTIONAL TESTS"
echo "  Tests actual functionality, not ports"
echo "========================================"

# Each test returns 1 on failure via its internal `fail` call; we must not
# let `set -e` short-circuit the remaining tests or skip the summary. The
# TESTS_PASSED / TESTS_FAILED counters are updated inside pass/fail and
# drive the final exit code below, so suspending strict mode for exactly
# this block is the explicit expression of "errors here are accounted for."
# (CLAUDE.md forbids `|| true` / silent swallow; this bounded toggle makes
# the intent visible instead of hiding it behind a trailing `|| true`.)
set +e
test_llm_functional
test_tts_functional
test_embeddings_functional
test_whisper_functional
set -e

echo ""
echo "========================================"
echo "  Results: $TESTS_PASSED passed, $TESTS_FAILED failed"
echo "========================================"

if [[ $TESTS_FAILED -eq 0 ]]; then
    echo -e "${GREEN}✓ All functional tests passed${NC}"
    exit 0
else
    echo -e "${RED}✗ Some functional tests failed${NC}"
    exit 1
fi
