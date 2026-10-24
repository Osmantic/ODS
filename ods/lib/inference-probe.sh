#!/usr/bin/env bash
# Shared installed-route probes for the inference validation entry points.
# Callers capture explicit diagnostic URL/HOST/PORT overrides before loading
# installation .env. Override endpoints do not inherit the gateway credential.

# shellcheck source=lib/preflight-llm-route.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/preflight-llm-route.sh"

ods_inference_http() {
    local path="$1" timeout="$2" payload="${3:-}" response status
    local -a options=(--silent --max-time "$timeout" --write-out $'\n%{http_code}')
    [[ -z "$payload" ]] || options+=(-H 'Content-Type: application/json' --data "$payload")
    _ODS_PROBE_ERROR="HTTP 000"
    if [[ -n "$_ODS_PROBE_TOKEN" ]]; then
        response=$(curl -H @<(printf 'Authorization: Bearer %s\n' "$_ODS_PROBE_TOKEN") \
            "${options[@]}" "$_ODS_PROBE_API_URL/$path" 2>/dev/null) || return 1
    else
        response=$(curl "${options[@]}" "$_ODS_PROBE_API_URL/$path" 2>/dev/null) || return 1
    fi
    status="${response##*$'\n'}"
    _ODS_PROBE_ERROR="HTTP $status"
    [[ "$status" =~ ^2[0-9][0-9]$ ]] || return 1
    _ODS_PROBE_RESPONSE="${response%$'\n'*}"
}

ods_inference_prepare() {
    _ODS_PROBE_REACHABLE=false
    _ODS_PROBE_TOKEN=""
    _ODS_PROBE_MODEL=""
    local url="" gateway=false
    if [[ -n "${_ODS_DIAGNOSTIC_URL:-}" ]]; then
        url="$_ODS_DIAGNOSTIC_URL"
    elif [[ -n "${_ODS_DIAGNOSTIC_HOST:-}${_ODS_DIAGNOSTIC_PORT:-}" ]]; then
        url="http://${_ODS_DIAGNOSTIC_HOST:-localhost}:${_ODS_DIAGNOSTIC_PORT:-${OLLAMA_PORT:-11434}}"
    elif [[ "${ODS_MODEL_SWITCHBOARD:-enabled}" == enabled ]] || ods_preflight_uses_litellm; then
        gateway=true
        url="http://127.0.0.1:${LITELLM_PORT:-4000}"
        _ODS_PROBE_TOKEN="${LITELLM_KEY:-${LITELLM_MASTER_KEY:-}}"
        if [[ -z "$_ODS_PROBE_TOKEN" ]]; then
            _ODS_PROBE_ERROR="gateway authentication unavailable (LITELLM_KEY missing)"
            return 1
        fi
    else
        url="http://localhost:${OLLAMA_PORT:-${LLAMA_SERVER_PORT:-${SERVICE_PORTS[llama-server]:-11434}}}"
    fi
    _ODS_PROBE_API_URL="${url%/}"
    [[ "$_ODS_PROBE_API_URL" == */v1 ]] || _ODS_PROBE_API_URL+=/v1
    ods_inference_http models 10 || return 1
    _ODS_PROBE_REACHABLE=true
    _ODS_PROBE_MODEL=$(printf '%s' "$_ODS_PROBE_RESPONSE" | jq -er \
        'select(type == "object" and (has("error") | not)) | .data[0].id | select(type == "string" and test("\\S"))' \
        2>/dev/null) || {
        _ODS_PROBE_ERROR="model discovery returned no valid model"
        return 1
    }
    if [[ "$gateway" == true ]]; then
        if [[ "${ODS_MODEL_SWITCHBOARD:-enabled}" == enabled ]]; then
            _ODS_PROBE_MODEL="ods/current"
        else
            _ODS_PROBE_MODEL="default"
        fi
    fi
}

ods_inference_chat() {
    local kind="$1" prompt="$2" timeout="${3:-30}" payload
    payload=$(jq -n --arg model "$_ODS_PROBE_MODEL" --arg prompt "$prompt" --arg kind "$kind" '
        {model: $model, messages: [{role: "user", content: $prompt}], max_tokens: 100}
        + if $kind == "tool" then {tools: [{type: "function", function: {
            name: "get_weather", description: "Get weather", parameters: {
                type: "object", properties: {city: {type: "string"}}, required: ["city"]
            }}}], tool_choice: "auto"} else {} end') || return 1
    ods_inference_http chat/completions "$timeout" "$payload" || return 1
    _ODS_PROBE_ERROR="no valid assistant answer"
    if [[ "$kind" == tool ]]; then
        _ODS_PROBE_ERROR="no valid get_weather call for Tokyo"
        printf '%s' "$_ODS_PROBE_RESPONSE" | jq -e '
            select(type == "object" and (has("error") | not))
            | [.choices[0].message.tool_calls[]?
                | select(.type == "function" and .function.name == "get_weather")
                | .function.arguments | fromjson? | select(type == "object" and .city == "Tokyo")]
            | length > 0' >/dev/null 2>&1
    else
        _ODS_PROBE_CONTENT=$(printf '%s' "$_ODS_PROBE_RESPONSE" | jq -er '
            select(type == "object" and (has("error") | not))
            | .choices[0].message.content | select(type == "string" and test("\\S"))' 2>/dev/null)
    fi
}
