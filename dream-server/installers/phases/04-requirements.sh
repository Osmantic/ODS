#!/bin/bash
# ============================================================================
# Dream Server Installer — Phase 04: Requirements Check
# ============================================================================
# Part of: installers/phases/
# Purpose: RAM, disk, GPU, and port availability checks
#
# Expects: SCRIPT_DIR, LOG_FILE, TIER, RAM_GB, DISK_AVAIL, GPU_BACKEND,
#           GPU_VRAM, GPU_NAME, GPU_COUNT, INTERACTIVE, DRY_RUN,
#           PREFLIGHT_REPORT_FILE, CAP_PLATFORM_ID, CAP_COMPOSE_OVERLAYS,
#           ENABLE_VOICE, ENABLE_WORKFLOWS, ENABLE_RAG,
#           tier_rank(), chapter(), ai_ok(), ai_bad(), ai_warn(), log(), warn()
# Provides: REQUIREMENTS_MET, TIER_RANK
#
# Modder notes:
#   Change minimum RAM/disk thresholds per tier here.
# ============================================================================

dream_progress 25 "requirements" "Checking system requirements"
chapter "REQUIREMENTS CHECK"

[[ -f "${SCRIPT_DIR:-}/lib/safe-env.sh" ]] && . "${SCRIPT_DIR}/lib/safe-env.sh"
[[ -f "$SCRIPT_DIR/lib/service-registry.sh" ]] && . "$SCRIPT_DIR/lib/service-registry.sh"

REQUIREMENTS_MET=true
TIER_RANK="$(tier_rank "$TIER")"

# Capability-aware preflight checks
if [[ -x "$SCRIPT_DIR/scripts/preflight-engine.sh" ]]; then
    PREFLIGHT_ENV="$("$SCRIPT_DIR/scripts/preflight-engine.sh" \
        --report "$PREFLIGHT_REPORT_FILE" \
        --tier "$TIER" \
        --ram-gb "$RAM_GB" \
        --disk-gb "$DISK_AVAIL" \
        --gpu-backend "$GPU_BACKEND" \
        --gpu-vram-mb "$GPU_VRAM" \
        --gpu-name "$GPU_NAME" \
        --platform-id "${CAP_PLATFORM_ID:-linux}" \
        --compose-overlays "${CAP_COMPOSE_OVERLAYS:-}" \
        --script-dir "$SCRIPT_DIR" \
        --env 2>>"$LOG_FILE")"
    load_env_from_output <<< "$PREFLIGHT_ENV"

    log "Preflight report: $PREFLIGHT_REPORT_FILE"
    if [[ "${PREFLIGHT_BLOCKERS:-0}" -gt 0 ]]; then
        REQUIREMENTS_MET=false
        ai_bad "Preflight found ${PREFLIGHT_BLOCKERS} blocker(s) and ${PREFLIGHT_WARNINGS:-0} warning(s)."

        PYTHON_CMD="python3"
        if [[ -f "$SCRIPT_DIR/lib/python-cmd.sh" ]]; then
            . "$SCRIPT_DIR/lib/python-cmd.sh"
            PYTHON_CMD="$(ds_detect_python_cmd)"
        elif command -v python >/dev/null 2>&1; then
            PYTHON_CMD="python"
        fi

        "$PYTHON_CMD" - "$PREFLIGHT_REPORT_FILE" << 'PY'
import json
import sys

path = sys.argv[1]
try:
    data = json.load(open(path, "r", encoding="utf-8"))
except Exception:
    sys.exit(0)
for check in data.get("checks", []):
    if check.get("status") != "blocker":
        continue
    message = check.get("message", "").strip()
    action = check.get("action", "").strip()
    if message:
        print(f"  - BLOCKER: {message}")
    if action:
        print(f"    Fix: {action}")
PY
    else
        ai_ok "Preflight passed with ${PREFLIGHT_WARNINGS:-0} warning(s)."
    fi

    if [[ "${PREFLIGHT_WARNINGS:-0}" -gt 0 ]]; then
        "$PYTHON_CMD" - "$PREFLIGHT_REPORT_FILE" << 'PY'
import json
import sys

path = sys.argv[1]
try:
    data = json.load(open(path, "r", encoding="utf-8"))
except Exception:
    sys.exit(0)
for check in data.get("checks", []):
    if check.get("status") != "warn":
        continue
    message = check.get("message", "").strip()
    action = check.get("action", "").strip()
    if message:
        print(f"  - WARN: {message}")
    if action:
        print(f"    Suggestion: {action}")
PY
    fi
else
    warn "Preflight engine missing, using legacy requirement checks."
    case $TIER in
        NV_ULTRA) MIN_RAM=96 ;;
        SH_LARGE) MIN_RAM=96 ;;
        SH_COMPACT) MIN_RAM=64 ;;
        4) MIN_RAM=64 ;;
        3) MIN_RAM=48 ;;
        2) MIN_RAM=32 ;;
        0) MIN_RAM=4 ;;
        *) MIN_RAM=16 ;;
    esac
    if [[ $RAM_GB -lt $MIN_RAM ]]; then
        warn "RAM: ${RAM_GB}GB available, ${MIN_RAM}GB recommended for Tier $TIER"
    else
        ai_ok "RAM: ${RAM_GB}GB (recommended: ${MIN_RAM}GB+)"
    fi
    case $TIER in
        0) MIN_DISK=15 ;;
        1) MIN_DISK=30 ;;
        2) MIN_DISK=50 ;;
        3) MIN_DISK=80 ;;
        4) MIN_DISK=150 ;;
        *) MIN_DISK=50 ;;
    esac
    if [[ $DISK_AVAIL -lt $MIN_DISK ]]; then
        warn "Disk: ${DISK_AVAIL}GB available, ${MIN_DISK}GB minimum required for Tier $TIER"
        REQUIREMENTS_MET=false
    else
        ai_ok "Disk: ${DISK_AVAIL}GB available (minimum: ${MIN_DISK}GB for Tier $TIER)"
    fi
    if [[ "$TIER_RANK" -ge 2 && "$GPU_BACKEND" != "amd" && $GPU_VRAM -lt 10000 ]]; then
        warn "GPU: Tier $TIER requires dedicated NVIDIA GPU with 12GB+ VRAM"
    else
        ai_ok "GPU: Detected $GPU_NAME"
    fi
fi

if [[ "${LLM_MODEL_SIZE_MB:-0}" =~ ^[0-9]+$ && "${LLM_MODEL_SIZE_MB:-0}" -gt 0 && "${TIER:-}" != "CLOUD" ]]; then
    _model_disk_gb=$(( (LLM_MODEL_SIZE_MB + 1023) / 1024 ))
    _model_needed_gb=$(( _model_disk_gb + 15 ))
    if [[ "${DISK_AVAIL:-0}" -lt "$_model_needed_gb" ]]; then
        warn "Disk: ${DISK_AVAIL}GB available, ${_model_needed_gb}GB required for selected model (${_model_disk_gb}GB model + Docker images)"
        REQUIREMENTS_MET=false
    else
        ai_ok "Disk: ${DISK_AVAIL}GB available (selected model needs ~${_model_needed_gb}GB)"
    fi
fi

# Port conflict detection with process details
# Warn-once guard for missing port-check tools
_port_check_warned=false

check_port_conflict() {
    local port="$1"
    PORT_CONFLICT=false
    PORT_CONFLICT_PID=""
    PORT_CONFLICT_PROC=""

    # Try lsof first (most reliable for getting process info)
    if command -v lsof &> /dev/null; then
        if lsof -i ":${port}" -sTCP:LISTEN >/dev/null 2>&1; then
            PORT_CONFLICT_PID=$(lsof -t -i ":${port}" -sTCP:LISTEN 2>/dev/null | head -1)
            PORT_CONFLICT_PROC=$(ps -p "$PORT_CONFLICT_PID" -o comm= 2>/dev/null || echo "unknown")
            PORT_CONFLICT=true
            return 0
        fi
    # Fallback to ss (faster but less detailed)
    elif command -v ss &> /dev/null; then
        if ss -tln 2>/dev/null | grep -qE ":${port}(\s|$)"; then
            # Try to extract PID from ss output (format: users:(("process",pid=1234,fd=5)))
            local ss_line
            ss_line=$(ss -tlnp 2>/dev/null | grep -E ":${port}(\s|$)" | head -1)
            if [[ "$ss_line" =~ pid=([0-9]+) ]]; then
                PORT_CONFLICT_PID="${BASH_REMATCH[1]}"
                PORT_CONFLICT_PROC=$(ps -p "$PORT_CONFLICT_PID" -o comm= 2>/dev/null || echo "unknown")
            else
                PORT_CONFLICT_PROC="unknown"
            fi
            PORT_CONFLICT=true
            return 0
        fi
    # Fallback to netstat
    elif command -v netstat &> /dev/null; then
        if netstat -tln 2>/dev/null | grep -qE ":${port}(\s|$)"; then
            # netstat -tlnp requires root, so we may not get PID
            local netstat_line
            netstat_line=$(netstat -tlnp 2>/dev/null | grep -E ":${port}(\s|$)" | head -1)
            if [[ "$netstat_line" =~ ([0-9]+)/([^ ]+) ]]; then
                PORT_CONFLICT_PID="${BASH_REMATCH[1]}"
                PORT_CONFLICT_PROC="${BASH_REMATCH[2]}"
            else
                PORT_CONFLICT_PROC="unknown"
            fi
            PORT_CONFLICT=true
            return 0
        fi
    else
        # No tools available
        if [[ "${_port_check_warned}" != "true" ]]; then
            _port_check_warned=true
            warn "Neither 'lsof', 'ss', nor 'netstat' found — cannot verify port availability"
            warn "Install lsof, iproute2 (for ss), or net-tools (for netstat) to enable port checks"
        fi
        return 1
    fi

    return 1
}

# Ollama conflict detection
check_ollama_conflict() {
    OLLAMA_RUNNING=false
    OLLAMA_PID=""

    if pgrep -x ollama >/dev/null 2>&1; then
        OLLAMA_RUNNING=true
        OLLAMA_PID=$(pgrep -x ollama | head -1)
    fi
}

# Ollama conflict detection (must happen before port checks)
check_ollama_conflict
if $OLLAMA_RUNNING; then
    ai_warn "Ollama is running (PID ${OLLAMA_PID}) and may conflict with Dream Server."
    ai "  Note: this is usually not a port collision. Open WebUI may auto-discover Ollama (11434) and prefer it over the local llama-server (8080)."
    if $INTERACTIVE && ! $DRY_RUN; then
        read -r -p "  Stop Ollama for this session? [Y/n] " ollama_choice < /dev/tty
        if [[ ! "$ollama_choice" =~ ^[nN] ]]; then
            kill "$OLLAMA_PID" 2>/dev/null || sudo kill "$OLLAMA_PID" 2>/dev/null || true
            sleep 2
            if pgrep -x ollama >/dev/null 2>&1; then
                ai_warn "Ollama restarted automatically. Stop it manually: sudo systemctl stop ollama"
            else
                ai_ok "Ollama stopped"
            fi
        else
            ai_warn "Ollama left running. Port conflicts may occur."
        fi
    else
        ai_warn "Ollama detected. Run without --non-interactive to resolve, or stop manually: sudo systemctl stop ollama"
    fi
fi

# Port conflict detection with detailed process information
PORTS_TO_CHECK="${SERVICE_PORTS[llama-server]:-8080} ${SERVICE_PORTS[open-webui]:-3000}"
[[ "$ENABLE_VOICE" == "true" ]] && PORTS_TO_CHECK="$PORTS_TO_CHECK ${SERVICE_PORTS[whisper]:-9000} ${SERVICE_PORTS[tts]:-8880}"
[[ "$ENABLE_WORKFLOWS" == "true" ]] && PORTS_TO_CHECK="$PORTS_TO_CHECK ${SERVICE_PORTS[n8n]:-5678}"
[[ "$ENABLE_RAG" == "true" ]] && PORTS_TO_CHECK="$PORTS_TO_CHECK ${SERVICE_PORTS[qdrant]:-6333}"
[[ "$ENABLE_COMFYUI" == "true" ]] && PORTS_TO_CHECK="$PORTS_TO_CHECK ${SERVICE_PORTS[comfyui]:-8188}"

for port in $PORTS_TO_CHECK; do
    if check_port_conflict "$port"; then
        if [[ -n "$PORT_CONFLICT_PID" ]]; then
            warn "Port $port is in use by ${PORT_CONFLICT_PROC} (PID ${PORT_CONFLICT_PID})"
        else
            warn "Port $port is in use by ${PORT_CONFLICT_PROC}"
        fi
        REQUIREMENTS_MET=false
    fi
done

if [[ "$REQUIREMENTS_MET" != "true" ]]; then
    warn "Some requirements not met. Installation may have limited functionality."
    if $INTERACTIVE && ! $DRY_RUN; then
        read -p "  Continue anyway? [y/N] " -r < /dev/tty
        [[ ! $REPLY =~ ^[Yy]$ ]] && exit 1
    elif $DRY_RUN; then
        log "[DRY RUN] Would prompt to continue despite unmet requirements"
    fi
fi
