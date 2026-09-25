#!/usr/bin/env bash
# Select the endpoint that serves the installed model, not the detected GPU.

is_external_lemonade() {
    local external="${LEMONADE_EXTERNAL:-false}"
    local managed="${AMD_INFERENCE_MANAGED:-}"
    local mode="${ODS_MODE:-local}"
    [[ "${external,,}" == "true" ]] || [[ "${mode,,}" == "lemonade" && "${managed,,}" == "false" ]]
}

ods_preflight_uses_litellm() {
    local mode="${ODS_MODE:-local}"
    is_external_lemonade || [[ -n "${EXTERNAL_LLM_URL:-}" ]] || [[ "${mode,,}" == "cloud" ]]
}
