#!/bin/bash
# ODS Integration Test Suite
# Validates all services are working end-to-end
#
# Usage: ./tests/test-integration.sh [--verbose] [--quick]
#
# Auth: dashboard-api routes under /api/*, /status, /gpu, /services now require
# a Bearer token (see security.py). This script resolves the API key from, in
# order: $DASHBOARD_API_KEY, the ODS install .env, or falls back to skipping
# authenticated checks with a clear warning. Never invents a key.
#
# Ports: honor env-var overrides from .env so the checks track whatever ports
# the installer actually wrote for this host (e.g. Strix Halo AMD writes
# WHISPER_PORT=9100 to avoid Lemonade's port 9000).

# Note: Intentionally NOT using set -e here — test functions return 1 on failure
# and we want to continue running all tests, tracking results via PASSED/FAILED counters
set -uo pipefail

# Colors (must be defined before any log_* call, including the jq check below)
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Check for required dependencies
command -v jq >/dev/null 2>&1 || { echo -e "${RED}✗${NC} jq is required but not installed. Install with: apt-get install jq (Debian/Ubuntu) or brew install jq (macOS)"; exit 1; }

# Config
VERBOSE=${VERBOSE:-false}
QUICK=${QUICK:-false}
TIMEOUT=10
PASSED=0
FAILED=0
SKIPPED=0

# Parse args
for arg in "$@"; do
    case $arg in
        --verbose|-v) VERBOSE=true ;;
        --quick|-q) QUICK=true ;;
        --help|-h)
            echo "Usage: $0 [--verbose] [--quick]"
            echo "  --verbose  Show detailed output"
            echo "  --quick    Skip slow tests"
            exit 0
            ;;
    esac
done

# Logging
log_info() { echo -e "${BLUE}ℹ${NC} $1"; }
log_pass() { echo -e "${GREEN}✓${NC} $1"; ((PASSED++)); }
log_fail() { echo -e "${RED}✗${NC} $1"; ((FAILED++)); }
log_skip() { echo -e "${YELLOW}○${NC} $1 (skipped)"; ((SKIPPED++)); }
log_verbose() { $VERBOSE && echo -e "  ${NC}$1" || true; }

# ─── Resolve host, ports, and API key from installer .env ────────────────────
# Shared helper handles: shell-env-wins precedence, CRLF scrubbing, inline
# comment / quote stripping, and default fallbacks. Also populates the
# AE_AUTH_HEADER array we splat into curl.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/auth-env.sh
. "$SCRIPT_DIR/lib/auth-env.sh"

ODS_ROOT="${ODS_INSTALL_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"

# Load .env safely for the extra port env vars (WHISPER_PORT, TTS_PORT, …)
# that aren't part of the auth helper's remit. Capture shell overrides first
# so they still win over .env (load_env_file exports unconditionally).
_shell_DASHBOARD_PORT="${DASHBOARD_PORT:-}"
_shell_WHISPER_PORT="${WHISPER_PORT:-}"
_shell_TTS_PORT="${TTS_PORT:-}"
_shell_N8N_PORT="${N8N_PORT:-}"
_shell_QDRANT_PORT="${QDRANT_PORT:-}"
_shell_LIVEKIT_PORT="${LIVEKIT_PORT:-}"
_shell_OLLAMA_PORT="${OLLAMA_PORT:-}"
_shell_LLAMA_SERVER_PORT="${LLAMA_SERVER_PORT:-}"

if [[ -f "$ODS_ROOT/lib/safe-env.sh" ]]; then
    # shellcheck source=/dev/null
    . "$ODS_ROOT/lib/safe-env.sh"
    load_env_file "$ODS_ROOT/.env"
fi

[[ -n "$_shell_DASHBOARD_PORT"      ]] && DASHBOARD_PORT="$_shell_DASHBOARD_PORT"
[[ -n "$_shell_WHISPER_PORT"        ]] && WHISPER_PORT="$_shell_WHISPER_PORT"
[[ -n "$_shell_TTS_PORT"            ]] && TTS_PORT="$_shell_TTS_PORT"
[[ -n "$_shell_N8N_PORT"            ]] && N8N_PORT="$_shell_N8N_PORT"
[[ -n "$_shell_QDRANT_PORT"         ]] && QDRANT_PORT="$_shell_QDRANT_PORT"
[[ -n "$_shell_LIVEKIT_PORT"        ]] && LIVEKIT_PORT="$_shell_LIVEKIT_PORT"
[[ -n "$_shell_OLLAMA_PORT"         ]] && OLLAMA_PORT="$_shell_OLLAMA_PORT"
[[ -n "$_shell_LLAMA_SERVER_PORT"   ]] && LLAMA_SERVER_PORT="$_shell_LLAMA_SERVER_PORT"

# CRLF scrub every port variable so a Windows-edited .env doesn't leak `\r`
# into URLs (silent curl failure).
for _p in DASHBOARD_PORT WHISPER_PORT TTS_PORT N8N_PORT QDRANT_PORT \
          LIVEKIT_PORT OLLAMA_PORT LLAMA_SERVER_PORT; do
    _v="${!_p:-}"; printf -v "$_p" '%s' "${_v%$'\r'}"
done
unset _p _v

# Resolve API key + port + host + AE_AUTH_HEADER via the shared helper.
ae_resolve "$SCRIPT_DIR"

# Ports — canonical manifest defaults from ods/extensions/services/*/manifest.yaml.
DASHBOARD_PORT="${DASHBOARD_PORT:-3001}"
WHISPER_PORT="${WHISPER_PORT:-9000}"
TTS_PORT="${TTS_PORT:-8880}"
N8N_PORT="${N8N_PORT:-5678}"
QDRANT_PORT="${QDRANT_PORT:-6333}"
LIVEKIT_PORT="${LIVEKIT_PORT:-7880}"
# llama-server: OLLAMA_PORT (installer alias) → LLAMA_SERVER_PORT → 8080.
# 8080 is the canonical default (docker-compose.base.yml); 11434 is only
# the Strix Halo AMD override written by installer phase 06 into .env.
LLM_PORT="${OLLAMA_PORT:-${LLAMA_SERVER_PORT:-8080}}"

API_BASE="$ae_api_base"

# Back-compat aliases so the rest of the script reads cleanly.
API_KEY="$DASHBOARD_API_KEY"
if ae_key_available; then AUTH_AVAILABLE=true; else AUTH_AVAILABLE=false; fi
# Historical name kept for local grep. AE_AUTH_HEADER is the canonical array.
declare -a AUTH_HEADER_ARGS
if $AUTH_AVAILABLE; then
    AUTH_HEADER_ARGS=("${AE_AUTH_HEADER[@]}")
else
    AUTH_HEADER_ARGS=()
fi

test_http() {
    local name="$1"
    local url="$2"
    local expected="${3:-200}"
    local method="${4:-GET}"
    local data="${5:-}"

    local args=(-s -o /dev/null -w "%{http_code}" --max-time "$TIMEOUT")
    [[ -n "$data" ]] && args+=(-X "$method" -H "Content-Type: application/json" -d "$data")

    local code
    code=$(curl "${args[@]}" "$url" 2>/dev/null) || code="000"

    if [[ "$code" == "$expected" ]]; then
        log_pass "$name"
        return 0
    else
        log_fail "$name (expected $expected, got $code)"
        return 1
    fi
}

# Like test_http but treats connection failure (000) as SKIP rather than
# FAIL — the service is optional / not deployed. Also stops the shell from
# double-counting via the old `test_http ... || log_skip` pattern which fired
# both log_fail and log_skip on the same check.
test_http_optional() {
    local name="$1"
    local url="$2"
    local expected="${3:-200}"

    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$TIMEOUT" "$url" 2>/dev/null) || code="000"

    if [[ "$code" == "$expected" ]]; then
        log_pass "$name"
        return 0
    elif [[ "$code" == "000" ]]; then
        log_skip "$name (service not running)"
        return 0
    else
        log_fail "$name (expected $expected, got $code)"
        return 1
    fi
}

test_json() {
    local name="$1"
    local url="$2"
    local jq_filter="$3"

    local response
    response=$(curl -s --max-time $TIMEOUT "$url" 2>/dev/null) || response=""

    if echo "$response" | jq -e "$jq_filter" >/dev/null 2>&1; then
        log_pass "$name"
        local summary
        summary=$(echo "$response" | jq -c '.' 2>/dev/null || echo "$response")
        log_verbose "Response: ${summary:0:100}"
        return 0
    else
        log_fail "$name (jq filter failed: $jq_filter)"
        log_verbose "Response: ${response:0:100}"
        return 1
    fi
}

# Auth-aware variant. Skips (does not fail) when no API key is available,
# so a dev running this without an install .env doesn't see spurious FAIL
# lines for endpoints that are behaving correctly but locked.
test_json_auth() {
    local name="$1"
    local url="$2"
    local jq_filter="$3"

    if ! $AUTH_AVAILABLE; then
        log_skip "$name (no DASHBOARD_API_KEY)"
        return 0
    fi

    local response
    response=$(curl -s --max-time "$TIMEOUT" "${AUTH_HEADER_ARGS[@]}" "$url" 2>/dev/null) || response=""

    if echo "$response" | jq -e "$jq_filter" >/dev/null 2>&1; then
        log_pass "$name"
        local summary
        summary=$(echo "$response" | jq -c '.' 2>/dev/null || echo "$response")
        log_verbose "Response: ${summary:0:100}"
        return 0
    else
        log_fail "$name (jq filter failed: $jq_filter)"
        log_verbose "Response: ${response:0:100}"
        return 1
    fi
}

test_llm() {
    local name="$1"
    local url="$2"
    local prompt="$3"

    local data
    data=$(jq -n --arg prompt "$prompt" '{
        model: "qwen2.5-32b-instruct",
        messages: [{role: "user", content: $prompt}],
        max_tokens: 50,
        stream: false
    }')

    local response
    response=$(curl -s --max-time 30 -X POST \
        -H "Content-Type: application/json" \
        -d "$data" \
        "$url/v1/chat/completions" 2>/dev/null) || response=""

    if echo "$response" | jq -e '.choices[0].message.content' >/dev/null 2>&1; then
        local content
        content=$(echo "$response" | jq -r '.choices[0].message.content' | head -c 100)
        log_pass "$name"
        log_verbose "Response: $content"
        return 0
    else
        log_fail "$name (no valid response)"
        log_verbose "Response: $response"
        return 1
    fi
}

# Header
echo ""
echo -e "${BLUE}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║${NC}  ODS Integration Tests                              ${BLUE}║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

if ! $AUTH_AVAILABLE; then
    log_info "DASHBOARD_API_KEY not found (checked \$DASHBOARD_API_KEY and $ENV_FILE)."
    log_info "Authenticated /api/* checks will be skipped — set the env var or run after install."
    echo ""
fi

# ========================================
# Dashboard API Tests
# ========================================
echo -e "${BLUE}▸ Dashboard API${NC}"

test_http "API health check" "$API_BASE/health"
test_json_auth "API status endpoint" "$API_BASE/api/status" '(.gpu != null) or ((.services // []) | length > 0)'
# GPU may legitimately be absent on CPU-only installs (503) — treat as skip.
# Single fetch with -w emits body then '\n<code>' so we can classify + reuse
# without hitting the endpoint twice.
if $AUTH_AVAILABLE; then
    _gpu_resp=$(curl -s -w '\n%{http_code}' --max-time "$TIMEOUT" \
        "${AUTH_HEADER_ARGS[@]}" "$API_BASE/gpu" 2>/dev/null || printf '\n000')
    _gpu_code="${_gpu_resp##*$'\n'}"
    _gpu_body="${_gpu_resp%$'\n'*}"
    if [[ "$_gpu_code" == "503" ]]; then
        log_skip "GPU metrics (CPU-only install — /gpu returned 503)"
    elif [[ "$_gpu_code" == "200" ]] && \
         echo "$_gpu_body" | jq -e '.name and (.memory_used_mb != null)' >/dev/null 2>&1; then
        log_pass "GPU metrics"
    else
        log_fail "GPU metrics (http=$_gpu_code, jq filter: .name and .memory_used_mb)"
        log_verbose "Response: ${_gpu_body:0:100}"
    fi
    unset _gpu_resp _gpu_code _gpu_body
else
    log_skip "GPU metrics (no DASHBOARD_API_KEY)"
fi
test_json_auth "Service list" "$API_BASE/services" '. | length > 0'

# ========================================
# Model API Tests
# ========================================
echo ""
echo -e "${BLUE}▸ Model Manager API${NC}"

# Fetch /api/models once and drive both the catalog + VRAM checks from it —
# saves a redundant round-trip and eliminates the race window between them.
if $AUTH_AVAILABLE; then
    _models_body=$(curl -s --max-time "$TIMEOUT" "${AUTH_HEADER_ARGS[@]}" "$API_BASE/api/models" 2>/dev/null || echo '')
    if echo "$_models_body" | jq -e '.models | length > 0' >/dev/null 2>&1; then
        log_pass "Model catalog"
        log_verbose "Response: ${_models_body:0:100}"
    else
        log_fail "Model catalog (jq filter failed: .models | length > 0)"
        log_verbose "Response: ${_models_body:0:100}"
    fi
    if echo "$_models_body" | jq -e '.gpu == null' >/dev/null 2>&1; then
        log_skip "VRAM info in catalog (CPU-only install — .gpu is null)"
    elif echo "$_models_body" | jq -e '.gpu.vramTotal > 0' >/dev/null 2>&1; then
        log_pass "VRAM info in catalog"
    else
        log_fail "VRAM info in catalog (jq filter failed: .gpu.vramTotal > 0)"
    fi
    unset _models_body
else
    log_skip "Model catalog (no DASHBOARD_API_KEY)"
    log_skip "VRAM info in catalog (no DASHBOARD_API_KEY)"
fi

# ========================================
# Workflow API Tests
# ========================================
echo ""
echo -e "${BLUE}▸ Workflow API${NC}"

test_json_auth "Workflow catalog" "$API_BASE/api/workflows" '.workflows | length > 0'
test_json_auth "Workflow categories" "$API_BASE/api/workflows" '.categories | keys | length > 0'

# ========================================
# Voice API Tests
# ========================================
echo ""
echo -e "${BLUE}▸ Voice API${NC}"

test_json_auth "Voice status" "$API_BASE/api/voice/status" '.services'

# ========================================
# Core Service Tests
# ========================================
echo ""
echo -e "${BLUE}▸ Core Services${NC}"

# llama-server
if ! $QUICK; then
    test_http "llama-server health" "http://${SERVICE_HOST}:${LLM_PORT}/health"
    test_llm "llama-server inference" "http://${SERVICE_HOST}:${LLM_PORT}" "Say hello in exactly 3 words."
else
    log_skip "llama-server inference test"
fi

# n8n / Qdrant — optional in the default stack
test_http_optional "n8n health" "http://${SERVICE_HOST}:${N8N_PORT}/healthz"
test_http_optional "Qdrant health" "http://${SERVICE_HOST}:${QDRANT_PORT}/"

# ========================================
# Voice Services Tests
# ========================================
echo ""
echo -e "${BLUE}▸ Voice Services${NC}"

# Whisper (Speaches container): serves /health, not /. Default port 9000
# per manifest (not 9001). AMD Lemonade installs may override to 9100 via
# WHISPER_PORT in .env — respected above.
test_http_optional "Whisper STT" "http://${SERVICE_HOST}:${WHISPER_PORT}/health"
# Kokoro-FastAPI serves /health; the root path 404s.
test_http_optional "Kokoro TTS" "http://${SERVICE_HOST}:${TTS_PORT}/health"
# LiveKit's HTTP port is a WebSocket-signaling endpoint; GET / returns 404
# when the service is up and healthy. Only a connection-refused (000) means
# it's not running. Treat 404 as the "up" signature.
test_http_optional "LiveKit" "http://${SERVICE_HOST}:${LIVEKIT_PORT}/" "404"

# ========================================
# Dashboard UI Tests
# ========================================
echo ""
echo -e "${BLUE}▸ Dashboard UI${NC}"

test_http "Dashboard serves" "http://${SERVICE_HOST}:${DASHBOARD_PORT}/"

# ========================================
# Summary
# ========================================
echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
TOTAL=$((PASSED + FAILED + SKIPPED))
echo -e "Results: ${GREEN}$PASSED passed${NC} / ${RED}$FAILED failed${NC} / ${YELLOW}$SKIPPED skipped${NC} ($TOTAL total)"

if [[ $FAILED -gt 0 ]]; then
    echo -e "${RED}Some tests failed. Check the output above.${NC}"
    exit 1
else
    echo -e "${GREEN}All active tests passed!${NC}"
    exit 0
fi
