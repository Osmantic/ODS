#!/bin/bash
# External Ollama / LM Studio discovery and validation helpers.

# Reduce a provider's model id to a comparable name.
#
# Ollama reports names as `<model>:<tag>`. The tag is the default `latest`
# unless the user asked for a specific one, so `ollama pull qwen3-30b-a3b`
# is listed as `qwen3-30b-a3b:latest` — the exact model ODS wants, under a
# name that used to normalize to `qwen3-30b-a3b-latest` and match nothing.
#
# Tag handling therefore runs BEFORE the quantization strip: a quant that
# arrives in the tag position (`qwen3.5-2b:q8_0`) is only recognisable once
# the colon has become a separator the quant pattern accepts.
external_llm_normalize_model_name() {
    local value="${1:-}"
    value="${value##*/}"
    value="${value,,}"
    value="${value%.gguf}"
    value="$(printf '%s' "$value" | sed -E \
        -e 's/:latest$//' \
        -e 's/:/-/' \
        -e 's/[-_.](q[0-9]+([_.][a-z0-9]+)*|iq[0-9]+([_.][a-z0-9]+)*|fp(8|16|32)|bf16)([-_.].*)?$//' \
        -e 's/[[:space:]_]+/-/g' \
        -e 's/-+/-/g' \
        -e 's/^-|-$//g')"
    printf '%s\n' "$value"
}

external_llm_model_matches() {
    local expected actual
    expected="$(external_llm_normalize_model_name "${1:-}")"
    actual="$(external_llm_normalize_model_name "${2:-}")"
    [[ -n "$expected" && "$expected" == "$actual" ]]
}

external_llm_find_matching_model() {
    local target="${1:-}" candidate
    while IFS= read -r candidate; do
        if [[ -n "$candidate" ]] && external_llm_model_matches "$target" "$candidate"; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

external_llm_strip_url() {
    local url="${1:-}"
    url="${url%/}"
    case "$url" in
        */api/v1) url="${url%/api/v1}" ;;
        */v1) url="${url%/v1}" ;;
    esac
    printf '%s\n' "$url"
}

external_llm_validate_url() {
    local url="${1:-}"
    EXTERNAL_LLM_VALIDATE_URL="$url" python3 - <<'PY'
import os
import sys
from urllib.parse import urlsplit

try:
    parsed = urlsplit(os.environ["EXTERNAL_LLM_VALIDATE_URL"])
    _ = parsed.port
except (KeyError, ValueError):
    sys.exit(1)

if (
    parsed.scheme not in {"http", "https"}
    or not parsed.hostname
    or parsed.username is not None
    or parsed.password is not None
    or parsed.query
    or parsed.fragment
    or parsed.path.rstrip("/") not in {"", "/v1", "/api/v1"}
):
    sys.exit(1)
PY
}

external_llm_host_url() {
    local url
    url="$(external_llm_strip_url "${1:-}")"
    url="${url/host.docker.internal/127.0.0.1}"
    printf '%s\n' "$url"
}

external_llm_container_url() {
    local url
    url="$(external_llm_strip_url "${1:-}")"
    if [[ "$url" =~ ^(https?://)(localhost|127\.0\.0\.1|\[::1\])([:/].*|$) ]]; then
        url="${BASH_REMATCH[1]}host.docker.internal${BASH_REMATCH[3]}"
    fi
    printf '%s\n' "$url"
}

external_llm_models() {
    local provider="${1:-}" url="${2:-}" response
    url="$(external_llm_host_url "$url")"
    case "$provider" in
        ollama)
            response="$(curl -fsS --max-time 5 "${url}/api/tags" 2>/dev/null)" || return 1
            ;;
        lmstudio)
            response="$(curl -fsS --max-time 5 "${url}/v1/models" 2>/dev/null)" || return 1
            ;;
        *)
            return 2
            ;;
    esac

    python3 -c '
import json
import sys

provider = sys.argv[1]
payload = json.load(sys.stdin)
if provider == "ollama":
    values = (item.get("name") or item.get("model") for item in payload.get("models", []))
else:
    values = (item.get("id") for item in payload.get("data", []))
for value in values:
    if isinstance(value, str) and value:
        print(value)
' "$provider" <<<"$response"
}

external_llm_detect_provider() {
    local url="${1:-}"
    if external_llm_models ollama "$url" >/dev/null 2>&1; then
        printf 'ollama\n'
        return 0
    fi
    if external_llm_models lmstudio "$url" >/dev/null 2>&1; then
        printf 'lmstudio\n'
        return 0
    fi
    return 1
}

external_llm_resolve_model() {
    local provider="${1:-}" url="${2:-}" requested="${3:-}" target="${4:-}"
    local models
    models="$(external_llm_models "$provider" "$url")" || return 1
    if [[ -n "$requested" ]]; then
        if grep -Fqx -- "$requested" <<<"$models"; then
            printf '%s\n' "$requested"
            return 0
        fi
        return 2
    fi
    external_llm_find_matching_model "$target" <<<"$models"
}

external_llm_probe_completion() {
    local url="${1:-}" model="${2:-}" body
    url="$(external_llm_host_url "$url")"
    body="$(
        EXTERNAL_LLM_MODEL_VALUE="$model" python3 - <<'PY'
import json
import os

print(json.dumps({
    "model": os.environ["EXTERNAL_LLM_MODEL_VALUE"],
    "messages": [{"role": "user", "content": "Reply with OK."}],
    "max_tokens": 1,
    "temperature": 0,
    "stream": False,
}))
PY
    )"
    curl -fsS --max-time "${EXTERNAL_LLM_PROBE_TIMEOUT:-60}" \
        -H "Content-Type: application/json" \
        -d "$body" \
        "${url}/v1/chat/completions" >/dev/null
}

external_llm_env_value() {
    local env_file="${1:-}" key="${2:-}" value
    [[ -f "$env_file" ]] || return 1
    value="$(grep -m1 "^${key}=" "$env_file" 2>/dev/null | cut -d= -f2- || true)"
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
    printf '%s\n' "$value"
}
