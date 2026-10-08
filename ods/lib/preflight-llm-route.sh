#!/usr/bin/env bash
# Select the endpoint that serves the installed model, not the detected GPU.

# True when the model runs in a host-native llama-server outside the stack
# (the Windows Portal); containers and clients reach it through LiteLLM.
ods_preflight_host_native_llm() {
    [[ -n "${NATIVE_LLM_BASE_URL:-}" ]]
}

ods_preflight_uses_litellm() {
    local mode="${ODS_MODE:-local}"
    ods_preflight_host_native_llm || [[ -n "${EXTERNAL_LLM_URL:-}" ]] || [[ "${mode,,}" == "cloud" ]]
}

# Status leaves out the in-stack inference services a LiteLLM route does not
# start: llama-server always, model-router unless a host-native llama-server
# keeps it for model switching.
ods_status_skips_managed_inference() {
    local sid="$1"
    ods_preflight_uses_litellm || return 1
    [[ "$sid" == "llama-server" ]] && return 0
    [[ "$sid" == "model-router" ]] && ! ods_preflight_host_native_llm
}
