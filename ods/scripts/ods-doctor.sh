#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
    echo "Usage: $0 [REPORT_PATH]"
    echo "       $0 --help"
    echo ""
    echo "Generates a machine-readable diagnostics report for installer and runtime readiness."
    echo "Report includes capability profile, preflight-style analysis, and autofix_hints."
    echo ""
    echo "Arguments:"
    echo "  REPORT_PATH  Output JSON path (default: /tmp/ods-doctor-report.json)"
    echo ""
    echo "Exit codes: 0 = report generated, 1 = error (e.g. missing dependency)"
    echo ""
    echo "See docs/ODS-DOCTOR.md for details."
}
case "${1:-}" in
    -h|--help) usage; exit 0 ;;
esac

REPORT_FILE="${1:-/tmp/ods-doctor-report.json}"

CAP_FILE="/tmp/ods-doctor-capabilities.json"
PREFLIGHT_FILE="/tmp/ods-doctor-preflight.json"
DOCTOR_BASH_CMD="${BASH:-}"
if [[ -z "$DOCTOR_BASH_CMD" || ! -x "$DOCTOR_BASH_CMD" ]]; then
    DOCTOR_BASH_CMD="$(command -v bash 2>/dev/null || printf '%s\n' bash)"
fi

# Source service registry and safe env helpers
if [[ -f "$ROOT_DIR/lib/service-registry.sh" ]]; then
    export SCRIPT_DIR="$ROOT_DIR"
    . "$ROOT_DIR/lib/service-registry.sh"
    sr_load
fi
if [[ -f "$ROOT_DIR/lib/safe-env.sh" ]]; then
    . "$ROOT_DIR/lib/safe-env.sh"
fi

# Safe .env loading (no direct source to avoid injection)
load_env_safe() {
    local env_file="${1:-$ROOT_DIR/.env}"
    [[ -f "$env_file" ]] || return 0
    while IFS='=' read -r key value; do
        [[ "$key" =~ ^[[:space:]]*# ]] && continue
        [[ -z "$key" ]] && continue
        [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        value="${value%\"}"
        value="${value#\"}"
        value="${value%\'}"
        value="${value#\'}"
        export "$key=$value"
    done < "$env_file"
}
load_env_safe "$ROOT_DIR/.env"
sr_resolve_ports
_DASHBOARD_PORT="${SERVICE_PORTS[dashboard]:-3001}"
_WEBUI_PORT="${SERVICE_PORTS[open-webui]:-3000}"

# RAM: platform-branch. /proc/meminfo does not exist on macOS; use sysctl.
if [[ "$(uname -s)" == "Darwin" ]]; then
    RAM_BYTES="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
    RAM_GB=$(( RAM_BYTES / 1024 / 1024 / 1024 ))
else
    RAM_GB="$(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print int($2/1024/1024)}' || echo 0)"
fi
# Installer-recorded fallback: if detection returned 0 and .env has HOST_RAM_GB, trust that.
if (( RAM_GB == 0 )) && [[ -f "$ROOT_DIR/.env" ]]; then
    _env_ram=$(grep '^HOST_RAM_GB=' "$ROOT_DIR/.env" | cut -d= -f2 | tr -d '"' || true)
    [[ -n "${_env_ram:-}" ]] && RAM_GB="$_env_ram"
fi

# Disk: POSIX df -k — works on BSD and GNU identically (df -BG is GNU-only).
DISK_GB="$(df -k "$HOME" 2>/dev/null | tail -1 | awk '{print int($4/1024/1024)}' || echo 0)"

if [[ -x "$SCRIPT_DIR/scripts/build-capability-profile.sh" ]]; then
    CAP_ENV="$("$DOCTOR_BASH_CMD" "$SCRIPT_DIR/scripts/build-capability-profile.sh" --output "$CAP_FILE" --env)"
    load_env_from_output <<< "$CAP_ENV"
else
    echo "scripts/build-capability-profile.sh not found/executable" >&2
    exit 1
fi

if [[ -x "$SCRIPT_DIR/scripts/preflight-engine.sh" ]]; then
    PREFLIGHT_ENV="$("$DOCTOR_BASH_CMD" "$SCRIPT_DIR/scripts/preflight-engine.sh" \
        --report "$PREFLIGHT_FILE" \
        --tier "${CAP_RECOMMENDED_TIER:-T1}" \
        --ram-gb "$RAM_GB" \
        --disk-gb "$DISK_GB" \
        --gpu-backend "${CAP_LLM_BACKEND:-cpu}" \
        --gpu-vram-mb "${CAP_GPU_VRAM_MB:-0}" \
        --gpu-name "${CAP_GPU_NAME:-Unknown}" \
        --platform-id "${CAP_PLATFORM_ID:-unknown}" \
        --compose-overlays "${CAP_COMPOSE_OVERLAYS:-}" \
        --script-dir "$ROOT_DIR" \
        --env)"
    load_env_from_output <<< "$PREFLIGHT_ENV"
else
    echo "scripts/preflight-engine.sh not found/executable" >&2
    exit 1
fi

DOCKER_CLI="false"
DOCKER_DAEMON="false"
COMPOSE_CLI="false"
DASHBOARD_HTTP="false"
WEBUI_HTTP="false"

# Extension diagnostics (JSON array of objects)
EXT_DIAGNOSTICS="[]"

if command -v docker >/dev/null 2>&1; then
    DOCKER_CLI="true"
    if docker info >/dev/null 2>&1; then
        DOCKER_DAEMON="true"
    fi
    if docker compose version >/dev/null 2>&1 || command -v docker-compose >/dev/null 2>&1; then
        COMPOSE_CLI="true"
    fi
fi

if command -v curl >/dev/null 2>&1; then
    if curl -sf --max-time 10 "http://127.0.0.1:${_DASHBOARD_PORT}" >/dev/null 2>&1; then
        DASHBOARD_HTTP="true"
    fi
    if curl -sf --max-time 10 "http://127.0.0.1:${_WEBUI_PORT}" >/dev/null 2>&1; then
        WEBUI_HTTP="true"
    fi
fi

# STT model cache check: a common silent-failure mode is the installer's
# pre-download failing, so Whisper's /health passes (service up) but the
# model isn't cached. Transcription then returns 404. This check catches
# that case and surfaces the exact recovery command.
STT_MODEL_CACHED="unknown"
STT_MODEL_NAME=""
STT_RECOVERY_HINT=""
TTS_HTTP="unknown"
TTS_PORT=""
if [[ "${ENABLE_VOICE:-false}" == "true" ]] && command -v curl >/dev/null 2>&1; then
    STT_MODEL_NAME="${AUDIO_STT_MODEL:-Systran/faster-whisper-base}"
    _stt_whisper_port="${SERVICE_PORTS[whisper]:-9000}"
    _stt_model_encoded="${STT_MODEL_NAME//\//%2F}"
    _stt_whisper_url="http://127.0.0.1:${_stt_whisper_port}"
    if curl -sf --max-time 5 "${_stt_whisper_url}/v1/models/${_stt_model_encoded}" >/dev/null 2>&1; then
        STT_MODEL_CACHED="true"
    else
        # Distinguish "service down" from "model missing" for the hint.
        if curl -sf --max-time 5 "${_stt_whisper_url}/health" >/dev/null 2>&1; then
            STT_MODEL_CACHED="false"
            STT_RECOVERY_HINT="curl --max-time 3600 -X POST ${_stt_whisper_url}/v1/models/${_stt_model_encoded}"
        else
            STT_MODEL_CACHED="service_down"
        fi
    fi

    TTS_PORT="${SERVICE_PORTS[tts]:-8880}"
    if curl -sf --max-time 5 "http://127.0.0.1:${TTS_PORT}/health" >/dev/null 2>&1; then
        TTS_HTTP="true"
    else
        TTS_HTTP="false"
    fi
elif [[ "${ENABLE_VOICE:-false}" != "true" ]]; then
    STT_MODEL_CACHED="disabled"
    TTS_HTTP="disabled"
fi

# DGX Spark / GB10 CUDA arch check. Generic llama.cpp CUDA images can run on
# GB10 while missing sm_121 support, which has been observed to produce
# syntactically valid but unusable model output. Surface that mismatch in
# doctor so operators do not have to infer it from llama-server logs.
DGX_SPARK_GPU="false"
DGX_SPARK_GPU_NAME=""
DGX_SPARK_COMPUTE_CAP=""
LLAMA_CUDA_ARCHS=""
DGX_SPARK_CUDA_ARCH_STATUS="unknown"
DGX_SPARK_CUDA_ARCH_MESSAGE=""
_doctor_gpu_backend="${GPU_BACKEND:-${CAP_LLM_BACKEND:-}}"
if [[ "$_doctor_gpu_backend" == "nvidia" ]] && command -v nvidia-smi >/dev/null 2>&1; then
    _dgx_gpu_raw="$(nvidia-smi --query-gpu=name,compute_cap --format=csv,noheader,nounits 2>/dev/null | head -1 || true)"
    if [[ -n "$_dgx_gpu_raw" ]]; then
        DGX_SPARK_GPU_NAME="$(echo "$_dgx_gpu_raw" | cut -d',' -f1 | xargs)"
        DGX_SPARK_COMPUTE_CAP="$(echo "$_dgx_gpu_raw" | cut -d',' -f2 | xargs)"
        if [[ "$DGX_SPARK_GPU_NAME" == *"GB10"* || "$DGX_SPARK_COMPUTE_CAP" == "12.1" ]]; then
            DGX_SPARK_GPU="true"
            if [[ "$DOCKER_DAEMON" == "true" ]] && docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx 'ods-llama-server'; then
                _llama_arch_line="$(docker logs ods-llama-server 2>&1 | grep 'CUDA : ARCHS =' | tail -1 || true)"
                LLAMA_CUDA_ARCHS="$(echo "$_llama_arch_line" | sed -n 's/.*CUDA : ARCHS = \([^|]*\).*/\1/p' | xargs)"
                if [[ -z "$LLAMA_CUDA_ARCHS" ]]; then
                    DGX_SPARK_CUDA_ARCH_STATUS="unknown"
                    DGX_SPARK_CUDA_ARCH_MESSAGE="DGX Spark detected, but llama-server CUDA archs were not found in logs."
                elif [[ ",${LLAMA_CUDA_ARCHS}," == *",1210,"* || ",${LLAMA_CUDA_ARCHS}," == *",121,"* || ",${LLAMA_CUDA_ARCHS}," == *",121a,"* ]]; then
                    DGX_SPARK_CUDA_ARCH_STATUS="pass"
                    DGX_SPARK_CUDA_ARCH_MESSAGE="DGX Spark llama-server binary includes sm_121 support."
                else
                    DGX_SPARK_CUDA_ARCH_STATUS="warn"
                    DGX_SPARK_CUDA_ARCH_MESSAGE="DGX Spark detected, but llama-server reports CUDA archs '${LLAMA_CUDA_ARCHS}' without sm_121."
                fi
            else
                DGX_SPARK_CUDA_ARCH_STATUS="unknown"
                DGX_SPARK_CUDA_ARCH_MESSAGE="DGX Spark detected, but ods-llama-server is not available for CUDA arch inspection."
            fi
        fi
    fi
fi

# Collect extension diagnostics (wrapped in function to allow local variables)
collect_extension_diagnostics() {
    # Use outer GPU_BACKEND or default to nvidia (don't make local to avoid set -u issues)
    local backend="${GPU_BACKEND-nvidia}"
    local EXT_DIAG_ITEMS=()

    for sid in "${SERVICE_IDS[@]}"; do
        # Skip core services
        [[ "${SERVICE_CATEGORIES[$sid]:-}" == "core" ]] && continue

        # Check if extension is enabled
        local compose_file="${SERVICE_COMPOSE[$sid]:-}"
        [[ -z "$compose_file" || ! -f "$compose_file" ]] && continue

        # Build diagnostic entry
        local container="${SERVICE_CONTAINERS[$sid]:-}"
        local container_state="unknown"
        local health_status="unknown"
        local issues=()

        # Check container state
        if [[ "$DOCKER_DAEMON" == "true" && -n "$container" ]]; then
            local inspect_output
            inspect_output=$(docker inspect --format '{{.State.Status}}' "$container" 2>&1)
            if [[ $? -eq 0 ]]; then
                container_state="$inspect_output"
            else
                container_state="not_found"
            fi

            # Check health endpoint if container running
            if [[ "$container_state" == "running" ]]; then
                local port="${SERVICE_PORTS[$sid]:-0}"
                local health="${SERVICE_HEALTH[$sid]:-}"
                if [[ "$port" != "0" && -n "$health" ]]; then
                    if curl -sf --max-time 5 "http://127.0.0.1:${port}${health}" >/dev/null 2>&1; then
                        health_status="healthy"
                    else
                        health_status="unhealthy"
                        issues+=("health_check_failed")
                    fi
                fi
            else
                issues+=("container_not_running")
            fi
        fi

        # Check GPU backend compatibility (only if SERVICE_GPU_BACKENDS array exists from PR #357).
        # dashboard-api uses GPU_BACKEND=nvidia internally on macOS (see
        # installers/macos/docker-compose.macos.yml) so service manifests are
        # discovered. doctor/preflight path doesn't have that workaround, so the
        # raw gpu_backends check produces false positives for CPU-only services
        # declaring gpu_backends: [amd, nvidia]. Skip the check on apple — if a
        # service genuinely needs GPU and isn't available on Apple, it's a
        # manifest-level concern, not a runtime doctor warning.
        if [[ "$backend" != "apple" ]] && declare -p SERVICE_GPU_BACKENDS &>/dev/null; then
            local gpu_backends="${SERVICE_GPU_BACKENDS[$sid]:-}"
            if [[ -n "$gpu_backends" \
                && ! " $gpu_backends " =~ " all " \
                && ! " $gpu_backends " =~ " $backend " ]]; then
                issues+=("gpu_backend_incompatible")
            fi
        fi

        # Check dependencies
        local deps="${SERVICE_DEPENDS[$sid]:-}"
        if [[ -n "$deps" ]]; then
            local dep
            for dep in $deps; do
                local dep_compose="${SERVICE_COMPOSE[$dep]:-}"
                local dep_cat="${SERVICE_CATEGORIES[$dep]:-}"
                if [[ "$dep_cat" != "core" && ! -f "$dep_compose" ]]; then
                    issues+=("missing_dependency:$dep")
                fi
            done
        fi

        # Build JSON object (escape quotes in values)
        local issues_json="[]"
        if [[ ${#issues[@]} -gt 0 ]]; then
            # Use printf with newline separator, then convert to JSON array
            issues_json="[\"$(printf '%s\n' "${issues[@]}" | sed 's/"/\\"/g' | tr '\n' ',' | sed 's/,$//' | sed 's/,/","/g')\"]"
        fi

        EXT_DIAG_ITEMS+=("{\"id\":\"$sid\",\"container_state\":\"$container_state\",\"health_status\":\"$health_status\",\"issues\":$issues_json}")
    done

    if [[ ${#EXT_DIAG_ITEMS[@]} -gt 0 ]]; then
        echo "[$(IFS=,; echo "${EXT_DIAG_ITEMS[*]}")]"
    else
        echo "[]"
    fi
}

# Hermes slash workers are spawned by upstream Hermes for /slash commands. A
# leak in that worker lifecycle can create many long-lived child processes and
# starve local models of RAM. Doctor only reports; cleanup remains explicit via
# `ods repair hermes-workers`.
HERMES_SLASH_WORKER_MAX_COUNT="${HERMES_SLASH_WORKER_MAX_COUNT:-8}"
HERMES_SLASH_WORKER_COUNT="0"
if [[ "$DOCKER_DAEMON" == "true" ]] && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'ods-hermes'; then
    _hermes_worker_count="$(
        docker exec ods-hermes sh -c \
            "(ps -eo args= 2>/dev/null || ps -ef 2>/dev/null) | grep '[t]ui_gateway[.]slash_worker' | wc -l" \
            2>/dev/null || echo 0
    )"
    HERMES_SLASH_WORKER_COUNT="$(echo "$_hermes_worker_count" | tr -dc '0-9')"
    [[ -n "$HERMES_SLASH_WORKER_COUNT" ]] || HERMES_SLASH_WORKER_COUNT="0"
fi

ODS_MANAGED_CONTAINER_COUNT="0"
ODS_RUNNING_CONTAINER_COUNT="0"
if [[ "$DOCKER_DAEMON" == "true" ]]; then
    ODS_MANAGED_CONTAINER_COUNT="$(
        docker ps -a --format '{{.Names}}' 2>/dev/null | grep -c '^ods-' || true
    )"
    ODS_RUNNING_CONTAINER_COUNT="$(
        docker ps --format '{{.Names}}' 2>/dev/null | grep -c '^ods-' || true
    )"
fi

# Rootless Docker subordinate ID ranges — runtime half of the install
# preflight's ROOTLESS_SUBID check. A rootless daemon maps in-container
# users through /etc/subuid + /etc/subgid; a missing entry or a range under
# 65536 IDs breaks layer extraction for non-root images with cryptic
# "uid/gid map out of range" errors, so doctor surfaces it with the fix.
ROOTLESS_DOCKER="false"
ROOTLESS_SUBID_STATUS="skipped"
ROOTLESS_SUBUID_TOTAL="0"
ROOTLESS_SUBGID_TOTAL="0"
ROOTLESS_SUBID_USER="$(id -un 2>/dev/null || echo unknown)"
if [[ "${ODS_ASSUME_ROOTLESS:-0}" == "1" ]] || { [[ "$DOCKER_DAEMON" == "true" ]] \
    && docker info --format '{{.SecurityOptions}}' 2>/dev/null | grep -q 'rootless'; }; then
    ROOTLESS_DOCKER="true"
    subid_range_total() {
        # Sum every range allocated to the user; entries may be keyed by
        # name or numeric UID (subgid is also keyed by user, not group).
        local file="$1" user="$2" numeric_uid="$3" total=0 owner start count
        [[ -r "$file" ]] || { echo 0; return; }
        while IFS=: read -r owner start count; do
            if [[ "$owner" == "$user" || "$owner" == "$numeric_uid" ]]; then
                [[ "$count" =~ ^[0-9]+$ ]] && total=$((total + count))
            fi
        done <"$file"
        echo "$total"
    }
    _subid_uid="$(id -u)"
    ROOTLESS_SUBUID_TOTAL="$(subid_range_total "${ODS_SUBUID_FILE:-/etc/subuid}" "$ROOTLESS_SUBID_USER" "$_subid_uid")"
    ROOTLESS_SUBGID_TOTAL="$(subid_range_total "${ODS_SUBGID_FILE:-/etc/subgid}" "$ROOTLESS_SUBID_USER" "$_subid_uid")"
    if [[ "$ROOTLESS_SUBUID_TOTAL" -eq 0 || "$ROOTLESS_SUBGID_TOTAL" -eq 0 ]]; then
        ROOTLESS_SUBID_STATUS="fail"
    elif [[ "$ROOTLESS_SUBUID_TOTAL" -lt 65536 || "$ROOTLESS_SUBGID_TOTAL" -lt 65536 ]]; then
        ROOTLESS_SUBID_STATUS="warn"
    else
        ROOTLESS_SUBID_STATUS="pass"
    fi
fi
export ROOTLESS_DOCKER ROOTLESS_SUBID_STATUS ROOTLESS_SUBUID_TOTAL ROOTLESS_SUBGID_TOTAL ROOTLESS_SUBID_USER

# Collect extension diagnostics if service registry loaded
EXT_DIAGNOSTICS="[]"
if [[ "${#SERVICE_IDS[@]}" -gt 0 ]]; then
    EXT_DIAGNOSTICS=$(collect_extension_diagnostics)
fi

PYTHON_CMD="python3"
if [[ -f "$ROOT_DIR/lib/python-cmd.sh" ]]; then
    . "$ROOT_DIR/lib/python-cmd.sh"
    PYTHON_CMD="$(ods_detect_python_cmd)"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

"$PYTHON_CMD" - "$CAP_FILE" "$PREFLIGHT_FILE" "$REPORT_FILE" "$DOCKER_CLI" "$DOCKER_DAEMON" "$COMPOSE_CLI" "$DASHBOARD_HTTP" "$WEBUI_HTTP" "$_DASHBOARD_PORT" "$_WEBUI_PORT" "$EXT_DIAGNOSTICS" "$STT_MODEL_CACHED" "$STT_MODEL_NAME" "$STT_RECOVERY_HINT" "$TTS_HTTP" "$TTS_PORT" "$DGX_SPARK_GPU" "$DGX_SPARK_GPU_NAME" "$DGX_SPARK_COMPUTE_CAP" "$LLAMA_CUDA_ARCHS" "$DGX_SPARK_CUDA_ARCH_STATUS" "$DGX_SPARK_CUDA_ARCH_MESSAGE" "$HERMES_SLASH_WORKER_COUNT" "$HERMES_SLASH_WORKER_MAX_COUNT" "$ODS_MANAGED_CONTAINER_COUNT" "$ODS_RUNNING_CONTAINER_COUNT" "$ROOT_DIR" <<'PY'
import json
import os
import pathlib
import re
import shlex
import sys
from datetime import datetime, timezone
from urllib import error, parse, request

cap_file, preflight_file, report_file, docker_cli, docker_daemon, compose_cli, dashboard_http, webui_http, dashboard_port, webui_port, ext_diagnostics_json, stt_cached, stt_model_name, stt_recovery, tts_http, tts_port, dgx_spark_gpu, dgx_spark_gpu_name, dgx_spark_compute_cap, llama_cuda_archs, dgx_spark_arch_status, dgx_spark_arch_message, hermes_slash_worker_count, hermes_slash_worker_max_count, ods_managed_container_count, ods_running_container_count, root_dir_arg = sys.argv[1:]

cap = json.load(open(cap_file, "r", encoding="utf-8"))
pre = json.load(open(preflight_file, "r", encoding="utf-8"))
ext_diagnostics = json.loads(ext_diagnostics_json)
root_dir = pathlib.Path(root_dir_arg).resolve()

def _int_value(raw, default=0):
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


hermes_slash_worker_count_num = _int_value(hermes_slash_worker_count)
hermes_slash_worker_max_count_num = max(1, _int_value(hermes_slash_worker_max_count, 8))

def _clean_env(name, default=""):
    return os.environ.get(name, default).strip()


def _join_url(base_url, path):
    base = base_url.rstrip("/")
    suffix = path if path.startswith("/") else f"/{path}"
    return f"{base}{suffix}"


def _split_backends(raw):
    backends = []
    invalid = []
    for item in (raw or "").split(","):
        backend = item.strip().lower()
        if not backend:
            continue
        if backend in {"auto", "cpu", "npu", "rocm", "vulkan"}:
            if backend not in backends:
                backends.append(backend)
        else:
            invalid.append(backend)
    return backends, invalid


def _env_bool(name):
    return _clean_env(name).lower() in {"1", "true", "yes", "on"}


def _amd_health_url(runtime, location, port):
    external_lemonade = (
        _env_bool("LEMONADE_EXTERNAL")
        or _clean_env("AMD_INFERENCE_RUNTIME_MODE").lower() == "external-lemonade"
        or _clean_env("AMD_INFERENCE_MANAGED").lower() == "false"
    )
    if runtime == "lemonade" and external_lemonade:
        lemonade_base = (_clean_env("LEMONADE_BASE_URL") or "").rstrip("/")
        if lemonade_base:
            for suffix in ("/api/v1", "/v1", "/api"):
                if lemonade_base.endswith(suffix):
                    lemonade_base = lemonade_base[: -len(suffix)]
                    break
            api_path = _clean_env("LEMONADE_API_BASE_PATH", _clean_env("LLM_API_BASE_PATH", "/api/v1")) or "/api/v1"
            return _join_url(lemonade_base, _join_url(api_path, "health"))
    if location == "container":
        host_port = _clean_env("OLLAMA_PORT", port)
    else:
        host_port = port
    base = f"http://127.0.0.1:{host_port}"
    if runtime == "lemonade":
        api_path = _clean_env("LLM_API_BASE_PATH", "/api/v1") or "/api/v1"
        return _join_url(base, _join_url(api_path, "health"))
    return _join_url(base, "health")


def _probe_health(url):
    try:
        with request.urlopen(url, timeout=2.0) as response:
            status = getattr(response, "status", response.getcode())
            body = response.read(4096).decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        return "unhealthy", "unknown", f"health_http_{exc.code}"
    except (error.URLError, TimeoutError, OSError):
        return "unreachable", "unknown", "health_unreachable"

    version = "unknown"
    try:
        payload = json.loads(body) if body else {}
        if isinstance(payload, dict) and payload.get("version"):
            version = str(payload["version"])
    except json.JSONDecodeError:
        pass
    if 200 <= int(status) < 300:
        return "reachable", version, None
    return "unhealthy", version, f"health_http_{status}"


def _amd_runtime_report():
    gpu_backend = (_clean_env("GPU_BACKEND") or _clean_env("CAP_LLM_BACKEND")).lower()
    amd_env_present = any(
        _clean_env(name)
        for name in (
            "AMD_INFERENCE_RUNTIME",
            "AMD_INFERENCE_BACKEND",
            "AMD_INFERENCE_LOCATION",
            "AMD_INFERENCE_SUPPORTED_BACKENDS",
        )
    )
    if gpu_backend != "amd" and not amd_env_present:
        return {
            "available": False,
            "reason": "not_amd",
            "runtime": "none",
            "location": "none",
            "runtimeMode": "none",
            "managedByODS": False,
            "selectedBackend": "none",
            "supportedBackends": [],
            "defaultBackend": "none",
            "health": "not_checked",
            "warnings": [],
        }

    warnings = []
    runtime = _clean_env("AMD_INFERENCE_RUNTIME").lower()
    selected_backend = _clean_env("AMD_INFERENCE_BACKEND").lower()
    location = _clean_env("AMD_INFERENCE_LOCATION").lower()
    runtime_mode = _clean_env("AMD_INFERENCE_RUNTIME_MODE").lower()
    supported_backends, invalid_backends = _split_backends(_clean_env("AMD_INFERENCE_SUPPORTED_BACKENDS"))
    managed_raw = _clean_env("AMD_INFERENCE_MANAGED").lower()
    managed = _env_bool("AMD_INFERENCE_MANAGED")
    port = _clean_env("AMD_INFERENCE_PORT", "8080") or "8080"

    if invalid_backends:
        warnings.append("amd_supported_backends_invalid")
    if not runtime:
        runtime = "none"
        warnings.append("amd_runtime_env_missing")
    if not selected_backend:
        selected_backend = "unknown"
        warnings.append("amd_backend_env_missing")
    if not location:
        location = "unknown"
        warnings.append("amd_location_env_missing")
    if not runtime_mode:
        runtime_mode = "unknown"
        warnings.append("amd_runtime_mode_env_missing")
    if not managed_raw:
        warnings.append("amd_managed_env_missing")
    if not supported_backends:
        warnings.append("amd_supported_backends_env_missing")
    elif selected_backend not in {"unknown", "none"} and selected_backend not in supported_backends:
        warnings.append("amd_selected_backend_not_supported")

    if not port.isdigit() or not (1 <= int(port) <= 65535):
        port = "8080"
        warnings.append("amd_port_invalid")

    health = "not_checked"
    version = "unknown"
    health_url = None
    if runtime in {"lemonade", "llama-server"}:
        health_url = _amd_health_url(runtime, location, port)
        health, version, health_warning = _probe_health(health_url)
        if health_warning:
            warnings.append(health_warning)

    return {
        "available": runtime in {"lemonade", "llama-server"},
        "reason": None if runtime in {"lemonade", "llama-server"} else "runtime_not_configured",
        "runtime": runtime,
        "location": location,
        "runtimeMode": runtime_mode,
        "managedByODS": managed,
        "selectedBackend": selected_backend,
        "supportedBackends": supported_backends,
        "defaultBackend": selected_backend if selected_backend else "none",
        "healthUrl": health_url,
        "health": health,
        "version": version,
        "warnings": warnings,
    }


amd_runtime = _amd_runtime_report()


def _read_text(path, max_chars=120_000):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) > max_chars:
        return text[-max_chars:]
    return text


def _latest_install_report():
    reports = []
    try:
        reports = [p for p in root_dir.glob("install-report-*.txt") if p.is_file()]
    except OSError:
        reports = []
    if not reports:
        return None
    return max(reports, key=lambda p: p.stat().st_mtime)


def _parse_kv_lines(text):
    values = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            values[key] = value.strip()
    return values


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _int_arg(value):
    try:
        return int(str(value or "0").strip())
    except ValueError:
        return 0


def _mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return 0


def _source_mentions_zero_containers(text):
    lowered = text.lower()
    return (
        "docker compose did not create any managed containers" in lowered
        or "zero managed containers" in lowered
    )


def _zero_container_failure_is_stale(failure_sources, compose_launch_path):
    if docker_daemon != "true":
        return False
    if _int_arg(ods_managed_container_count) <= 0 or _int_arg(ods_running_container_count) <= 0:
        return False
    if not compose_launch_path.exists():
        return False

    zero_source_mtimes = [
        _mtime(path)
        for path, text in failure_sources
        if path and _source_mentions_zero_containers(text)
    ]
    if not zero_source_mtimes:
        return False

    return _mtime(compose_launch_path) > max(zero_source_mtimes)


def _env_file_values():
    env_path = root_dir / ".env"
    if not env_path.exists():
        return {}
    return _parse_kv_lines(_read_text(env_path))


def _compose_files_from_flags(flags_text):
    if not flags_text.strip():
        return []
    try:
        tokens = shlex.split(flags_text)
    except ValueError:
        tokens = flags_text.split()
    files = []
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token in {"-f", "--file"} and idx + 1 < len(tokens):
            files.append(tokens[idx + 1])
            idx += 2
            continue
        if token.startswith("--file="):
            files.append(token.split("=", 1)[1])
        idx += 1
    return files


def _has_compose_file(files, expected):
    return any(pathlib.PurePosixPath(item).name == expected or item == expected for item in files)


def _looks_like_litellm_route(value):
    lowered = (value or "").lower()
    return "litellm" in lowered or ":4000" in lowered


def _looks_like_local_llama_route(value):
    lowered = (value or "").lower()
    if not lowered:
        return False
    return (
        "llama-server" in lowered
        or "ods-llama-server" in lowered
        or "localhost:8080" in lowered
        or "127.0.0.1:8080" in lowered
        or "host.docker.internal:8080" in lowered
        or "localhost:11434" in lowered
        or "127.0.0.1:11434" in lowered
        or "host.docker.internal:11434" in lowered
    )


def _url_host(value):
    raw = (value or "").strip()
    if not raw:
        return ""
    candidate = raw if "://" in raw else f"http://{raw}"
    try:
        return (parse.urlparse(candidate).hostname or "").strip().lower()
    except ValueError:
        return ""


def _is_loopback_host(host):
    return host in {"localhost", "127.0.0.1", "::1"}


def _is_host_gateway(host):
    return host in {"host.docker.internal", "gateway.docker.internal"}


def _looks_like_installer_generated_lemonade_key(value):
    return str(value or "").strip().startswith("sk-ods-lemonade-")


def _source(path):
    try:
        return path.relative_to(root_dir).as_posix()
    except ValueError:
        return path.as_posix()


def _evidence(source, detail):
    return {"source": source, "detail": detail}


def _diagnosis(diag_id, severity, confidence, title, evidence, impact, next_steps):
    return {
        "id": diag_id,
        "severity": severity,
        "confidence": confidence,
        "title": title,
        "evidence": evidence,
        "impact": impact,
        "next_steps": next_steps,
    }


def _inference_issue(issue_id, severity, source, detail):
    return {
        "id": issue_id,
        "severity": severity,
        "source": source,
        "detail": detail,
    }


def _collect_inference_contract():
    env_values = _env_file_values()

    def env_get(name, default=""):
        return env_values.get(name) or _clean_env(name, default)

    flags_path = root_dir / ".compose-flags"
    flags_text = _read_text(flags_path, max_chars=20_000) if flags_path.exists() else ""
    compose_flags_exists = flags_path.exists()
    compose_files = _compose_files_from_flags(flags_text)

    ods_mode = (env_get("ODS_MODE", "local") or "local").strip().lower()
    gpu_backend = (env_get("GPU_BACKEND", "") or "").strip().lower()
    llm_backend = env_get("LLM_BACKEND", "")
    llm_api_url = env_get("LLM_API_URL", "")
    hermes_base_url = env_get("HERMES_LLM_BASE_URL", "")
    lemonade_external = (
        _truthy(env_get("LEMONADE_EXTERNAL"))
        or env_get("AMD_INFERENCE_RUNTIME_MODE").strip().lower() == "external-lemonade"
        or (
            env_get("AMD_INFERENCE_RUNTIME").strip().lower() == "lemonade"
            and env_get("AMD_INFERENCE_MANAGED").strip().lower() == "false"
        )
    )
    lemonade_base_url = env_get("LEMONADE_BASE_URL", "")
    lemonade_container_base_url = env_get("LEMONADE_CONTAINER_BASE_URL", "")
    lemonade_base_host = _url_host(lemonade_base_url)
    lemonade_container_host = _url_host(lemonade_container_base_url)
    lemonade_auth_configured = any(
        value and not _looks_like_installer_generated_lemonade_key(value)
        for value in (
            env_get("LEMONADE_API_KEY", ""),
            env_get("LEMONADE_ADMIN_API_KEY", ""),
            env_get("LITELLM_LEMONADE_API_KEY", ""),
        )
    )

    cloud_overlay = _has_compose_file(compose_files, "docker-compose.cloud.yml")
    lemonade_external_overlay = _has_compose_file(compose_files, "docker-compose.lemonade-external.yml")
    local_inference_overlay = any(
        _has_compose_file(compose_files, name)
        for name in (
            "docker-compose.nvidia.yml",
            "docker-compose.amd.yml",
            "docker-compose.cpu.yml",
            "docker-compose.arc.yml",
            "docker-compose.intel.yml",
            "docker-compose.apple.yml",
            "docker-compose.macos.yml",
        )
    )

    external_inference = ods_mode == "cloud" or lemonade_external
    expected_owner = "external" if external_inference else "ods"
    expected_gateway = (
        "litellm"
        if external_inference or ods_mode == "lemonade" or gpu_backend == "amd"
        else "llama-server"
    )

    issues = []
    if ods_mode not in {"local", "cloud", "hybrid", "lemonade"}:
        issues.append(
            _inference_issue(
                "ODS-RUNTIME-MODE-UNKNOWN",
                "blocker",
                ".env",
                f"ODS_MODE={ods_mode!r} is not one of local/cloud/hybrid/lemonade.",
            )
        )

    if ods_mode == "cloud":
        if compose_flags_exists and not cloud_overlay:
            issues.append(
                _inference_issue(
                    "ODS-RUNTIME-CLOUD-OVERLAY-MISSING",
                    "blocker",
                    ".compose-flags",
                    "ODS_MODE=cloud but docker-compose.cloud.yml is not in the resolved compose stack.",
                )
            )
        if _looks_like_local_llama_route(llm_api_url):
            issues.append(
                _inference_issue(
                    "ODS-RUNTIME-CLOUD-LLM-LOCAL-ROUTE",
                    "blocker",
                    ".env",
                    f"ODS_MODE=cloud but LLM_API_URL points at local llama-server ({llm_api_url}).",
                )
            )
        if _looks_like_local_llama_route(hermes_base_url):
            issues.append(
                _inference_issue(
                    "ODS-RUNTIME-CLOUD-HERMES-LOCAL-ROUTE",
                    "blocker",
                    ".env",
                    f"ODS_MODE=cloud but HERMES_LLM_BASE_URL points at local llama-server ({hermes_base_url}).",
                )
            )
        if llm_api_url and not _looks_like_litellm_route(llm_api_url):
            issues.append(
                _inference_issue(
                    "ODS-RUNTIME-CLOUD-GATEWAY-BYPASS",
                    "warn",
                    ".env",
                    f"ODS_MODE=cloud normally routes ODS services through LiteLLM, but LLM_API_URL={llm_api_url}.",
                )
            )

    if lemonade_external:
        if compose_flags_exists and not cloud_overlay:
            issues.append(
                _inference_issue(
                    "ODS-RUNTIME-EXTERNAL-LEMONADE-CLOUD-OVERLAY-MISSING",
                    "blocker",
                    ".compose-flags",
                    "External Lemonade needs the cloud overlay so ODS does not start a managed llama-server.",
                )
            )
        if compose_flags_exists and not lemonade_external_overlay:
            issues.append(
                _inference_issue(
                    "ODS-RUNTIME-EXTERNAL-LEMONADE-OVERLAY-MISSING",
                    "warn",
                    ".compose-flags",
                    "External Lemonade mode is active but docker-compose.lemonade-external.yml is not in the compose stack.",
                )
            )
        if _looks_like_local_llama_route(llm_api_url):
            issues.append(
                _inference_issue(
                    "ODS-RUNTIME-EXTERNAL-LEMONADE-LOCAL-ROUTE",
                    "blocker",
                    ".env",
                    f"External Lemonade is configured, but LLM_API_URL points at local llama-server ({llm_api_url}).",
                )
            )
        host_routed_lemonade = _is_host_gateway(lemonade_container_host)
        network_lemonade = lemonade_base_host and not _is_loopback_host(lemonade_base_host)
        if (host_routed_lemonade or network_lemonade) and not lemonade_auth_configured:
            detail = (
                "External Lemonade is routed through "
                f"{lemonade_container_base_url or lemonade_base_url or 'an unknown host route'} "
                "without a user-provided Lemonade API key. If the Lemonade daemon is bound "
                "beyond loopback so Docker can reach it, that same daemon may also be reachable "
                "from the LAN."
            )
            issues.append(
                _inference_issue(
                    "ODS-RUNTIME-EXTERNAL-LEMONADE-UNAUTHENTICATED-HOST-ROUTE",
                    "warn",
                    ".env",
                    detail,
                )
            )

    if ods_mode == "local" and not lemonade_external:
        if compose_flags_exists and cloud_overlay:
            issues.append(
                _inference_issue(
                    "ODS-RUNTIME-LOCAL-CLOUD-OVERLAY",
                    "warn",
                    ".compose-flags",
                    "ODS_MODE=local but docker-compose.cloud.yml is still in the compose stack.",
                )
            )
        if _looks_like_litellm_route(llm_api_url) and gpu_backend != "amd":
            issues.append(
                _inference_issue(
                    "ODS-RUNTIME-LOCAL-LITELLM-ROUTE",
                    "warn",
                    ".env",
                    f"ODS_MODE=local on non-AMD usually routes directly to llama-server, but LLM_API_URL={llm_api_url}.",
                )
            )

    diagnoses = []
    issue_titles = {
        "ODS-RUNTIME-MODE-UNKNOWN": "Runtime mode is not recognized",
        "ODS-RUNTIME-CLOUD-OVERLAY-MISSING": "Cloud mode is missing the cloud compose overlay",
        "ODS-RUNTIME-CLOUD-LLM-LOCAL-ROUTE": "Cloud mode still routes chat clients to local llama-server",
        "ODS-RUNTIME-CLOUD-HERMES-LOCAL-ROUTE": "Cloud mode still routes Hermes to local llama-server",
        "ODS-RUNTIME-CLOUD-GATEWAY-BYPASS": "Cloud mode bypasses the LiteLLM gateway",
        "ODS-RUNTIME-EXTERNAL-LEMONADE-CLOUD-OVERLAY-MISSING": "External Lemonade is missing the cloud compose overlay",
        "ODS-RUNTIME-EXTERNAL-LEMONADE-OVERLAY-MISSING": "External Lemonade is missing its compose overlay",
        "ODS-RUNTIME-EXTERNAL-LEMONADE-LOCAL-ROUTE": "External Lemonade still routes clients to local llama-server",
        "ODS-RUNTIME-EXTERNAL-LEMONADE-UNAUTHENTICATED-HOST-ROUTE": "External Lemonade host route has no user-provided API key",
        "ODS-RUNTIME-LOCAL-CLOUD-OVERLAY": "Local mode still has the cloud compose overlay",
        "ODS-RUNTIME-LOCAL-LITELLM-ROUTE": "Local mode routes through LiteLLM unexpectedly",
    }
    next_steps = {
        "ODS-RUNTIME-CLOUD-OVERLAY-MISSING": [
            "Regenerate compose flags with ODS_MODE=cloud and include docker-compose.cloud.yml.",
            "Run `ods restart` after correcting .env/.compose-flags.",
        ],
        "ODS-RUNTIME-CLOUD-LLM-LOCAL-ROUTE": [
            "Set LLM_API_URL to the LiteLLM service URL used by the stack, usually http://litellm:4000.",
            "Do not point cloud mode at llama-server unless ODS is intentionally managing local inference.",
        ],
        "ODS-RUNTIME-CLOUD-HERMES-LOCAL-ROUTE": [
            "Set HERMES_LLM_BASE_URL to http://litellm:4000/v1 for cloud mode.",
            "Restart Hermes after updating the generated config/template.",
        ],
        "ODS-RUNTIME-CLOUD-GATEWAY-BYPASS": [
            "Route ODS services through LiteLLM so hosted, private-cloud, and auth behavior stay consistent.",
        ],
        "ODS-RUNTIME-EXTERNAL-LEMONADE-CLOUD-OVERLAY-MISSING": [
            "Regenerate compose flags for external Lemonade so the managed llama-server is profiled out.",
        ],
        "ODS-RUNTIME-EXTERNAL-LEMONADE-OVERLAY-MISSING": [
            "Include docker-compose.lemonade-external.yml when LEMONADE_EXTERNAL=true.",
        ],
        "ODS-RUNTIME-EXTERNAL-LEMONADE-LOCAL-ROUTE": [
            "Route external Lemonade clients through LiteLLM, usually http://litellm:4000.",
        ],
        "ODS-RUNTIME-EXTERNAL-LEMONADE-UNAUTHENTICATED-HOST-ROUTE": [
            "Configure Lemonade with LEMONADE_API_KEY or LEMONADE_ADMIN_API_KEY, then reinstall with --lemonade-api-key.",
            "Prefer binding Lemonade to a host-only or Docker-reachable interface instead of exposing it broadly on 0.0.0.0.",
            "Keep firewall rules scoped to the Docker network subnet when host-routed Lemonade is required.",
        ],
        "ODS-RUNTIME-LOCAL-CLOUD-OVERLAY": [
            "Regenerate compose flags for local mode so local inference starts normally.",
        ],
        "ODS-RUNTIME-LOCAL-LITELLM-ROUTE": [
            "If this is not an AMD/Lemonade install, set LLM_API_URL back to http://llama-server:8080.",
        ],
        "ODS-RUNTIME-MODE-UNKNOWN": [
            "Set ODS_MODE to local, cloud, hybrid, or lemonade.",
        ],
    }
    for issue in issues:
        diagnoses.append(
            _diagnosis(
                issue["id"],
                issue["severity"],
                "high" if issue["severity"] == "blocker" else "medium",
                issue_titles.get(issue["id"], "Inference runtime contract mismatch"),
                [_evidence(issue["source"], issue["detail"])],
                "ODS services can report healthy while chat, agents, or model routing target the wrong inference owner.",
                next_steps.get(issue["id"], ["Regenerate .env/.compose-flags with the installer and restart ODS."]),
            )
        )

    return {
        "ods_mode": ods_mode,
        "gpu_backend": gpu_backend,
        "llm_backend": llm_backend,
        "expected_inference_owner": expected_owner,
        "expected_gateway": expected_gateway,
        "external_lemonade": lemonade_external,
        "llm_api_url": llm_api_url,
        "hermes_llm_base_url": hermes_base_url,
        "compose_files": compose_files,
        "signals": {
            "compose_flags_exists": compose_flags_exists,
            "cloud_overlay": cloud_overlay,
            "lemonade_external_overlay": lemonade_external_overlay,
            "local_inference_overlay": local_inference_overlay,
            "lemonade_auth_configured": lemonade_auth_configured,
            "lemonade_host_routed": _is_host_gateway(lemonade_container_host),
        },
        "issues": issues,
        "issue_counts": {
            "blockers": sum(1 for item in issues if item["severity"] == "blocker"),
            "warnings": sum(1 for item in issues if item["severity"] == "warn"),
        },
        "diagnoses": diagnoses,
    }


def _extract_image(text):
    match = re.search(
        r"failed to resolve reference\s+\"?([^\"\s]+:[A-Za-z0-9._-]+)\"?",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1)
    match = re.search(
        r"([a-z0-9._-]+(?:[.:][0-9]+)?/[a-z0-9._/-]+:[A-Za-z0-9._-]+).*?(?:not found|manifest unknown)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group(1) if match else ""


def _is_windows_alpine_probe_failure(text):
    lowered = text.lower()
    has_alpine_probe = (
        "alpine:3.20" in lowered
        or "unable to find image 'alpine" in lowered
        or 'unable to find image "alpine' in lowered
    )
    if not has_alpine_probe:
        return False
    return (
        "docker could not download" in lowered
        or "bind-mount probe" in lowered
        or "file-sharing probe" in lowered
        or "cannot bind-mount" in lowered
    )


def _collect_install_artifacts():
    latest_report = _latest_install_report()
    compose_launch = root_dir / "logs" / "compose-launch.txt"
    compose_up = root_dir / "logs" / "compose-up.log"
    install_log = root_dir / "logs" / "install.log"
    env_file = root_dir / ".env"
    compose_flags = root_dir / ".compose-flags"
    return {
        "root_dir": root_dir.as_posix(),
        "env_file": {
            "path": _source(env_file),
            "exists": env_file.is_file(),
        },
        "compose_flags_file": {
            "path": _source(compose_flags),
            "exists": compose_flags.is_file(),
        },
        "compose_launch_record": {
            "path": _source(compose_launch),
            "exists": compose_launch.is_file(),
        },
        "compose_up_log": {
            "path": _source(compose_up),
            "exists": compose_up.is_file(),
        },
        "install_log": {
            "path": _source(install_log),
            "exists": install_log.is_file(),
        },
        "latest_install_report": (
            {
                "path": _source(latest_report),
                "exists": True,
            }
            if latest_report
            else {"path": None, "exists": False}
        ),
        "current_ods_containers": {
            "managed": _int_arg(ods_managed_container_count),
            "running": _int_arg(ods_running_container_count),
        },
    }


def _collect_install_diagnoses(artifacts):
    diagnoses = []
    env_path = root_dir / ".env"
    flags_path = root_dir / ".compose-flags"
    compose_launch_path = root_dir / "logs" / "compose-launch.txt"
    compose_up_path = root_dir / "logs" / "compose-up.log"
    install_log_path = root_dir / "logs" / "install.log"
    latest_report_path = _latest_install_report()

    installed_like = (
        flags_path.exists()
        or compose_launch_path.exists()
        or (root_dir / "data").exists()
        or bool(latest_report_path)
    )
    if installed_like and not env_path.exists():
        diagnoses.append(
            _diagnosis(
                "ODS-INSTALL-ENV-MISSING",
                "blocker",
                "high",
                "Install directory is missing .env",
                [
                    _evidence(_source(env_path), "Expected installer-generated .env was not found."),
                    _evidence(artifacts["root_dir"], "Doctor root appears to be an installed ODS tree."),
                ],
                "Docker Compose and service config can resolve missing or default values, causing startup failures.",
                [
                    "Re-run the installer from the source checkout.",
                    "Run ODS commands from the installed directory that contains .env.",
                ],
            )
        )

    if compose_launch_path.exists():
        launch_text = _read_text(compose_launch_path)
        launch = _parse_kv_lines(launch_text)
        launch_cwd = launch.get("cwd")
        if launch_cwd:
            try:
                launch_cwd_path = pathlib.Path(launch_cwd).resolve()
            except OSError:
                launch_cwd_path = pathlib.Path(launch_cwd)
            if launch_cwd_path != root_dir:
                diagnoses.append(
                    _diagnosis(
                        "ODS-COMPOSE-CWD-MISMATCH",
                        "blocker",
                        "high",
                        "Compose launch record points at a different working directory",
                        [
                            _evidence(_source(compose_launch_path), f"cwd={launch_cwd}"),
                            _evidence("doctor.root_dir", root_dir.as_posix()),
                        ],
                        "Compose can miss .env or use the wrong relative compose files when launched outside the install directory.",
                        [
                            f"Run ODS commands from {root_dir.as_posix()}.",
                            "Use `ods start` / `ods restart` instead of raw docker compose from a source checkout.",
                        ],
                    )
                )

    failure_sources = []
    if latest_report_path:
        failure_sources.append((latest_report_path, _read_text(latest_report_path)))
    if compose_up_path.exists():
        failure_sources.append((compose_up_path, _read_text(compose_up_path)))
    if install_log_path.exists():
        failure_sources.append((install_log_path, _read_text(install_log_path)))
    combined_failure_text = "\n".join(text for _, text in failure_sources if text)

    if combined_failure_text:
        failed_image = _extract_image(combined_failure_text)
        if failed_image:
            evidence = []
            if latest_report_path:
                evidence.append(_evidence(_source(latest_report_path), f"Failed image: {failed_image}"))
            if compose_up_path.exists():
                evidence.append(_evidence(_source(compose_up_path), f"Failed image: {failed_image}"))
            diagnoses.append(
                _diagnosis(
                    "ODS-DOCKER-IMAGE-UNRESOLVED",
                    "blocker",
                    "high",
                    "Docker image reference could not be resolved",
                    evidence,
                    "The stack cannot start until the image tag is corrected, republished, or replaced by a supported fallback.",
                    [
                        f"Check whether `{failed_image}` exists in the registry.",
                        "Update the image pin or pull a known-good ODS version, then re-run the installer.",
                    ],
                )
            )

        lowered = combined_failure_text.lower()
        if _source_mentions_zero_containers(
            combined_failure_text
        ) and not _zero_container_failure_is_stale(failure_sources, compose_launch_path):
            diagnoses.append(
                _diagnosis(
                    "ODS-COMPOSE-ZERO-CONTAINERS",
                    "blocker",
                    "high",
                    "Docker Compose completed without creating ODS containers",
                    [
                        _evidence(_source(latest_report_path), "Report/log mentions zero managed containers.")
                        if latest_report_path
                        else _evidence(_source(compose_up_path), "Compose log mentions zero managed containers.")
                    ],
                    "Startup may appear to finish while no service is actually managed by the resolved compose stack.",
                    [
                        "Open logs/compose-launch.txt and run the saved ps/logs commands.",
                        "Fix the compose/runtime error and re-run the installer.",
                    ],
                )
            )

        if "modulenotfounderror: no module named 'yaml'" in lowered or "pyyaml is required" in lowered:
            diagnoses.append(
                _diagnosis(
                    "ODS-PYTHON-PYYAML-MISSING",
                    "blocker",
                    "high",
                    "Selected Python cannot import PyYAML",
                    [
                        _evidence(
                            _source(latest_report_path) if latest_report_path else _source(compose_up_path),
                            "Failure text mentions missing yaml/PyYAML.",
                        )
                    ],
                    "Compose resolution and generated config validation depend on PyYAML and can fail before services start.",
                    [
                        "Re-run the installer so it can install PyYAML or create the private installer venv.",
                        "If using Conda/venv, deactivate it and retry with the system Python.",
                    ],
                )
            )

        alpine_probe_source = None
        for source_path, source_text in failure_sources:
            if _is_windows_alpine_probe_failure(source_text):
                alpine_probe_source = source_path
                break
        if alpine_probe_source:
            diagnoses.append(
                _diagnosis(
                    "ODS-WINDOWS-FILE-SHARING-PROBE-IMAGE",
                    "warn",
                    "medium",
                    "Windows bind-mount probe was blocked before the file-sharing check completed",
                    [
                        _evidence(
                            _source(alpine_probe_source),
                            "Failure text includes Alpine probe image pull/download or bind-mount probe failure.",
                        )
                    ],
                    "Docker Desktop can report a file-sharing failure when the real prerequisite is the probe image/network path.",
                    [
                        "Run `docker pull alpine:3.20` and verify Docker Desktop has internet access.",
                        "Then verify Docker Desktop > Settings > Resources > File Sharing includes the install directory.",
                    ],
                )
            )

    # Deduplicate by id while preserving order; one diagnosis per root cause keeps
    # the human output stable and avoids noisy repeats across install report/log.
    seen = set()
    unique = []
    for item in diagnoses:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        unique.append(item)
    return unique


install_artifacts = _collect_install_artifacts()
inference_contract = _collect_inference_contract()
diagnoses = _collect_install_diagnoses(install_artifacts) + inference_contract.get("diagnoses", [])
inference_contract_public = dict(inference_contract)
inference_contract_public.pop("diagnoses", None)

report = {
    "version": "1",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "autofix_hints": [],
    "capability_profile": cap,
    "preflight": pre,
    "install_artifacts": install_artifacts,
    "diagnoses": diagnoses,
    "runtime": {
        "docker_cli": docker_cli == "true",
        "docker_daemon": docker_daemon == "true",
        "compose_cli": compose_cli == "true",
        "dashboard_http": dashboard_http == "true",
        "webui_http": webui_http == "true",
        "stt_model_cached": stt_cached,
        "stt_model_name": stt_model_name,
        "tts_http": tts_http,
        "tts_port": tts_port,
        "dgx_spark_gpu": dgx_spark_gpu == "true",
        "dgx_spark_gpu_name": dgx_spark_gpu_name,
        "dgx_spark_compute_cap": dgx_spark_compute_cap,
        "llama_cuda_archs": llama_cuda_archs,
        "dgx_spark_cuda_arch_check": {
            "status": dgx_spark_arch_status,
            "message": dgx_spark_arch_message,
        },
        "hermes_slash_workers": {
            "count": hermes_slash_worker_count_num,
            "max_count": hermes_slash_worker_max_count_num,
            "status": "warn"
            if hermes_slash_worker_count_num > hermes_slash_worker_max_count_num
            else "pass",
        },
        "rootless_docker": os.environ.get("ROOTLESS_DOCKER") == "true",
        "rootless_subid_check": {
            "status": os.environ.get("ROOTLESS_SUBID_STATUS", "skipped"),
            "user": os.environ.get("ROOTLESS_SUBID_USER", ""),
            "subuid_total": _int_value(os.environ.get("ROOTLESS_SUBUID_TOTAL")),
            "subgid_total": _int_value(os.environ.get("ROOTLESS_SUBGID_TOTAL")),
        },
        "amd_runtime": amd_runtime,
        "inference_contract": inference_contract_public,
    },
    "extensions": ext_diagnostics,
    "summary": {
        "preflight_blockers": pre.get("summary", {}).get("blockers", 0),
        "preflight_warnings": pre.get("summary", {}).get("warnings", 0),
        "runtime_warnings": (
            (1 if dgx_spark_arch_status == "warn" else 0)
            + (1 if stt_cached in {"false", "service_down"} else 0)
            + (1 if tts_http == "false" else 0)
            + (1 if hermes_slash_worker_count_num > hermes_slash_worker_max_count_num else 0)
            + (1 if os.environ.get("ROOTLESS_SUBID_STATUS") in {"warn", "fail"} else 0)
            + len(amd_runtime.get("warnings", []))
            + inference_contract.get("issue_counts", {}).get("warnings", 0)
        ),
        "runtime_contract_blockers": inference_contract.get("issue_counts", {}).get("blockers", 0),
        "runtime_contract_warnings": inference_contract.get("issue_counts", {}).get("warnings", 0),
        "diagnoses_total": len(diagnoses),
        "diagnoses_blockers": sum(1 for d in diagnoses if d.get("severity") == "blocker"),
        "diagnoses_warnings": sum(1 for d in diagnoses if d.get("severity") == "warn"),
        "runtime_ready": (docker_daemon == "true" and compose_cli == "true"),
        "extensions_total": len(ext_diagnostics),
        "extensions_healthy": sum(1 for e in ext_diagnostics if e.get("health_status") == "healthy"),
        "extensions_issues": sum(1 for e in ext_diagnostics if len(e.get("issues", [])) > 0),
    },
}

fix_hints = []
for diagnosis in diagnoses:
    fix_hints.extend(diagnosis.get("next_steps", []))

for check in pre.get("checks", []):
    status = check.get("status")
    action = (check.get("action") or "").strip()
    if status in {"blocker", "warn"} and action:
        fix_hints.append(action)

runtime = report["runtime"]
if not runtime["docker_cli"]:
    fix_hints.append("Install Docker CLI/Docker Desktop and reopen your terminal.")
if runtime["docker_cli"] and not runtime["docker_daemon"]:
    fix_hints.append("Start Docker daemon/Desktop before launching ODS.")
if not runtime["compose_cli"]:
    fix_hints.append("Install Docker Compose v2 plugin (or docker-compose).")
if runtime["docker_daemon"] and not runtime["dashboard_http"]:
    fix_hints.append(f"Run installer/start command, then verify dashboard on http://127.0.0.1:{dashboard_port}.")
if runtime["docker_daemon"] and not runtime["webui_http"]:
    fix_hints.append(f"Verify Open WebUI container and port {webui_port} mapping.")

# Rootless daemon without (enough) subordinate IDs: images fail to extract
# with "uid/gid map out of range" long after doctor reports healthy Docker.
# Two hints: the fixed 100000-165535 range is only safe to suggest when the
# user has NO allocation; with an existing-but-small range that command
# could overlap what is already allocated, so ask for a non-overlapping
# extension instead.
_rootless_subid = report["runtime"]["rootless_subid_check"]
if _rootless_subid["status"] == "fail":
    fix_hints.append(
        "Rootless Docker: no subordinate ID allocation for "
        f"{_rootless_subid['user']} "
        f"(subuids={_rootless_subid['subuid_total']}, subgids={_rootless_subid['subgid_total']}). "
        f"Run: sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 {_rootless_subid['user']} "
        "then restart the rootless Docker daemon (systemctl --user restart docker)."
    )
elif _rootless_subid["status"] == "warn":
    fix_hints.append(
        "Rootless Docker: subordinate ID allocation for "
        f"{_rootless_subid['user']} is below 65536 "
        f"(subuids={_rootless_subid['subuid_total']}, subgids={_rootless_subid['subgid_total']}). "
        "Extend it to at least 65536 IDs with a range that does not overlap "
        "existing entries in /etc/subuid and /etc/subgid "
        f"(sudo usermod --add-subuids <start>-<start+65535> --add-subgids <start>-<start+65535> {_rootless_subid['user']}), "
        "then restart the rootless Docker daemon (systemctl --user restart docker)."
    )

# STT model cache: service up but model missing is a common silent failure
if stt_cached == "false" and stt_recovery:
    fix_hints.append(
        f"Whisper STT model '{stt_model_name}' not cached — transcription will 404. "
        f"Run: {stt_recovery}"
    )
elif stt_cached == "service_down":
    fix_hints.append("Whisper STT is not responding. Run: ods repair voice")

if tts_http == "false":
    fix_hints.append("Kokoro TTS is not responding. Run: ods repair voice")

if hermes_slash_worker_count_num > hermes_slash_worker_max_count_num:
    fix_hints.append(
        f"Hermes has {hermes_slash_worker_count_num} slash_worker children "
        f"(policy max {hermes_slash_worker_max_count_num}). Run: ods repair hermes-workers"
    )

if dgx_spark_arch_status == "warn":
    fix_hints.append(
        "DGX Spark / GB10 detected, but llama-server was not built with sm_121 support. "
        "Build llama.cpp with -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=121 or use a GB10-specific llama-server image."
    )

for warning in amd_runtime.get("warnings", []):
    if warning == "health_unreachable":
        fix_hints.append("AMD inference runtime is configured but its health endpoint is unreachable. Start the runtime or run 'ods restart'.")
    elif warning == "amd_supported_backends_env_missing":
        fix_hints.append("AMD runtime capabilities are missing from .env. Re-run the installer or add AMD_INFERENCE_SUPPORTED_BACKENDS.")
    elif warning == "amd_selected_backend_not_supported":
        fix_hints.append("AMD_INFERENCE_BACKEND is not listed in AMD_INFERENCE_SUPPORTED_BACKENDS. Check the installer-generated .env.")

# Extension-specific hints
for ext in ext_diagnostics:
    ext_id = ext.get("id", "unknown")
    container_state = ext.get("container_state", "unknown")
    issues = ext.get("issues", [])
    for issue in issues:
        if issue == "container_not_running":
            if container_state == "not_found":
                fix_hints.append(f"Extension {ext_id}: not installed (image not built). Skipped by installer or disabled by tier system.")
            else:
                fix_hints.append(f"Extension {ext_id}: container not running. Run 'ods start {ext_id}'.")
        elif issue == "health_check_failed":
            fix_hints.append(f"Extension {ext_id}: health check failed. Check logs with 'docker logs ods-{ext_id}'.")
        elif issue == "gpu_backend_incompatible":
            fix_hints.append(f"Extension {ext_id}: incompatible with current GPU backend. Consider disabling.")
        elif issue.startswith("missing_dependency:"):
            dep = issue.split(":", 1)[1]
            fix_hints.append(f"Extension {ext_id}: missing dependency '{dep}'. Run 'ods enable {dep}'.")


# Deduplicate while preserving order
seen = set()
uniq_hints = []
for hint in fix_hints:
    if hint in seen:
        continue
    seen.add(hint)
    uniq_hints.append(hint)

report["autofix_hints"] = uniq_hints  # overwrite initial empty list

path = pathlib.Path(report_file)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
PY

echo "ODS Doctor report: $REPORT_FILE"
echo "  Preflight blockers: ${PREFLIGHT_BLOCKERS:-0}"
echo "  Preflight warnings: ${PREFLIGHT_WARNINGS:-0}"
echo "  Docker daemon: $DOCKER_DAEMON"
echo "  Compose CLI:   $COMPOSE_CLI"
"$PYTHON_CMD" - "$REPORT_FILE" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    data = json.load(open(path, "r", encoding="utf-8"))
except Exception:
    raise SystemExit(0)

# Show extension summary
summary = data.get("summary", {})
ext_total = summary.get("extensions_total", 0)
ext_healthy = summary.get("extensions_healthy", 0)
ext_issues = summary.get("extensions_issues", 0)

if ext_total > 0:
    print(f"  Extensions:    {ext_healthy}/{ext_total} healthy, {ext_issues} with issues")

dgx_check = data.get("runtime", {}).get("dgx_spark_cuda_arch_check", {})
if dgx_check.get("status") == "warn":
    print(f"  DGX Spark:     warning - {dgx_check.get('message')}")
elif dgx_check.get("status") == "pass":
    print("  DGX Spark:     llama-server includes sm_121 support")

amd_runtime = data.get("runtime", {}).get("amd_runtime", {})
if amd_runtime.get("available"):
    print(
        "  AMD Runtime:   "
        f"{amd_runtime.get('runtime')} / {amd_runtime.get('selectedBackend')} / "
        f"{amd_runtime.get('location')} / {amd_runtime.get('health')}"
    )
elif amd_runtime.get("reason") and amd_runtime.get("reason") != "not_amd":
    print(f"  AMD Runtime:   {amd_runtime.get('reason')}")

hermes_workers = data.get("runtime", {}).get("hermes_slash_workers", {})
if hermes_workers.get("status") == "warn":
    print(
        "  Hermes:        "
        f"{hermes_workers.get('count')} slash_worker processes "
        f"(policy max {hermes_workers.get('max_count')})"
    )

diagnoses = data.get("diagnoses") or []
if diagnoses:
    blockers = sum(1 for item in diagnoses if item.get("severity") == "blocker")
    warnings = sum(1 for item in diagnoses if item.get("severity") == "warn")
    print(f"  Diagnoses:     {len(diagnoses)} total, {blockers} blocker(s), {warnings} warning(s)")
    for item in diagnoses[:5]:
        print(f"    - {item.get('id')}: {item.get('title')}")

hints = data.get("autofix_hints") or []
if hints:
    print("  Suggested fixes:")
    for hint in hints[:10]:
        print(f"    - {hint}")
PY
