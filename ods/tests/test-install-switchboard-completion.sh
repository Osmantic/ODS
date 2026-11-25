#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/installers/lib/readiness-summary.sh"
export ODS_MODEL_SWITCHBOARD=enabled LITELLM_KEY=test-key ODS_AGENT_KEY=agent-test
line="$(ods_readiness_model_line 8080 /health ods-llama-server 4000)"
[[ "$line" == *'|model-route' ]]
curl() {
    [[ "$*" == *'/v1/model/status'* ]] && { printf '{}'; return 0; }
    # The key reaches curl in a header file (-H @file), never as an argument.
    [[ "$*" != *test-key* ]] || return 1
    local arg headers=""
    for arg in "$@"; do
        if [[ "$arg" == @* && "$arg" != @- ]]; then
            headers+="$(cat "${arg#@}")"$'\n'
        fi
    done
    [[ "$*" == *'ods/current'* && "$headers" == *'Authorization: Bearer test-key'* ]] || return 1
    printf '%s' "$RESPONSE"
    return "${CURL_STATUS:-0}"
}
for RESPONSE in '{}' '{"choices":[]}' '{"choices":[{"message":{"content":" "}}]}' 'not json'; do
    ! _ods_readiness_model_route_available http://localhost:4000/v1/chat/completions
done
RESPONSE='{"choices":[{"message":{"content":"OK"}}]}'
_ods_readiness_model_route_available http://localhost:4000/v1/chat/completions
CURL_STATUS=22
! _ods_readiness_model_route_available http://localhost:4000/v1/chat/completions
CURL_STATUS=0
[[ "$(printf '%s\n' "$line" | ods_readiness_summary)" == *'Ready now: 1/1'* ]]
RESPONSE='{}'
[[ "$(printf '%s\n' "$line" | ods_readiness_summary)" == *'Ready now: 0/1'* ]]
echo 'PASS: switchboard completion controls shared install readiness'
