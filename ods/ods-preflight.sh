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

# Registry owns HTTP health paths/ports/headers (2xx only).
# shellcheck source=lib/service-registry.sh
. "$ODS_DIR/lib/service-registry.sh"
sr_load
sr_resolve_ports

SERVICE_HOST="${SERVICE_HOST:-localhost}"

# Probe a registry service on SERVICE_HOST then 127.0.0.1.
preflight_sr_health() {
    local sid="$1"
    local host
    for host in "$SERVICE_HOST" "127.0.0.1"; do
        if sr_curl_health "$sid" 10 "$host" >/dev/null 2>&1; then
            printf '%s' "$(sr_health_url "$sid" "$host")"
            return 0
        fi
    done
    return 1
}

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

# 4. LLM Endpoint check — registry health first; /v1/models is functional fallback.
log "[4/8] Checking LLM endpoint..."
if is_external_lemonade; then
    LLM_SID="litellm"
    LLM_SERVICE_NAME="LiteLLM external Lemonade gateway"
    LLM_CONTAINER_MATCH="ods-litellm"
    LLM_START_CMD="docker compose up -d litellm"
else
    LLM_SID="llama-server"
    LLM_SERVICE_NAME="llama-server"
    LLM_CONTAINER_MATCH="ods-llama-server"
    LLM_START_CMD="docker compose up -d llama-server"
fi

LLM_FOUND=false
if LLM_URL="$(preflight_sr_health "$LLM_SID")"; then
    pass "LLM endpoint ($LLM_SERVICE_NAME) responding at $LLM_URL"
    LLM_FOUND=true
else
    # Functional readiness (models list) — not a health-path probe.
    for host in "$SERVICE_HOST" "127.0.0.1"; do
        if sr_http_probe_2xx "http://${host}:$(sr_health_port "$LLM_SID")/v1/models" 10 >/dev/null 2>&1; then
            pass "LLM endpoint ($LLM_SERVICE_NAME) models API at http://${host}:$(sr_health_port "$LLM_SID")/v1/models"
            LLM_FOUND=true
            break
        fi
    done
fi

if [ "$LLM_FOUND" = false ]; then
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qi "${LLM_CONTAINER_MATCH}"; then
        warn "$LLM_SERVICE_NAME container running but not responding yet (model may still be loading)"
    else
        fail "No LLM endpoint found for registry service '$LLM_SID'"
        warn "Start $LLM_SERVICE_NAME with: $LLM_START_CMD"
    fi
fi
log ""

# 5. Whisper STT check
log "[5/8] Checking Whisper STT..."
if WHISPER_URL="$(preflight_sr_health whisper)"; then
    pass "Whisper STT responding at $WHISPER_URL"
else
    warn "Whisper STT not found — voice input will be unavailable"
fi
log ""

# 6. TTS check
log "[6/8] Checking TTS (Kokoro)..."
if TTS_URL="$(preflight_sr_health tts)"; then
    pass "TTS endpoint responding at $TTS_URL"
else
    warn "TTS not found — voice output will be unavailable"
fi
log ""

# 7. Embeddings check
log "[7/8] Checking Embeddings..."
if EMB_URL="$(preflight_sr_health embeddings)"; then
    pass "Embeddings endpoint responding at $EMB_URL"
else
    warn "Embeddings not found — RAG features will be unavailable"
fi
log ""

# 8. Dashboard check (replaces LiveKit — more useful for all backends)
log "[8/8] Checking Dashboard..."
if DASH_URL="$(preflight_sr_health dashboard)"; then
    pass "Dashboard responding at $DASH_URL"
else
    warn "Dashboard not found (registry port $(sr_health_port dashboard))"
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
