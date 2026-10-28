#!/usr/bin/env bash
# ============================================================================
# ODS Installer -- model server choice (llama.cpp or the vLLM engine)
# ============================================================================
# Part of: installers/lib/
# Purpose: The rules phase 02 uses to choose the model server. The vLLM
#          engine (docker-compose.vllm.yml) serves a vLLM catalog model on one
#          NVIDIA GPU (datacenter Blackwell or the RTX PRO 6000 class);
#          llama.cpp (llama-server) serves a GGUF everywhere else. Which vLLM
#          model fits which GPU is
#          scripts/select-model.py --llm-runtime vllm's decision.
#
# Expects: nothing (arguments only)
# Provides: ods_vllm_route_blocker(), ods_vllm_auto_considered(),
#           ods_vllm_image_from_lock()
#
# Modder notes:
#   scripts/resolve-compose-stack.sh refuses LLM_BACKEND=vllm with the same
#   routes ods_vllm_route_blocker names, so the installer never selects a
#   stack the resolver rejects.
#
# This file is sourced; it must not change the caller's shell options.
# ============================================================================

# ods_vllm_route_blocker KERNEL WSL GPU_BACKEND ODS_MODE EXTERNAL_URL NATIVE_URL GATEWAY_ONLY
# Prints why this install cannot run the vLLM engine, or nothing when it can.
# KERNEL is `uname -s`; WSL and GATEWAY_ONLY are true or false; EXTERNAL_URL
# is the external model server this run uses (a flag or the installation's
# retained one), NATIVE_URL the host-native llama-server's origin. The vLLM
# engine runs in local mode only: a vLLM installation's reruns
# (scripts/preserve-active-model.py) and `ods mode` (scripts/mode-switch.sh)
# keep it local, since hybrid mode was never proven with it.
ods_vllm_route_blocker() {
    local kernel="$1" wsl="$2" gpu_backend="$3" ods_mode="$4"
    local external_url="$5" native_url="$6" gateway_only="$7"
    if [[ "$kernel" != "Linux" ]]; then
        printf 'the vLLM engine runs only on Linux, and this is %s\n' "${kernel:-an unknown system}"
    elif [[ "$wsl" == "true" ]]; then
        printf 'this is WSL, where ODS runs llama.cpp\n'
    elif [[ "$ods_mode" == "cloud" ]]; then
        printf 'ODS runs in cloud mode, which has no local model server\n'
    elif [[ "$ods_mode" != "local" ]]; then
        printf 'ODS_MODE=%s, and the vLLM engine runs only in local mode\n' "$ods_mode"
    elif [[ "$gateway_only" == "true" ]]; then
        printf '%s\n' '--gateway-only serves an external model'
    elif [[ -n "$external_url" ]]; then
        printf 'an external model server (%s) serves the model\n' "$external_url"
    elif [[ -n "$native_url" ]]; then
        printf 'a host-native llama-server (%s) serves the model\n' "$native_url"
    elif [[ "$gpu_backend" != "nvidia" ]]; then
        printf 'the GPU backend is %s, and the vLLM engine runs only on NVIDIA GPUs\n' "${gpu_backend:-unknown}"
    fi
}

# ods_vllm_auto_considered EXISTING_INSTALL RETAINED_BACKEND RESELECT
# Without --llm-backend, whether this run may choose vLLM: a fresh install,
# a rerun of a vLLM installation, or --reselect-model. A rerun of a llama.cpp
# installation keeps its active model (phase 02 preserves it), so it stays on
# llama.cpp until the owner asks for vLLM or a new recommendation.
ods_vllm_auto_considered() {
    local existing_install="$1" retained_backend="$2" reselect="$3"
    [[ "$existing_install" != "true" || "$retained_backend" == "vllm" || "$reselect" == "true" ]]
}

# ods_vllm_image_from_lock DEPENDENCY_LOCK_JSON
# Prints the digest-pinned vLLM image (config/dependency-lock.json entry
# vllm.vllm-server); fails when the lock has no such entry.
ods_vllm_image_from_lock() {
    local lock="$1" image
    image="$(jq -r '[.entries[] | select(.id == "vllm.vllm-server") | .value] | if length == 1 then .[0] else "" end' "$lock")" || return 1
    [[ "$image" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] || return 1
    printf '%s\n' "$image"
}
