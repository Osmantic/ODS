#!/usr/bin/env bash
# Select the endpoint that serves the installed model, not the detected GPU.

# True when the model runs in a host-native llama-server outside the stack
# (the Windows Portal); containers and clients reach it through LiteLLM.
ods_preflight_host_native_llm() {
    [[ -n "${NATIVE_LLM_BASE_URL:-}" ]]
}

# True when .env selects the vLLM engine (LLM_BACKEND=vllm,
# docker-compose.vllm.yml): the vllm-server container serves the model on the
# ODS network without a host port, and the in-stack llama-server is off.
# Clients reach it through model-router and LiteLLM.
ods_preflight_vllm_engine() {
    local backend
    backend=$(printf '%s' "${LLM_BACKEND:-}" | tr '[:upper:]' '[:lower:]')
    [[ "$backend" == "vllm" ]]
}

ods_preflight_uses_litellm() {
    # ods-preflight.sh runs under macOS /bin/bash 3.2, which has no Bash 4 case conversion.
    local mode
    mode=$(printf '%s' "${ODS_MODE:-local}" | tr '[:upper:]' '[:lower:]')
    ods_preflight_host_native_llm || ods_preflight_vllm_engine \
        || [[ -n "${EXTERNAL_LLM_URL:-}" ]] || [[ "$mode" == "cloud" ]]
}

# Status leaves out the in-stack inference services a LiteLLM route does not
# start: llama-server always, model-router unless a host-native llama-server
# keeps it for model switching or the vLLM engine is served through it.
ods_status_skips_managed_inference() {
    local sid="$1"
    ods_preflight_uses_litellm || return 1
    [[ "$sid" == "llama-server" ]] && return 0
    [[ "$sid" == "model-router" ]] && ! ods_preflight_host_native_llm && ! ods_preflight_vllm_engine
}
