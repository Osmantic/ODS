#!/usr/bin/env bash
# Shared installed-route probes for the inference validation entry points.
# Callers capture explicit diagnostic URL/HOST/PORT overrides before loading
# installation .env. Override endpoints do not inherit the gateway credential.

# shellcheck source=lib/preflight-llm-route.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/preflight-llm-route.sh"

ods_inference_http() {
    local path="$1" timeout="$2" payload="${3:-}" base="${4:-$_ODS_PROBE_API_URL}" response status
    local -a options=(--silent --max-time "$timeout" --write-out $'\n%{http_code}')
    [[ -z "$payload" ]] || options+=(-H 'Content-Type: application/json' --data "$payload")
    _ODS_PROBE_ERROR="HTTP 000"
    if [[ -n "$_ODS_PROBE_TOKEN" ]]; then
        response=$(curl -H @<(printf 'Authorization: Bearer %s\n' "$_ODS_PROBE_TOKEN") \
            "${options[@]}" "$base/$path" 2>/dev/null) || return 1
    else
        response=$(curl "${options[@]}" "$base/$path" 2>/dev/null) || return 1
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
    local port="${OLLAMA_PORT:-${LLAMA_SERVER_PORT:-${SERVICE_PORTS[llama-server]:-11434}}}"
    if [[ -n "${_ODS_DIAGNOSTIC_URL:-}" ]]; then
        url="$_ODS_DIAGNOSTIC_URL"
    elif [[ -n "${_ODS_DIAGNOSTIC_HOST:-}${_ODS_DIAGNOSTIC_PORT:-}" ]]; then
        url="http://${_ODS_DIAGNOSTIC_HOST:-localhost}:${_ODS_DIAGNOSTIC_PORT:-$port}"
    elif [[ "${ODS_MODEL_SWITCHBOARD:-enabled}" == enabled ]] || ods_preflight_uses_litellm; then
        gateway=true
        url="http://127.0.0.1:${LITELLM_PORT:-4000}"
        _ODS_PROBE_TOKEN="${LITELLM_KEY:-${LITELLM_MASTER_KEY:-}}"
        if [[ -z "$_ODS_PROBE_TOKEN" ]]; then
            _ODS_PROBE_ERROR="gateway authentication unavailable (LITELLM_KEY missing)"
            return 1
        fi
    else
        url="http://localhost:$port"
    fi
    _ODS_PROBE_API_URL="${url%/}"
    [[ "$_ODS_PROBE_API_URL" == */v1 ]] || _ODS_PROBE_API_URL+=/v1
    if [[ "${1:-}" == health ]]; then
        local health_path=health
        [[ "$gateway" == false ]] || health_path=health/readiness
        ods_inference_http "$health_path" "${2:-10}" "" "${_ODS_PROBE_API_URL%/v1}" || return 1
        _ODS_PROBE_REACHABLE=true
    fi
    ods_inference_http models 10 || return 1
    _ODS_PROBE_REACHABLE=true
    _ODS_PROBE_MODEL=$(printf '%s' "$_ODS_PROBE_RESPONSE" | python3 -c '
import json, sys
try:
    document = json.load(sys.stdin)
    if "error" in document:
        raise ValueError("model discovery error")
    model = document["data"][0]["id"]
    if not isinstance(model, str) or not model.strip():
        raise ValueError("missing model")
    print(model)
except (ValueError, LookupError, TypeError, AttributeError):
    sys.exit(1)
' 2>/dev/null) || {
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

# Structural completion validation from upstream #8096, shared by the
# installed-route consumers. Preserve reasoning-only thinking-model replies.
_completion_ok() {
    python3 -c '
import json, sys

def has_weather_call(message):
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if (call.get("type") == "function" and function.get("name") == "get_weather"
                and isinstance(arguments, dict) and arguments.get("city") == "Tokyo"):
            return True
    return False

try:
    document = json.load(sys.stdin)
    if "error" in document:
        raise ValueError("API error body")
    message = document["choices"][0]["message"]
    if sys.argv[1] == "tool":
        ok = has_weather_call(message)
    else:
        text = message.get("content") or message.get("reasoning_content")
        ok = isinstance(text, str) and bool(text.strip())
        if ok and sys.argv[1] == "text-output":
            print(text)
except (ValueError, LookupError, TypeError, AttributeError):
    ok = False  # not a chat completion: an error body, HTML, or nothing
sys.exit(0 if ok else 1)
' "$1"
}

ods_inference_chat() {
    local kind="$1" prompt="$2" timeout="${3:-30}" payload
    payload=$(python3 - "$_ODS_PROBE_MODEL" "$prompt" "$kind" <<'PY'
import json, sys
model, prompt, kind = sys.argv[1:]
payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 100}
if kind == "tool":
    payload.update(tools=[{"type": "function", "function": {
        "name": "get_weather", "description": "Get weather", "parameters": {
            "type": "object", "properties": {"city": {"type": "string", "enum": ["Tokyo"]}}, "required": ["city"]
        }}}], tool_choice="auto")
print(json.dumps(payload))
PY
    ) || return 1
    ods_inference_http chat/completions "$timeout" "$payload" || return 1
    _ODS_PROBE_ERROR="no valid assistant answer"
    if [[ "$kind" == tool ]]; then
        _ODS_PROBE_ERROR="no valid get_weather call for Tokyo"
        printf '%s' "$_ODS_PROBE_RESPONSE" | _completion_ok tool
    else
        _ODS_PROBE_CONTENT=$(printf '%s' "$_ODS_PROBE_RESPONSE" | _completion_ok text-output)
    fi
}
