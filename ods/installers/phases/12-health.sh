#!/bin/bash
# ============================================================================
# ODS Installer — Phase 12: Health Checks
# ============================================================================
# Part of: installers/phases/
# Purpose: Verify all services are responding, configure Perplexica,
#          pre-download STT model
#
# Expects: DRY_RUN, GPU_BACKEND, ENABLE_VOICE, ENABLE_WORKFLOWS, ENABLE_RAG, ENABLE_QDRANT,
#           ENABLE_EMBEDDINGS, ENABLE_HERMES, ENABLE_OPENCLAW, LLM_MODEL,
#           LOG_FILE, BGRN, AMB, NC,
#           WHISPER_PORT, TTS_PORT, OPENCLAW_PORT,
#           PERPLEXICA_PORT (:-3004), COMFYUI_PORT (:-8188),
#           show_phase(), check_service(), ai(), ai_ok(), ai_warn(), signal()
# Provides: Health check results, Perplexica auto-configuration
#
# Modder notes:
#   Add new service health checks or auto-configuration here.
# ============================================================================

# Source service registry for port/health resolution
. "$SCRIPT_DIR/lib/service-registry.sh"
sr_load

# Resolve port overrides from .env (SERVICE_PORTS uses manifest defaults
# but .env may override them, e.g. OLLAMA_PORT=11434 on Strix Halo)
if [[ -f "$INSTALL_DIR/.env" ]]; then
    . "$SCRIPT_DIR/lib/safe-env.sh" 2>/dev/null || true
    load_env_file "$INSTALL_DIR/.env"
    sr_resolve_ports
fi

ods_progress 85 "health" "Checking service health"
show_phase 6 6 "Systems Online" "~1-2 minutes"

if $DRY_RUN; then
    log "[DRY RUN] Would verify service health:"
    log "[DRY RUN]   - llama-server, Open WebUI, Perplexica, ComfyUI"
    log "[DRY RUN]   - Auto-configure Perplexica for ${LLM_MODEL:-default model}"
    [[ "$ENABLE_HERMES" == "true" ]] && log "[DRY RUN]   - Hermes Agent + hermes-proxy"
    [[ "$ENABLE_OPENCLAW" == "true" ]] && log "[DRY RUN]   - OpenClaw"
    [[ "$ENABLE_VOICE" == "true" ]] && log "[DRY RUN]   - Whisper (STT), Kokoro (TTS), pre-download STT model"
    [[ "$ENABLE_WORKFLOWS" == "true" ]] && log "[DRY RUN]   - n8n"
    [[ "${ENABLE_QDRANT:-${ENABLE_RAG:-false}}" == "true" ]] && log "[DRY RUN]   - Qdrant"
    [[ "${ENABLE_EMBEDDINGS:-${ENABLE_RAG:-false}}" == "true" ]] && log "[DRY RUN]   - Embeddings (TEI)"
    echo ""
    signal "All systems nominal. (dry run)"
    ai_ok "Sovereign intelligence is online. (dry run)"
    return 0 2>/dev/null || true
fi

ai "Linking services... standby."

sleep 5

# Health checks are best-effort — track failures but don't let set -e kill the install.
# Services may need more startup time; we report all failures at the end.
HEALTH_FAILURES=0
EMBEDDINGS_HEALTH_FAILED=false
_check_health() {
    if ! check_service "$@"; then
        HEALTH_FAILURES=$((HEALTH_FAILURES + 1))
    fi
}

_check_container_health() {
    local name=$1
    local container_name=$2
    local max_attempts=${3:-60}
    local docker_cmd="${DOCKER_CMD:-docker}"
    local -a docker_cmd_arr=()
    read -r -a docker_cmd_arr <<< "$docker_cmd"
    [[ ${#docker_cmd_arr[@]} -gt 0 ]] || docker_cmd_arr=(docker)

    printf "  ${GRN}...${NC} Waiting for %-20s " "$name"
    for attempt in $(seq 1 "$max_attempts"); do
        local state=""
        state=$("${docker_cmd_arr[@]}" inspect --format '{{.State.Status}}' "$container_name" 2>/dev/null || echo "missing")
        case "$state" in
            exited|dead|missing)
                printf "\r  ${RED}ERR${NC} %-55s\n" "$name container $state"
                ai_warn "$name container is $state; not retrying health probe."
                return 1
                ;;
        esac

        local health=""
        health=$("${docker_cmd_arr[@]}" inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_name" 2>/dev/null || echo "missing")
        case "$health" in
            healthy)
                printf "\r  ${BGRN}OK${NC} %-56s\n" "$name healthy"
                return 0
                ;;
            running)
                # No Docker healthcheck declared. Treat running as good enough.
                printf "\r  ${BGRN}OK${NC} %-56s\n" "$name running"
                return 0
                ;;
        esac

        sleep 5
    done

    printf "\r  ${AMB}WARN${NC} %-54s\n" "$name delayed (container health not healthy yet)"
    ai_warn "$name container health is not healthy yet. I will continue."
    return 1
}

_phase12_env_get() {
    local key="$1" default="${2:-}" env_file="${INSTALL_DIR:-.}/.env"
    if [[ -f "$env_file" ]]; then
        local value
        value=$(grep -m1 "^${key}=" "$env_file" 2>/dev/null | cut -d= -f2- || true)
        [[ -n "$value" ]] && { echo "$value"; return 0; }
    fi
    echo "$default"
}

_phase12_external_lemonade() {
    local external managed mode
    external="${LEMONADE_EXTERNAL:-$(_phase12_env_get LEMONADE_EXTERNAL false)}"
    managed="${AMD_INFERENCE_MANAGED:-$(_phase12_env_get AMD_INFERENCE_MANAGED "")}"
    mode="${ODS_MODE:-$(_phase12_env_get ODS_MODE local)}"
    [[ "${external,,}" == "true" ]] || [[ "${mode,,}" == "lemonade" && "${managed,,}" == "false" ]]
}

_phase12_model_looks_non_chat() {
    local model_lc="${1,,}"
    [[ "$model_lc" == *flux* ]] \
        || [[ "$model_lc" == *stable-diffusion* ]] \
        || [[ "$model_lc" == *sdxl* ]] \
        || [[ "$model_lc" == *diffusion* ]] \
        || [[ "$model_lc" == *dall-e* ]] \
        || [[ "$model_lc" == *image* ]] \
        || [[ "$model_lc" == *img2img* ]] \
        || [[ "$model_lc" == *txt2img* ]] \
        || [[ "$model_lc" == *comfy* ]]
}

_phase12_verify_external_lemonade_completion() {
    local litellm_port="${SERVICE_PORTS[litellm]:-4000}"
    local litellm_key="${LITELLM_KEY:-$(_phase12_env_get LITELLM_KEY "")}"
    local model="${LEMONADE_MODEL:-$(_phase12_env_get LEMONADE_MODEL default)}"
    [[ -n "$model" ]] || model="default"
    local auth_header=()
    [[ -n "$litellm_key" ]] && auth_header=(-H "Authorization: Bearer ${litellm_key}")
    local body response
    body='{"model":"default","messages":[{"role":"user","content":"Reply with exactly OK. /no_think"}],"max_tokens":16,"temperature":0,"stream":false}'

    ai "Verifying external Lemonade completion route through LiteLLM..."
    response="$(curl -sS --max-time 180 -X POST "http://127.0.0.1:${litellm_port}/v1/chat/completions" \
        "${auth_header[@]}" \
        -H "Content-Type: application/json" \
        -d "$body" 2>&1)" || {
        printf "  ${RED}ERR${NC} External Lemonade completion failed\n"
        ai_warn "LiteLLM could not complete through external Lemonade (model: ${model})."
        ai_warn "Check that Lemonade is reachable from Docker containers, is bound to 0.0.0.0 on trusted hosts, and that LEMONADE_MODEL matches /api/v1/models."
        printf '%s\n' "$response" >> "$LOG_FILE"
        return 1
    }

    if printf '%s\n' "$response" | grep -Eq '"content"[[:space:]]*:[[:space:]]*"[^"]+'; then
        printf "  ${BGRN}OK${NC} External Lemonade completion route healthy\n"
        return 0
    fi

    printf "  ${RED}ERR${NC} External Lemonade returned no assistant content\n"
    ai_warn "LiteLLM reached external Lemonade but did not receive non-empty assistant content (model: ${model})."
    if _phase12_model_looks_non_chat "$model"; then
        ai_warn "The selected Lemonade model looks like an image/non-chat model. ODS needs a text/chat model for the LLM route."
    fi
    ai_warn "Run: curl ${LEMONADE_BASE_URL:-$(_phase12_env_get LEMONADE_BASE_URL http://127.0.0.1:13305)}${LEMONADE_API_BASE_PATH:-$(_phase12_env_get LEMONADE_API_BASE_PATH /api/v1)}/models"
    if [[ -f "${SCRIPT_DIR}/install.sh" ]]; then
        ai_warn "Then rerun from ${SCRIPT_DIR}: LEMONADE_MODEL=<chat-model-id> ./install.sh --use-existing-lemonade ..."
    else
        ai_warn "Then rerun from ${SCRIPT_DIR}: LEMONADE_MODEL=<chat-model-id> bash install-core.sh --use-existing-lemonade ..."
    fi
    printf '%s\n' "$response" >> "$LOG_FILE"
    return 1
}

# Core service health checks with adaptive timeouts.
# Cloud mode does not launch local llama-server; LiteLLM/external APIs are the
# LLM surface, so do not wait on a container that intentionally is not running.
if [[ "${ODS_MODE:-local}" == "cloud" ]] || _phase12_external_lemonade; then
    ods_progress 86 "health" "Waiting for LiteLLM gateway"
    _check_health "LiteLLM" "http://127.0.0.1:${SERVICE_PORTS[litellm]:-4000}${SERVICE_HEALTH[litellm]:-/health/readiness}" 60 10 "$(sr_container litellm)"
    if _phase12_external_lemonade; then
        ods_progress 87 "health" "Verifying external Lemonade route"
        if ! _phase12_verify_external_lemonade_completion; then
            exit 1
        fi
    fi
else
    # Format: _check_health "name" "url" max_attempts timeout_per_request
    # llama-server: 150 attempts * adaptive backoff (2s->8s) = up to ~20 minutes (model loading can be slow)
    _llm_health_attempts=150
    if [[ "${COMPOSE_STARTED_WITH_DELAYED_HEALTH:-false}" == "true" ]]; then
        # If phase 11 already saw Docker Compose outlive its own health-gate
        # window, keep the installer in the long readiness loop instead of
        # failing right as very large GGUFs finish loading after reinstall.
        _llm_health_attempts="${ODS_LLM_DELAYED_HEALTH_ATTEMPTS:-300}"
    fi

    # When a background model upgrade is in progress the bootstrap model is
    # already serving and healthy — the full model will hot-swap automatically
    # once the download finishes. Blocking here would fail the installer on
    # hosts where the full model download + swap exceeds the health window
    # (e.g. tower2 with a 24 GB GGUF after a ComfyUI rebuild).
    _bootstrap_status_file="${INSTALL_DIR}/data/bootstrap-status.json"
    _model_download_active=false
    if [[ -f "$_bootstrap_status_file" ]]; then
        _bs_status="$(grep -o '"status"[[:space:]]*:[[:space:]]*"[^"]*"' "$_bootstrap_status_file" 2>/dev/null | head -1 | cut -d'"' -f4)"
        case "$_bs_status" in
            starting|downloading|verifying|swapping)
                _model_download_active=true
                ;;
        esac
    fi
    if [[ "$_model_download_active" != "true" ]] && command -v bg_task_status >/dev/null 2>&1; then
        if bg_task_status "full-model-download" >/dev/null 2>&1; then
            _model_download_active=true
        fi
    fi

    if [[ "$_model_download_active" == "true" ]]; then
        ai_ok "Full model upgrade in progress — skipping blocking LLM health wait"
        ai "  Monitor progress: tail -f ${INSTALL_DIR}/logs/model-upgrade.log"
    else
        ods_progress 86 "health" "Waiting for LLM engine"
        _check_health "llama-server" "http://127.0.0.1:${SERVICE_PORTS[llama-server]:-8080}${SERVICE_HEALTH[llama-server]:-/health}" "$_llm_health_attempts" 15 "$(sr_container llama-server)"
    fi
fi

# ── Pre-warm the LLM slot so the first real chat doesn't 503 ──
#
# /health goes green once the model is mmap'd, but the FIRST chat
# completion still materializes the KV cache, JIT-compiles fused kernels,
# and processes any system prompt the caller injects. While that's in
# flight, llama-server returns `HTTP 503: Loading model` to concurrent
# requests — including from Hermes Agent, which sends a ~14k-token system
# prompt and only retries 3 times within 120s. On big-model + single-GPU
# boxes (DGX Spark with the 45 GB qwen3-coder-next, tested 2026-05-12),
# that 14k prefill alone takes ~54s — fitting inside Hermes's first-call
# window only by luck. Roll the dice the wrong way and Hermes 503s every
# prompt for the rest of the install run.
#
# Pre-warming with a tiny dummy completion forces the slot through its
# cold path inside the installer (where time isn't surprising) so Hermes
# lands on an already-hot slot. Bounded by curl --max-time so a stalled
# llama-server doesn't hang phase 12.
if [[ "${ODS_MODE:-local}" == "cloud" ]] || _phase12_external_lemonade; then
    ai "External LLM mode - skipping local llama-server pre-warm"
else
    ods_progress 87 "health" "Pre-warming LLM slot"
    _prewarm_api_path="/v1"
    _prewarm_model="${GGUF_FILE:-${LLM_MODEL:-default}}"
    if [[ "${GPU_BACKEND:-}" == "amd" ]]; then
        _prewarm_api_path="/api/v1"
        [[ -n "${GGUF_FILE:-}" ]] && _prewarm_model="extra.${GGUF_FILE}"
    fi
    _prewarm_url="http://127.0.0.1:${SERVICE_PORTS[llama-server]:-8080}${_prewarm_api_path}/chat/completions"
    _prewarm_body="{\"model\":\"${_prewarm_model}\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":1,\"temperature\":0,\"stream\":false}"
    if curl -sf --max-time 120 -X POST "$_prewarm_url" \
        -H "Content-Type: application/json" \
        -d "$_prewarm_body" >/dev/null 2>&1; then
        ai_ok "LLM slot pre-warmed (first real chat will be fast)"
    else
        ai_warn "LLM pre-warm timed out — first Hermes prompt may need a retry or two while the slot finishes warming."
    fi
fi

# Open WebUI: 150 attempts * adaptive backoff = up to ~20 minutes
ods_progress 89 "health" "Waiting for Chat UI"
_check_health "Open WebUI" "http://127.0.0.1:${SERVICE_PORTS[open-webui]:-3000}${SERVICE_HEALTH[open-webui]:-/}" 150 10 "$(sr_container open-webui)"
# Perplexica: 150 attempts * adaptive backoff = up to ~20 minutes
if [[ "${ENABLE_PERPLEXICA:-false}" == "true" ]]; then
    ods_progress 91 "health" "Waiting for Research engine"
    _check_health "Perplexica" "http://127.0.0.1:${SERVICE_PORTS[perplexica]:-3004}${SERVICE_HEALTH[perplexica]:-/}" 150 10 "$(sr_container perplexica)"
fi
# ComfyUI: 150 attempts * adaptive backoff = up to ~20 minutes (FLUX model loading is slow)
if [[ "$ENABLE_COMFYUI" == "true" ]]; then
    ods_progress 93 "health" "Waiting for Image generation"
    _check_health "ComfyUI" "http://127.0.0.1:${SERVICE_PORTS[comfyui]:-8188}${SERVICE_HEALTH[comfyui]:-/}" 150 15 "$(sr_container comfyui)"
fi
# Embeddings (TEI): 150 attempts * adaptive backoff = up to ~20 minutes.
# Matches every other service. The 30-attempt cap (~230s total) used to
# print spurious "embeddings delayed (may still be starting)" warnings on
# fresh installs because TEI downloads its ONNX model from HuggingFace on
# first start (BAAI/bge-base-en-v1.5 is ~440 MB), which can take 3-7 min
# on bufferbloated networks before /health responds.
if [[ "${ENABLE_EMBEDDINGS:-${ENABLE_RAG:-false}}" == "true" ]]; then
    ods_progress 94 "health" "Waiting for Embeddings"
    if ! check_service "embeddings" "http://127.0.0.1:${SERVICE_PORTS[embeddings]:-8090}${SERVICE_HEALTH[embeddings]:-/health}" 150 10 "$(sr_container embeddings)"; then
        HEALTH_FAILURES=$((HEALTH_FAILURES + 1))
        EMBEDDINGS_HEALTH_FAILED=true
    fi
fi

# Perplexica auto-config: seed chat model + embedding model on first boot.
# The slim-latest image stores config in a database, not just config.json.
# We use the /api/config HTTP endpoint to set values after the service starts.
# Retry up to 5 times with 10s delay — Perplexica may still be starting
# (especially if it was stuck in "Created" state and started late).
if $DOCKER_CMD inspect ods-perplexica &>/dev/null; then
    PERPLEXICA_URL="http://127.0.0.1:${SERVICE_PORTS[perplexica]:-3004}"
    PERPLEXICA_MODEL="${LLM_MODEL:-default}"
    if [[ -n "${GGUF_FILE:-}" ]]; then
        PERPLEXICA_MODEL="$GGUF_FILE"
        _perplexica_backend="$(printf '%s' "${LLM_BACKEND:-${AMD_INFERENCE_RUNTIME:-}}" | tr '[:upper:]' '[:lower:]')"
        if [[ "$_perplexica_backend" == "lemonade" ]]; then
            PERPLEXICA_MODEL="extra.$GGUF_FILE"
        fi
    fi
    PERPLEXICA_LLM_BASE_URL="${LLM_API_URL:-http://llama-server:8080}"
    case "$PERPLEXICA_LLM_BASE_URL" in
        */v1|*/api/v1) ;;
        *) PERPLEXICA_LLM_BASE_URL="${PERPLEXICA_LLM_BASE_URL%/}/v1" ;;
    esac
    PERPLEXICA_API_KEY="${LITELLM_KEY:-${OPENAI_API_KEY:-no-key}}"
    PYTHON_CMD="python3"
    if [[ -f "$SCRIPT_DIR/lib/python-cmd.sh" ]]; then
        . "$SCRIPT_DIR/lib/python-cmd.sh"
        PYTHON_CMD="$(ods_detect_python_cmd)"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_CMD="python"
    fi

    PERPLEXICA_SETUP="skip"
    for _attempt in 1 2 3 4 5; do
        PERPLEXICA_SETUP=$(curl -sf --max-time 5 "${PERPLEXICA_URL}/api/config" 2>/dev/null | \
            PERPLEXICA_MODEL="$PERPLEXICA_MODEL" "$PYTHON_CMD" -c '
import os, sys, json
values = json.load(sys.stdin).get("values", {})
model = os.environ["PERPLEXICA_MODEL"]
providers = values.get("modelProviders", [])
openai_prov = next((p for p in providers if p.get("type") == "openai"), {})
chat_models = openai_prov.get("chatModels") or []
prefs = values.get("preferences") or {}
has_model = any(m.get("key") == model or m.get("name") == model for m in chat_models)
is_ready = bool(values.get("setupComplete")) and has_model and prefs.get("defaultChatModel") == model
print("done" if is_ready else "needed")
' 2>/dev/null || echo "skip")
        [[ "$PERPLEXICA_SETUP" != "skip" ]] && break
        [[ $_attempt -lt 5 ]] && sleep 10
    done

    if [[ "$PERPLEXICA_SETUP" == "needed" ]]; then
        ai "Configuring Perplexica for ${PERPLEXICA_MODEL}..."
        # Query current config to get provider UUIDs, then set model + preferences via API
        curl -sf "${PERPLEXICA_URL}/api/config" 2>/dev/null | \
        PERPLEXICA_URL="$PERPLEXICA_URL" \
        PERPLEXICA_MODEL="$PERPLEXICA_MODEL" \
        PERPLEXICA_LLM_BASE_URL="$PERPLEXICA_LLM_BASE_URL" \
        PERPLEXICA_API_KEY="$PERPLEXICA_API_KEY" \
        "$PYTHON_CMD" -c '
import os
import sys, json, urllib.request

config = json.load(sys.stdin)["values"]
providers = config.get("modelProviders", [])
openai_prov = next((p for p in providers if p["type"] == "openai"), None)
transformers_prov = next((p for p in providers if p["type"] == "transformers"), None)

if not openai_prov:
    print("no-openai-provider")
    sys.exit(1)

url = os.environ["PERPLEXICA_URL"] + "/api/config"
model = os.environ["PERPLEXICA_MODEL"]
base_url = os.environ["PERPLEXICA_LLM_BASE_URL"]
api_key = os.environ["PERPLEXICA_API_KEY"] or "no-key"

def post(key, value):
    data = json.dumps({"key": key, "value": value}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10)

def post_setup_complete():
    setup_url = os.environ["PERPLEXICA_URL"] + "/api/config/setup-complete"
    req = urllib.request.Request(setup_url, data=b"{}", headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        post("setupComplete", True)

# Seed the chat model into the OpenAI provider and set auth config
openai_prov["chatModels"] = [{"key": model, "name": model}]
openai_prov["config"] = {
    **(openai_prov.get("config") or {}),
    "apiKey": api_key,
    "baseURL": base_url,
}
post("modelProviders", providers)

# Set default providers and models
post("preferences", {
    "defaultChatProvider": openai_prov["id"],
    "defaultChatModel": model,
    "defaultEmbeddingProvider": transformers_prov["id"] if transformers_prov else openai_prov["id"],
    "defaultEmbeddingModel": "Xenova/all-MiniLM-L6-v2"
})

# Mark setup complete to bypass the wizard
post_setup_complete()
print("ok")
' >> "$LOG_FILE" 2>&1 && \
            printf "\r  ${BGRN}✓${NC} %-60s\n" "Perplexica configured (model: ${PERPLEXICA_MODEL})" || \
            printf "\r  ${AMB}⚠${NC} %-60s\n" "Perplexica config — complete setup at :${PERPLEXICA_PORT:-3004}"
    fi
fi

# Extension service health checks with adaptive timeouts
ods_progress 94 "health" "Checking extension services"
# Hermes is intentionally internal-only: its port is exposed only inside the
# Docker network, not bound to the host. Wait on the container healthcheck
# instead of curling localhost:9119, which would fail on a correct install.
if [[ "$ENABLE_HERMES" == "true" ]]; then
    if ! _check_container_health "Hermes Agent" "$(sr_container hermes)" 60; then
        HEALTH_FAILURES=$((HEALTH_FAILURES + 1))
    fi
fi
# hermes-proxy is the LAN-facing entry and has an anonymous /health endpoint.
[[ "$ENABLE_HERMES" == "true" ]] && _check_health "Hermes Proxy" "http://127.0.0.1:${SERVICE_PORTS[hermes-proxy]:-9120}${SERVICE_HEALTH[hermes-proxy]:-/health}" 60 5 "$(sr_container hermes-proxy)"
[[ "$ENABLE_OPENCLAW" == "true" ]] && _check_health "OpenClaw" "http://127.0.0.1:${SERVICE_PORTS[openclaw]:-7860}${SERVICE_HEALTH[openclaw]:-/}" 150 10 "$(sr_container openclaw)"
systemctl --user is-active opencode-web &>/dev/null && _check_health "OpenCode Web" "http://127.0.0.1:3003/" 10 5
# Whisper: 150 attempts * adaptive backoff = up to ~20 minutes (model download on first start)
ods_progress 95 "health" "Checking voice services"
[[ "$ENABLE_VOICE" == "true" ]] && _check_health "Whisper (STT)" "http://127.0.0.1:${SERVICE_PORTS[whisper]:-9000}${SERVICE_HEALTH[whisper]:-/health}" 150 10 "$(sr_container whisper)"
[[ "$ENABLE_VOICE" == "true" ]] && _check_health "Kokoro (TTS)" "http://127.0.0.1:${SERVICE_PORTS[tts]:-8880}${SERVICE_HEALTH[tts]:-/health}" 150 10 "$(sr_container tts)"

# Pre-download the Whisper STT model so first transcription is instant.
# Speaches does NOT auto-download on transcription requests — it returns 404.
# We must trigger the download explicitly here, verify it completed, and
# surface a clear recovery command if anything fails.
if [[ "$ENABLE_VOICE" == "true" ]]; then
    # Prefer AUDIO_STT_MODEL from .env (written by Phase 06). Fall back to the
    # GPU_BACKEND switch for backward compat with older .env files missing it.
    if [[ -n "${AUDIO_STT_MODEL:-}" ]]; then
        STT_MODEL="$AUDIO_STT_MODEL"
    elif [[ "$GPU_BACKEND" == "nvidia" ]]; then
        STT_MODEL="deepdml/faster-whisper-large-v3-turbo-ct2"
    else
        STT_MODEL="Systran/faster-whisper-base"
    fi
    STT_MODEL_ENCODED="${STT_MODEL//\//%2F}"
    WHISPER_PORT_RESOLVED="${SERVICE_PORTS[whisper]:-9000}"
    WHISPER_URL="http://127.0.0.1:${WHISPER_PORT_RESOLVED}"
    STT_RECOVERY_CMD="curl --max-time 1800 -X POST ${WHISPER_URL}/v1/models/${STT_MODEL_ENCODED}"

    # Step 1: wait briefly for the models API to be ready. Whisper's /health
    # endpoint can pass before the models endpoint responds, so we probe
    # GET /v1/models with a short retry loop (max 15s total).
    _stt_api_ready=false
    for _i in $(seq 1 15); do
        if curl -sf --max-time 2 "${WHISPER_URL}/v1/models" &>/dev/null; then
            _stt_api_ready=true
            break
        fi
        sleep 1
    done

    if ! $_stt_api_ready; then
        printf "\r  ${AMB}⚠${NC} %-60s\n" "STT models API not ready — download manually:"
        printf "      %s\n" "$STT_RECOVERY_CMD"
    # Step 2: skip download if already cached.
    elif curl -sf --max-time 10 "${WHISPER_URL}/v1/models/${STT_MODEL_ENCODED}" &>/dev/null; then
        printf "\r  ${BGRN}✓${NC} %-60s\n" "STT model already cached (${STT_MODEL})"
    else
        # Step 3: POST to trigger download. Log stdout/stderr to install log.
        # max-time 600s (10 min): bounded retry budget so a stuck
        # huggingface_hub.snapshot_download (well-known on slow links and
        # under bufferbloat) can't consume the entire install timeout. The
        # next step verifies cache state via GET and prints the recovery
        # command if the timeout was hit before completion.
        ai "Downloading STT model (${STT_MODEL})..."
        curl -s --max-time 600 -X POST "${WHISPER_URL}/v1/models/${STT_MODEL_ENCODED}" \
            >> "$LOG_FILE" 2>&1 || true

        # Step 4: verify the model is actually cached. POST can return 200
        # even if the download partially fails, so this GET is the real test.
        if curl -sf --max-time 10 "${WHISPER_URL}/v1/models/${STT_MODEL_ENCODED}" &>/dev/null; then
            printf "\r  ${BGRN}✓${NC} %-60s\n" "STT model cached (${STT_MODEL})"
        else
            printf "\r  ${AMB}⚠${NC} %-60s\n" "STT model download failed — run manually:"
            printf "      %s\n" "$STT_RECOVERY_CMD"
            printf "      %s\n" "See $LOG_FILE for details."
        fi
    fi
fi

ods_progress 96 "health" "Checking workflow and RAG services"
[[ "$ENABLE_WORKFLOWS" == "true" ]] && _check_health "n8n" "http://127.0.0.1:${SERVICE_PORTS[n8n]:-5678}${SERVICE_HEALTH[n8n]:-/healthz}" 150 10 "$(sr_container n8n)"
[[ "${ENABLE_QDRANT:-${ENABLE_RAG:-false}}" == "true" ]] && _check_health "Qdrant" "http://127.0.0.1:${SERVICE_PORTS[qdrant]:-6333}${SERVICE_HEALTH[qdrant]:-/}" 150 10 "$(sr_container qdrant)"

ods_progress 97 "health" "Health checks complete"
echo ""
if [[ "$HEALTH_FAILURES" -gt 0 ]]; then
    ai_warn "${HEALTH_FAILURES} service(s) did not pass health checks."
    ai_warn "Some services may still be starting. Check with: ods status"
    ai_warn "Logs: docker compose logs <service-name>"
    if [[ "$EMBEDDINGS_HEALTH_FAILED" == "true" ]]; then
        ai_warn "Embeddings/RAG was selected, but the embeddings service did not become healthy."
        ai_warn "This often means text-embeddings-inference stalled while downloading its ONNX model from Hugging Face."
        ai_warn "Recovery: docker compose logs embeddings"
        ai_warn "Then retry after network/CDN recovery: docker compose up -d embeddings"
        exit 1
    fi
    if [[ "${COMPOSE_STARTED_WITH_DELAYED_HEALTH:-false}" == "true" ]]; then
        ai_warn "Docker Compose reported delayed LLM health during launch, and the longer health checks did not recover."
        exit 1
    fi
else
    signal "All systems nominal."
    ai_ok "Sovereign intelligence is online."
fi
