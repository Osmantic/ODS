#!/bin/bash
# Model-route cases for the Linux install readiness card.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$ROOT_DIR/installers/lib/readiness-summary.sh"

pass=0
fail=0
check() {
    if eval "$1"; then
        printf 'PASS %s\n' "$2"
        pass=$((pass + 1))
    else
        printf 'FAIL %s\n' "$2" >&2
        fail=$((fail + 1))
    fi
}

# Do not touch a real endpoint: these stubs prove the summary uses the
# selected model route, not a vacant local llama-server port.
external_llm_resolve_model() {
    [[ "$1" == openai-compatible && "$2" == http://192.0.2.10:8080 &&
       "$3" == Qwen3.6-test && "$4" == Qwen3.6-test &&
       "${MOCK_EXTERNAL_READY:-false}" == true ]]
}
_ods_readiness_http_code() {
    [[ "$1" == http://127.0.0.1:8080/health ]] && printf '200' || printf '000'
}
_ods_readiness_container_state() { printf 'running'; }

export EXTERNAL_LLM_URL=http://192.0.2.10:8080
export EXTERNAL_LLM_PROVIDER=openai-compatible
export EXTERNAL_LLM_MODEL=Qwen3.6-test
export ODS_MODE=local
export MOCK_EXTERNAL_READY=true

external_line="$(ods_readiness_model_line 8080 /health ods-llama-server 4000)"
check '[[ "$external_line" == External\ LLM\|* ]]' 'external route replaces local llama-server entry'
external_output="$(printf '%s\n' "$external_line" | ods_readiness_summary)"
check '[[ "$external_output" == *"Ready now: 1/1"* && "$external_output" == *"[OK] External LLM"* ]]' 'reachable selected external model is ready'
check '[[ "$external_output" != *"llama-server"* ]]' 'external mode never probes vacant local CPU port'

export MOCK_EXTERNAL_READY=false
external_output="$(printf '%s\n' "$external_line" | ods_readiness_summary)"
check '[[ "$external_output" == *"Ready now: 0/1"* && "$external_output" == *"external model unavailable"* ]]' 'unavailable external model is fail-closed'

export MOCK_EXTERNAL_READY=true
export EXTERNAL_LLM_MODEL=
external_output="$(printf '%s\n' "$external_line" | ods_readiness_summary)"
check '[[ "$external_output" == *"Ready now: 0/1"* ]]' 'incomplete external configuration is fail-closed'

export EXTERNAL_LLM_MODEL=Qwen3.6-test
unset -f external_llm_resolve_model
external_output="$(printf '%s\n' "$external_line" | ods_readiness_summary)"
check '[[ "$external_output" == *"Ready now: 0/1"* ]]' 'missing external discovery helper is fail-closed'

unset EXTERNAL_LLM_URL EXTERNAL_LLM_PROVIDER EXTERNAL_LLM_MODEL
local_line="$(ods_readiness_model_line 8080 /health ods-llama-server 4000)"
check '[[ "$local_line" == llama-server\|* ]]' 'internal CPU model keeps local llama-server entry'
local_output="$(printf '%s\n' "$local_line" | ods_readiness_summary)"
check '[[ "$local_output" == *"Ready now: 1/1"* && "$local_output" == *"[OK] llama-server"* ]]' 'internal CPU model health is still checked'

export ODS_MODE=cloud
cloud_line="$(ods_readiness_model_line 8080 /health ods-llama-server 4000)"
check '[[ -z "$cloud_line" ]]' 'cloud mode does not claim a local llama-server'
check 'grep -Fq "ods_readiness_model_line" "$ROOT_DIR/installers/phases/13-summary.sh"' 'Linux installer uses model-route summary selector'

printf '%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]
