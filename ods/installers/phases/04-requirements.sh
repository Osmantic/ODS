#!/bin/bash
# ============================================================================
# ODS Installer — Phase 04: Requirements Check
# ============================================================================
# Part of: installers/phases/
# Purpose: RAM, disk, and GPU requirement checks
#
# Expects: SCRIPT_DIR, LOG_FILE, TIER, RAM_GB, DISK_AVAIL, GPU_BACKEND,
#           GPU_VRAM, GPU_NAME, GPU_COUNT, INTERACTIVE, DRY_RUN,
#           PREFLIGHT_REPORT_FILE, CAP_PLATFORM_ID, CAP_COMPOSE_OVERLAYS,
#           ENABLE_VOICE, ENABLE_WORKFLOWS, ENABLE_RAG, ENABLE_QDRANT,
#           tier_rank(), chapter(), ai_ok(), ai_bad(), ai_warn(), log(), warn()
# Provides: REQUIREMENTS_MET, TIER_RANK
#
# Modder notes:
#   Change minimum RAM/disk thresholds per tier here.
# ============================================================================

ods_progress 25 "requirements" "Checking system requirements"
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

_phase04_lemonade_uses_host_9000() {
    [[ "${LEMONADE_EXTERNAL:-false}" =~ ^([Tt][Rr][Uu][Ee]|1|yes|on)$ ]] && return 0
    [[ "${AMD_INFERENCE_RUNTIME:-}" =~ ^([Ll][Ee][Mm][Oo][Nn][Aa][Dd][Ee])$ ]] && return 0
    [[ "${GPU_BACKEND:-}" == "amd" && "${ODS_MODE:-local}" != "cloud" ]] && return 0
    return 1
}

if [[ "${ENABLE_VOICE:-false}" == "true" ]] && _phase04_lemonade_uses_host_9000; then
    _whisper_port_for_check="${WHISPER_PORT:-}"
    if [[ -z "$_whisper_port_for_check" ]] \
        && declare -f ods_install_env_value >/dev/null 2>&1
    then
        _whisper_port_for_check="$(
            ods_install_env_value "$INSTALL_DIR/.env" WHISPER_PORT \
                "${SERVICE_PORTS[whisper]:-9000}"
        )"
    fi
    _whisper_port_for_check="${_whisper_port_for_check:-${SERVICE_PORTS[whisper]:-9000}}"
    if [[ "$_whisper_port_for_check" == "9000" ]]; then
        # Lemonade's native router can reserve host port 9000 on AMD systems.
        # Keep Whisper's container port unchanged, but use 9100 on the host
        # unless the user explicitly selected another non-9000 port.
        WHISPER_PORT=9100
        SERVICE_PORTS[whisper]=9100
        log "AMD/Lemonade detected; reserving host port 9000 for Lemonade and selecting Whisper port 9100"
    fi
    unset _whisper_port_for_check
fi

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
