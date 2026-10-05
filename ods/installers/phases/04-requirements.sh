#!/bin/bash
# ============================================================================
# ODS Installer — Phase 04: Requirements Check
# ============================================================================
# Part of: installers/phases/
# Purpose: RAM, disk, GPU, and port availability checks
#
# Expects: SCRIPT_DIR, LOG_FILE, TIER, RAM_GB, DISK_AVAIL, GPU_BACKEND,
#           GPU_VRAM, GPU_NAME, GPU_COUNT, INTERACTIVE, DRY_RUN,
#           PREFLIGHT_REPORT_FILE, CAP_PLATFORM_ID, CAP_COMPOSE_OVERLAYS,
#           ENABLE_VOICE, ENABLE_WORKFLOWS, ENABLE_RAG, ENABLE_QDRANT,
#           INSTALL_DIR, external_llm_env_value(),
#           tier_rank(), chapter(), ai_ok(), ai_bad(), ai_warn(), log(), warn()
# Provides: REQUIREMENTS_MET, TIER_RANK, WHISPER_PORT (only when the
#           generated default 9000 moves; phase 06 persists it)
#
# Modder notes:
#   Change minimum RAM/disk thresholds per tier here.
# ============================================================================

# Isolated phase reuse (tests) gets the route predicate installers/lib/
# native-llm.sh gives install-core: a host-native llama-server is in use.
declare -F ods_native_llm_requested >/dev/null 2>&1 \
    || ods_native_llm_requested() { [[ -n "${NATIVE_LLM_BASE_URL:-}" ]]; }

ods_progress 25 "requirements" "Checking system requirements"
chapter "REQUIREMENTS CHECK"

[[ -f "${SCRIPT_DIR:-}/lib/safe-env.sh" ]] && . "${SCRIPT_DIR}/lib/safe-env.sh"
[[ -f "$SCRIPT_DIR/lib/service-registry.sh" ]] && . "$SCRIPT_DIR/lib/service-registry.sh"

REQUIREMENTS_MET=true
TIER_RANK="$(tier_rank "$TIER")"

# Capability-aware preflight checks
if [[ -x "$SCRIPT_DIR/scripts/preflight-engine.sh" ]]; then
    PREFLIGHT_ENV="$(NATIVE_LLM_BASE_URL="${NATIVE_LLM_BASE_URL:-}" \
        NATIVE_LLM_GPU_NAME="${NATIVE_LLM_GPU_NAME:-}" \
        NATIVE_LLM_GPU_VRAM_MB="${NATIVE_LLM_GPU_VRAM_MB:-0}" \
        "$SCRIPT_DIR/scripts/preflight-engine.sh" \
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
    if [[ -n "$PREFLIGHT_ENV" ]]; then
        load_env_from_output <<< "$PREFLIGHT_ENV"
    fi

    log "Preflight report: $PREFLIGHT_REPORT_FILE"
    if [[ "${PREFLIGHT_BLOCKERS:-0}" -gt 0 ]]; then
        REQUIREMENTS_MET=false
        ai_bad "Preflight found ${PREFLIGHT_BLOCKERS} blocker(s) and ${PREFLIGHT_WARNINGS:-0} warning(s)."

        PYTHON_CMD="python3"
        if [[ -f "$SCRIPT_DIR/lib/python-cmd.sh" ]]; then
            . "$SCRIPT_DIR/lib/python-cmd.sh"
            PYTHON_CMD="$(ods_detect_python_cmd)"
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

# A host-native llama-server keeps its model on the Windows host (phase 11
# never downloads it here), so only the images need room on this disk.
if [[ -z "${EXTERNAL_LLM_URL:-}" ]] && ! ods_native_llm_requested \
      && [[ "${LLM_MODEL_SIZE_MB:-0}" =~ ^[0-9]+$ && "${LLM_MODEL_SIZE_MB:-0}" -gt 0 && "${TIER:-}" != "CLOUD" ]]; then
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

_phase04_current_install_owns_docker_port() {
    local port="${1:-}" container_id ownership working_dir project_name
    [[ "$port" =~ ^[0-9]+$ ]] || return 1
    command -v docker >/dev/null 2>&1 || return 1

    # An in-place upgrade is allowed to reuse ports already published by the
    # fixed ODS Compose project. Compose can retain an unchanged container's
    # original working-directory label after an install-directory migration,
    # so accept either the exact directory or the canonical project identity.
    # Containers from another Compose project remain genuine conflicts.
    while IFS= read -r container_id; do
        [[ -n "$container_id" ]] || continue
        ownership=$(docker inspect --format \
            '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}|{{ index .Config.Labels "com.docker.compose.project" }}' \
            "$container_id" 2>/dev/null || true)
        working_dir="${ownership%%|*}"
        project_name="${ownership#*|}"
        [[ -n "$working_dir" && "$working_dir" == "${INSTALL_DIR:-}" ]] && return 0
        [[ "$project_name" == "${COMPOSE_PROJECT_NAME:-ods}" ]] && return 0
    done < <(docker ps --filter "publish=${port}" --format '{{.ID}}' 2>/dev/null || true)

    return 1
}

# Docker Desktop publishes a running container's port through a listener on
# the Windows side, so this installation's own Whisper looks like a Windows
# program holding its host port. Nothing before phase 11 stops or recreates
# the stack, so on a rerun the previous Whisper is still running here. Read
# that container's host bindings without changing anything. Its name is fixed
# by extensions/services/whisper/compose.yaml, and its Compose identity is
# accepted as in _phase04_current_install_owns_docker_port. That helper cannot
# find it: Docker's publish filter matches the container side of a published
# port, which for Whisper is 8000.
_phase04_own_whisper_publishes() {
    local port="${1:-}" inspected running project_name host_ports working_dir
    [[ "$port" =~ ^[0-9]+$ ]] || return 1
    command -v docker >/dev/null 2>&1 || return 1
    # A missing container, an unreachable Docker daemon or no Docker at all
    # finds nothing. The callers then treat a held port as taken, as they did
    # before this check existed.
    inspected="$(docker container inspect --format \
        '{{.State.Running}}|{{ index .Config.Labels "com.docker.compose.project" }}|{{range $p, $conf := .NetworkSettings.Ports}}{{range $conf}}{{.HostPort}} {{end}}{{end}}|{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' \
        ods-whisper 2>/dev/null)" || return 1
    IFS='|' read -r running project_name host_ports working_dir <<< "$inspected"
    [[ "$running" == "true" ]] || return 1
    [[ ( -n "$working_dir" && "$working_dir" == "${INSTALL_DIR:-}" ) \
        || "$project_name" == "${COMPOSE_PROJECT_NAME:-ods}" ]] || return 1
    [[ " $host_ports " == *" $port "* ]]
}

check_port_conflict() {
    local port="$1"
    PORT_CONFLICT=false
    PORT_CONFLICT_PID=""
    PORT_CONFLICT_PROC=""
    local port_tool_found=false

    if _phase04_current_install_owns_docker_port "$port"; then
        return 1
    fi

    # Try lsof first (most reliable for getting process info)
    if command -v lsof &> /dev/null; then
        port_tool_found=true
        if lsof -i ":${port}" -sTCP:LISTEN >/dev/null 2>&1; then
            PORT_CONFLICT_PID=$(lsof -t -i ":${port}" -sTCP:LISTEN 2>/dev/null | head -1)
            PORT_CONFLICT_PROC=$(ps -p "$PORT_CONFLICT_PID" -o comm= 2>/dev/null || echo "unknown")
            PORT_CONFLICT=true
            return 0
        fi
    fi

    # Fallback to ss (faster but less detailed). Keep trying when lsof exists
    # but cannot observe the listener, which can happen under restricted users.
    if command -v ss &> /dev/null; then
        port_tool_found=true
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
    fi

    # Last fallback to netstat.
    if command -v netstat &> /dev/null; then
        port_tool_found=true
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
    fi

    if [[ "$port_tool_found" != "true" ]]; then
        # No tools available
        if [[ "${_port_check_warned}" != "true" ]]; then
            _port_check_warned=true
            warn "Neither 'lsof', 'ss', nor 'netstat' found — cannot verify port availability"
            warn "Install lsof, iproute2 (for ss), or net-tools (for netstat) to enable port checks"
        fi
    fi

    # Docker Desktop publishes WSL ports through Windows. Native Windows
    # listeners are invisible to Linux lsof/ss/netstat but still make the
    # eventual Docker bind fail.
    if declare -F ods_windows_host_port_in_use >/dev/null 2>&1 \
        && ods_windows_host_port_in_use "$port"; then
        PORT_CONFLICT_PROC="Windows host process"
        PORT_CONFLICT=true
        return 0
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
if $OLLAMA_RUNNING && [[ "${EXTERNAL_LLM_PROVIDER:-}" != "ollama" ]]; then
    ai_warn "Ollama is running (PID ${OLLAMA_PID}) and may conflict with ODS."
    ai "  Note: this is usually not a port collision. Open WebUI may auto-discover Ollama (11434) and prefer it over the local llama-server (8080)."
    if $INTERACTIVE && ! $DRY_RUN; then
        ollama_choice=""
        if [[ -r /dev/tty ]]; then
            read -r -p "  Stop Ollama for this session? [Y/n] " ollama_choice < /dev/tty
        else
            read -r -p "  Stop Ollama for this session? [Y/n] " ollama_choice || true
        fi
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

# Phase 06 writes WHISPER_PORT from this shell first, then from the installed
# .env, then 9000. Decide and check with that same port, so a rerun leaves a
# port the owner chose, or the 9100 earlier AMD installs moved Whisper to,
# untouched. A retained 9000 is the generated default: earlier installers
# wrote it whenever no port was chosen.
_whisper_configured_port="${WHISPER_PORT:-}"
if [[ -z "$_whisper_configured_port" && -f "${INSTALL_DIR:-}/.env" ]]; then
    _whisper_configured_port="$(external_llm_env_value "$INSTALL_DIR/.env" WHISPER_PORT)"
fi
[[ -z "$_whisper_configured_port" ]] || SERVICE_PORTS[whisper]="$_whisper_configured_port"
unset _whisper_configured_port

# A native Windows application can own 9000 even when WSL reports it free.
# For the generated Whisper default, select ODS's established alternate only
# when it is also free on both sides of the WSL boundary. Explicit non-default
# ports remain untouched and are reported by the normal conflict loop below.
# Choose it with voice off too: Whisper can be added from the Extensions
# Library later, and it then publishes the port this install writes. A rerun
# keeps 9000 while this installation's own Whisper is the one publishing it.
_whisper_port_for_check="${WHISPER_PORT:-${SERVICE_PORTS[whisper]:-9000}}"
if [[ "$_whisper_port_for_check" == "9000" ]] \
    && declare -F ods_windows_host_port_in_use >/dev/null 2>&1 \
    && ods_windows_host_port_in_use 9000 \
    && ! _phase04_own_whisper_publishes 9000; then
    _whisper_alternate=""
    for _whisper_candidate in 9100 9001; do
        if ! check_port_conflict "$_whisper_candidate"; then
            _whisper_alternate="$_whisper_candidate"
            break
        fi
    done
    if [[ -n "$_whisper_alternate" ]]; then
        WHISPER_PORT="$_whisper_alternate"
        SERVICE_PORTS[whisper]="$_whisper_alternate"
        log "Windows host port 9000 is occupied; checking Whisper on ${_whisper_alternate}"
    else
        warn "Windows host port 9000 is occupied and Whisper alternates 9100 and 9001 are unavailable"
    fi
    unset _whisper_alternate _whisper_candidate
fi
unset _whisper_port_for_check

# Port conflict detection with detailed process information
PORTS_TO_CHECK=""
[[ "${ENABLE_OPEN_WEBUI:-true}" != "true" ]] || PORTS_TO_CHECK="${SERVICE_PORTS[open-webui]:-3000}"
# A host-native llama-server (Windows Portal) owns its own port; the in-stack
# llama-server does not run then.
if [[ -z "${EXTERNAL_LLM_URL:-}" ]] && ! ods_native_llm_requested; then
    PORTS_TO_CHECK="${SERVICE_PORTS[llama-server]:-8080} ${PORTS_TO_CHECK}"
fi
if [[ "$ENABLE_VOICE" == "true" ]]; then
    # A rerun's running Whisper holds its own port; that is not a conflict.
    _phase04_own_whisper_publishes "${SERVICE_PORTS[whisper]:-9000}" \
        || PORTS_TO_CHECK="$PORTS_TO_CHECK ${SERVICE_PORTS[whisper]:-9000}"
    PORTS_TO_CHECK="$PORTS_TO_CHECK ${SERVICE_PORTS[tts]:-8880}"
fi
[[ "$ENABLE_WORKFLOWS" == "true" ]] && PORTS_TO_CHECK="$PORTS_TO_CHECK ${SERVICE_PORTS[n8n]:-5678}"
[[ "${ENABLE_QDRANT:-${ENABLE_RAG:-false}}" == "true" ]] && PORTS_TO_CHECK="$PORTS_TO_CHECK ${SERVICE_PORTS[qdrant]:-6333}"
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
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
        warn "Continuing despite unmet requirements at user request."
    elif $DRY_RUN; then
        log "[DRY RUN] Would prompt to continue despite unmet requirements"
    fi
fi

# This file is sourced by install-core.sh under `set -e`. Keep the phase's
# final status successful when the user explicitly chose to continue; otherwise
# a false [[ ... ]] test in the prompt branch can make `source phase-04` return
# 1 and trip the top-level error trap.
true
