#!/bin/bash
# ODS Pre-flight Check
# Validates all services start correctly before user interaction
# Backend-aware: detects AMD vs NVIDIA (both use llama-server)
# Usage: ./ods-preflight.sh
#        ./ods-preflight.sh --install-env   # Linux install environment report (JSON: see scripts/linux-install-preflight.sh --help)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_DIR="$SCRIPT_DIR"

case "${1:-}" in
    --install-env|--env-report)
        shift
        exec "$SCRIPT_DIR/scripts/linux-install-preflight.sh" "$@"
        ;;
esac
LOG_FILE="$ODS_DIR/preflight-$(date +%Y%m%d-%H%M%S).log"

# Safe .env loading (no eval; use lib/safe-env.sh)
[[ -f "$ODS_DIR/lib/safe-env.sh" ]] && . "$ODS_DIR/lib/safe-env.sh"
load_env_file "$ODS_DIR/.env"

SERVICE_HOST="${SERVICE_HOST:-localhost}"

# Auto-detect backend from .env or hardware probing.
# Priority: .env setting → nvidia-smi → AMD sysfs (any card).
# On dual-GPU systems (AMD iGPU + NVIDIA dGPU) we must prefer
# NVIDIA when present, since it is always the inference target.
detect_backend() {
    # 1. Trust .env if the installer already wrote it.
    if [[ "${GPU_BACKEND:-}" == "amd" ]]; then
        echo "amd"
        return
    fi
    if [[ "${GPU_BACKEND:-}" == "nvidia" ]]; then
        echo "nvidia"
        return
    fi

    # 2. Probe NVIDIA first (matches installer's detect_gpu order).
    #    Validate hardware via sysfs vendor ID before trusting nvidia-smi,
    #    which may be installed without NVIDIA hardware.
    local _nvidia_hw=false
    for _v in /sys/class/drm/card*/device/vendor; do
        [[ "$(cat "$_v" 2>/dev/null)" == "0x10de" ]] && _nvidia_hw=true && break
    done
    if $_nvidia_hw && command -v nvidia-smi &> /dev/null; then
        if nvidia-smi --query-gpu=name --format=csv,noheader &> /dev/null; then
            echo "nvidia"
            return
        fi
    fi

    # 3. Probe AMD sysfs — scan all DRM cards, not just card1.
    for card_dir in /sys/class/drm/card*/device; do
        [[ -d "$card_dir" ]] || continue
        if [[ "$(cat "$card_dir/vendor" 2>/dev/null)" == "0x1002" ]]; then
            echo "amd"
            return
        fi
    done

    # 4. No GPU detected — default to cpu.
    echo "cpu"
}

BACKEND=$(detect_backend)

is_external_lemonade() {
    local external="${LEMONADE_EXTERNAL:-false}"
    local managed="${AMD_INFERENCE_MANAGED:-}"
    local mode="${ODS_MODE:-local}"
    [[ "${external,,}" == "true" ]] || [[ "${mode,,}" == "lemonade" && "${managed,,}" == "false" ]]
}

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PASS=0
FAIL=0
WARN=0

log() {
    echo -e "$1"
    echo -e "$1" | sed $'s/\033\\[[0-9;]*m//g' >> "$LOG_FILE"
}
pass() { log "${GREEN}✓${NC} $1"; PASS=$((PASS+1)); }
fail() { log "${RED}✗${NC} $1"; FAIL=$((FAIL+1)); }
warn() { log "${YELLOW}⚠${NC} $1"; WARN=$((WARN+1)); }

echo "" > "$LOG_FILE"
log "========================================"
log "ODS Pre-flight Check"
log "Started: $(date)"
log "Backend: $BACKEND"
log "========================================"
log ""

# 1. Docker check
log "[1/8] Checking Docker..."
if command -v docker &> /dev/null; then
    DOCKER_VERSION=$(docker --version | awk '{print $3}' | tr -d ',')
    pass "Docker installed: $DOCKER_VERSION"

    if docker info &> /dev/null; then
        pass "Docker daemon running"
        # Rootless mode: non-root container UIDs are shifted by the subuid
        # offset (typically 100000) so data directories created by the
        # installer are inaccessible to those containers.  This is not a
        # hard failure — ODS installs and starts fine once ownership is fixed.
        if docker info --format '{{.SecurityOptions}}' 2>/dev/null | grep -q rootless; then
            warn "Docker rootless mode detected — non-root containers (n8n, hermes, whisper, tts, token-spy, privacy-shield, ape, langfuse) may fail with EACCES. Run: ods repair rootless-ownership"
        fi
    else
        fail "Docker daemon not running — start with: sudo systemctl start docker"
    fi
else
    fail "Docker not installed"
fi
log ""

# 2. Docker Compose check
log "[2/8] Checking Docker Compose..."
if docker compose version &> /dev/null 2>&1 || docker-compose version &> /dev/null 2>&1; then
    COMPOSE_VERSION=$(docker compose version 2>/dev/null | awk '{print $4}' || docker-compose version 2>/dev/null | head -1 | awk '{print $3}')
    pass "Docker Compose available: $COMPOSE_VERSION"
else
    fail "Docker Compose not found"
fi
log ""

# 3. GPU check — backend-aware
log "[3/8] Checking GPU..."
if [[ "$BACKEND" == "amd" ]]; then
    # AMD: check sysfs for GPU and driver
    GPU_FOUND=false
    for card_dir in /sys/class/drm/card*/device; do
        [[ -d "$card_dir" ]] || continue
        vendor=$(cat "$card_dir/vendor" 2>/dev/null) || continue
        if [[ "$vendor" == "0x1002" ]]; then
            device_id=$(cat "$card_dir/device" 2>/dev/null || echo "unknown")
            gtt_bytes=$(cat "$card_dir/mem_info_gtt_total" 2>/dev/null || echo "0")
            gtt_gb=$(( gtt_bytes / 1073741824 ))
            if lsmod 2>/dev/null | grep -q amdgpu; then
                pass "AMD GPU detected ($device_id) — ${gtt_gb}GB GTT, amdgpu driver loaded"
            else
                warn "AMD GPU detected ($device_id) but amdgpu driver not loaded"
            fi
            # Check ROCm device access
            if [[ -c /dev/kfd ]]; then
                pass "ROCm device /dev/kfd accessible"
            else
                warn "/dev/kfd not found — ROCm containers may fail"
            fi
            if [[ -d /dev/dri ]]; then
                pass "AMD GPU device nodes available (/dev/dri)"
            fi
            GPU_FOUND=true
            break
        fi
    done
    if [[ "$GPU_FOUND" == "false" ]]; then
        warn "No AMD GPU detected via sysfs"
    fi
elif [[ "$BACKEND" == "nvidia" ]]; then
    # NVIDIA: check nvidia-smi
    if command -v nvidia-smi &> /dev/null; then
        GPU_INFO=""
        if raw_gpu=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null); then
            GPU_INFO=$(echo "$raw_gpu" | head -1)
        fi
        if [ -n "$GPU_INFO" ]; then
            pass "NVIDIA GPU detected: $GPU_INFO"
            if docker info 2>/dev/null | grep -q "nvidia"; then
                pass "NVIDIA Docker runtime available"
            else
                warn "NVIDIA Docker runtime not configured — GPU containers may fail"
            fi
        else
            warn "nvidia-smi found but no GPU detected"
        fi
    else
        warn "nvidia-smi not found — NVIDIA GPU features unavailable"
    fi
else
    pass "CPU mode — no GPU runtime required"
fi
log ""

# 4. LLM Endpoint check
# OLLAMA_PORT controls the external port for llama-server.
# Canonical default is 8080 (config/ports.json, docker-compose.base.yml).
# 11434 is only used on Strix Halo AMD installs where phase 06 writes
# OLLAMA_PORT=11434 to .env automatically — it will be picked up via the
# ${OLLAMA_PORT:-...} expansion below, so the fallback should be 8080.
log "[4/8] Checking LLM endpoint..."
if is_external_lemonade; then
    LLM_PORT="${LITELLM_PORT:-4000}"
    LLM_ENDPOINTS=("http://${SERVICE_HOST}:${LLM_PORT}/health/readiness" "http://127.0.0.1:${LLM_PORT}/health/readiness" "http://127.0.0.1:${LLM_PORT}/v1/models")
    LLM_SERVICE_NAME="LiteLLM external Lemonade gateway"
    LLM_CONTAINER_MATCH="ods-litellm"
    LLM_START_CMD="docker compose up -d litellm"
else
    LLM_PORT="${OLLAMA_PORT:-${LLAMA_SERVER_PORT:-8080}}"
    # Also probe the actual mapped port in case docker remapped it
    EXTERNAL_PORT="$(docker port ods-llama-server 8080/tcp 2>/dev/null | head -1 | cut -d: -f2 || true)"
    [[ -n "$EXTERNAL_PORT" ]] || EXTERNAL_PORT="$LLM_PORT"
    LLM_ENDPOINTS=("http://${SERVICE_HOST}:${EXTERNAL_PORT}/health" "http://${SERVICE_HOST}:${EXTERNAL_PORT}/v1/models" "http://127.0.0.1:${EXTERNAL_PORT}/health" "http://127.0.0.1:${EXTERNAL_PORT}/v1/models" "http://127.0.0.1:${LLM_PORT}/health" "http://127.0.0.1:${LLM_PORT}/v1/models")
    LLM_SERVICE_NAME="llama-server"
    LLM_CONTAINER_MATCH="ods-llama-server"
    LLM_START_CMD="docker compose up -d llama-server"
fi

LLM_FOUND=false
for ENDPOINT in "${LLM_ENDPOINTS[@]}"; do
    if curl -sf "$ENDPOINT" &> /dev/null; then
        pass "LLM endpoint ($LLM_SERVICE_NAME) responding at $ENDPOINT"
        LLM_FOUND=true
        break
    fi
done

if [ "$LLM_FOUND" = false ]; then
    # Check if container is running but model still loading
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qi "${LLM_CONTAINER_MATCH}"; then
        warn "$LLM_SERVICE_NAME container running but not responding yet (model may still be loading)"
    else
        fail "No LLM endpoint found — checked: ${LLM_ENDPOINTS[*]}"
        warn "Start $LLM_SERVICE_NAME with: $LLM_START_CMD"
    fi
fi
log ""

# 5. Whisper STT check
log "[5/8] Checking Whisper STT..."
WHISPER_PORT_RESOLVED="${WHISPER_PORT:-9000}"
WHISPER_ENDPOINTS=("http://${SERVICE_HOST}:${WHISPER_PORT_RESOLVED}" "http://127.0.0.1:${WHISPER_PORT_RESOLVED}")
WHISPER_FOUND=false

for ENDPOINT in "${WHISPER_ENDPOINTS[@]}"; do
    if curl -sf "$ENDPOINT/health" &> /dev/null; then
        pass "Whisper STT responding at $ENDPOINT"
        WHISPER_FOUND=true
        break
    fi
done

if [ "$WHISPER_FOUND" = false ]; then
    warn "Whisper STT not found — voice input will be unavailable"
fi
log ""

# 6. TTS check
log "[6/8] Checking TTS (Kokoro)..."
TTS_PORT_RESOLVED="${TTS_PORT:-8880}"
TTS_ENDPOINTS=("http://${SERVICE_HOST}:${TTS_PORT_RESOLVED}" "http://127.0.0.1:${TTS_PORT_RESOLVED}")
TTS_FOUND=false

for ENDPOINT in "${TTS_ENDPOINTS[@]}"; do
    if curl -sf "$ENDPOINT/health" &> /dev/null; then
        pass "TTS endpoint responding at $ENDPOINT"
        TTS_FOUND=true
        break
    fi
done

if [ "$TTS_FOUND" = false ]; then
    warn "TTS not found — voice output will be unavailable"
fi
log ""

# 7. Embeddings check
log "[7/8] Checking Embeddings..."
EMBEDDINGS_PORT_RESOLVED="${EMBEDDINGS_PORT:-8090}"
EMBEDDING_ENDPOINTS=("http://${SERVICE_HOST}:${EMBEDDINGS_PORT_RESOLVED}" "http://127.0.0.1:${EMBEDDINGS_PORT_RESOLVED}")
EMBEDDING_FOUND=false

for ENDPOINT in "${EMBEDDING_ENDPOINTS[@]}"; do
    if curl -sf "$ENDPOINT/health" &> /dev/null; then
        pass "Embeddings endpoint responding at $ENDPOINT"
        EMBEDDING_FOUND=true
        break
    fi
done

if [ "$EMBEDDING_FOUND" = false ]; then
    warn "Embeddings not found — RAG features will be unavailable"
fi
log ""

# 8. Dashboard check (replaces LiveKit — more useful for all backends)
log "[8/8] Checking Dashboard..."
DASHBOARD_ENDPOINTS=("http://${SERVICE_HOST}:3001" "http://127.0.0.1:3001")
DASHBOARD_FOUND=false

for ENDPOINT in "${DASHBOARD_ENDPOINTS[@]}"; do
    if curl -sf "$ENDPOINT" &> /dev/null; then
        pass "Dashboard responding at $ENDPOINT"
        DASHBOARD_FOUND=true
        break
    fi
done

if [ "$DASHBOARD_FOUND" = false ]; then
    warn "Dashboard not found at port 3001"
fi
log ""

# Summary
log "========================================"
log "Pre-flight Summary"
log "========================================"
log "$(printf "${GREEN}✓${NC} Passed: %d" "$PASS")"
log "$(printf "${RED}✗${NC} Failed: %d" "$FAIL")"
log "$(printf "${YELLOW}⚠${NC} Warnings: %d" "$WARN")"
log ""

if [ $FAIL -eq 0 ]; then
    pass "Pre-flight PASSED — ODS is ready!"
    EXIT_CODE=0
else
    fail "Pre-flight FAILED — fix issues above before proceeding"
    EXIT_CODE=1
fi

log ""
log "Full log: $LOG_FILE"

exit $EXIT_CODE
