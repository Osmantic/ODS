#!/usr/bin/env bash
# Shared placement contract for the Windows installer and Linux config writer.
# This does not provision or adopt a runtime; Windows must prepare it first.
ods_windows_lemonade_requested() {
    [[ "${AMD_INFERENCE_RUNTIME_MODE:-}" == wsl-windows-lemonade ]]
}

ods_windows_lemonade_validate() {
    ods_windows_lemonade_requested || return 1
    [[ "$(uname -s)" == Linux ]] && grep -qi microsoft /proc/version || return 1
    [[ "${GPU_BACKEND:-}" == amd && "${AMD_INFERENCE_RUNTIME:-}" == lemonade \
        && "${AMD_INFERENCE_LOCATION:-}" == host && "${AMD_INFERENCE_MANAGED:-}" == true \
        && "${LEMONADE_EXTERNAL:-false}" == false && -z "${EXTERNAL_LLM_URL:-}" ]] || return 1
    [[ "${AMD_INFERENCE_PORT:-}" =~ ^[1-9][0-9]{0,4}$ ]] || return 1
    (( AMD_INFERENCE_PORT <= 65535 )) || return 1
    [[ -n "${LEMONADE_MODEL:-}" && "$LEMONADE_MODEL" != *$'\n'* && "$LEMONADE_MODEL" != *$'\r'* ]] || return 1
    [[ "${LEMONADE_BASE_URL:-}" == "http://127.0.0.1:${AMD_INFERENCE_PORT}" \
        && "${LEMONADE_CONTAINER_BASE_URL:-}" == "http://host.docker.internal:${AMD_INFERENCE_PORT}" ]] || return 1
}

ods_windows_lemonade_apply_hardware() {
    ods_windows_lemonade_validate || return 1
    [[ "${ODS_WINDOWS_GPU_VRAM_MB:-}" =~ ^[1-9][0-9]*$ \
        && "${ODS_WINDOWS_GPU_COUNT:-}" =~ ^[1-9][0-9]*$ \
        && "${ODS_WINDOWS_MODEL_CONTEXT:-}" =~ ^[1-9][0-9]*$ ]] || return 1
    (( ODS_WINDOWS_MODEL_CONTEXT <= 10000000 )) || return 1
    [[ "${ODS_WINDOWS_GPU_MEMORY_TYPE:-}" == discrete || "${ODS_WINDOWS_GPU_MEMORY_TYPE:-}" == unified ]] || return 1
    [[ "${ODS_WINDOWS_TIER:-}" =~ ^(0|1|2|3|4|SH_COMPACT|SH_LARGE|NV_ULTRA)$ ]] || return 1
    [[ -z "${TIER:-}" || "$TIER" == "$ODS_WINDOWS_TIER" ]] || return 1
    [[ -n "${ODS_WINDOWS_MODEL_FILE:-}" && "$ODS_WINDOWS_MODEL_FILE" == *.gguf \
        && "$ODS_WINDOWS_MODEL_FILE" != */* && "$ODS_WINDOWS_MODEL_FILE" != *\\* \
        && -n "${ODS_WINDOWS_MODEL_ID:-}" && -n "${ODS_WINDOWS_GPU_NAME:-}" ]] || return 1
    GPU_NAME="$ODS_WINDOWS_GPU_NAME"
    GPU_VRAM="$ODS_WINDOWS_GPU_VRAM_MB"
    GPU_COUNT="$ODS_WINDOWS_GPU_COUNT"
    GPU_MEMORY_TYPE="$ODS_WINDOWS_GPU_MEMORY_TYPE"
    GPU_TOPOLOGY_JSON='{}'
    GPU_HAS_NVLINK=false
    GPU_TOTAL_VRAM="$GPU_VRAM"
    TIER="$ODS_WINDOWS_TIER"
    TIER_NAME="Windows AMD ($TIER)"
    LLM_MODEL="$ODS_WINDOWS_MODEL_ID"
    GGUF_FILE="$ODS_WINDOWS_MODEL_FILE"
    local model_bytes
    [[ -f "${ODS_WINDOWS_MODELS_PATH:-}/$GGUF_FILE" ]] || return 1
    model_bytes=$(stat -c '%s' -- "$ODS_WINDOWS_MODELS_PATH/$GGUF_FILE") || return 1
    [[ "$model_bytes" =~ ^[1-9][0-9]*$ ]] || return 1
    LLM_MODEL_SIZE_MB=$(( (model_bytes + 1048575) / 1048576 ))
    GGUF_URL=""
    MAX_CONTEXT="$ODS_WINDOWS_MODEL_CONTEXT"
    CTX_SIZE="$MAX_CONTEXT"
    # The prepared Windows model is already verified. Do not replace it with a
    # Linux VM recommendation or download a second bootstrap checkpoint.
    CAP_PROFILE_LOADED=false
    CAP_COMPOSE_OVERLAYS=""
    NO_BOOTSTRAP=true
    GPU_BACKEND_FORCED=false
    BACKEND_ID=amd
    ODS_MODE=lemonade
    LLM_HEALTHCHECK_URL=http://127.0.0.1:4000/health/readiness
    LLM_PUBLIC_API_PORT=4000
    OPENCLAW_PROVIDER_NAME_DEFAULT=local-lemonade
    OPENCLAW_PROVIDER_URL_DEFAULT=http://litellm:4000/v1
    export GPU_BACKEND GPU_NAME GPU_VRAM GPU_COUNT GPU_MEMORY_TYPE ODS_MODE
}
