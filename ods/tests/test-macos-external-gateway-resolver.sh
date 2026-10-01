#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture="$(mktemp -d)"
trap 'rm -rf -- "$fixture"' EXIT

for overlay in docker-compose.base.yml docker-compose.cloud.yml \
    docker-compose.external-llm.yml docker-compose.gateway-only.yml; do
    cp "$root/$overlay" "$fixture/$overlay"
done
mkdir -p "$fixture/scripts"
cp "$root/scripts/resolve-compose-stack.sh" "$fixture/scripts/resolve-compose-stack.sh"
chmod +x "$fixture/scripts/resolve-compose-stack.sh"

resolve_gateway() {
    ODS_GATEWAY_ONLY=true ODS_EXTERNAL_LLM_SELECTED=true ENABLE_OPEN_WEBUI=false \
        "$fixture/scripts/resolve-compose-stack.sh" --script-dir "$fixture" \
        --gpu-backend apple --ods-mode local --tier 1
}

expected='-f docker-compose.base.yml -f docker-compose.cloud.yml -f docker-compose.external-llm.yml -f docker-compose.gateway-only.yml'
[[ "$(resolve_gateway)" == "$expected" ]] || {
    echo 'Apple external gateway selected native inference or omitted a required overlay' >&2
    exit 1
}

if ODS_GATEWAY_ONLY=true ODS_EXTERNAL_LLM_SELECTED=false ENABLE_OPEN_WEBUI=false \
    "$fixture/scripts/resolve-compose-stack.sh" --script-dir "$fixture" \
    --gpu-backend apple --ods-mode local --tier 1 >"$fixture/out" 2>&1; then
    echo 'Apple gateway-only accepted a missing external model route' >&2
    exit 1
fi
grep -q 'requires a local external model route' "$fixture/out"

read_env_value() {
    local line
    line="$(grep -m1 "^${2}=" "$1" 2>/dev/null || true)"
    printf '%s\n' "${line#*=}"
}
ai_err() { printf '%s\n' "$*" >&2; }
ensure_hermes_dashboard_session_token() { :; }
macos_model_store_compose_flags() { printf '%s\n' "$1"; }
eval "$(sed -n '/^_get_base_compose_flags() {/,/^}/p' "$root/installers/macos/ods-macos.sh")"
INSTALL_DIR="$fixture"
printf 'ODS_MODE=local\nODS_GATEWAY_ONLY=true\nEXTERNAL_LLM_URL=http://127.0.0.1:11434\nENABLE_OPEN_WEBUI=false\n' > "$fixture/.env"
[[ "$(_get_base_compose_flags)" == "$expected" ]] || {
    echo 'Mac CLI lost the external gateway when its Compose cache was absent' >&2
    exit 1
}
printf '%s\n' '-f docker-compose.base.yml -f installers/macos/docker-compose.macos.yml' > "$fixture/.compose-flags"
if _get_base_compose_flags >"$fixture/out" 2>&1; then
    echo 'Mac CLI accepted stale native Compose flags for a gateway install' >&2
    exit 1
fi
rm "$fixture/.compose-flags"
rm "$fixture/scripts/resolve-compose-stack.sh"
if _get_base_compose_flags >"$fixture/out" 2>&1; then
    echo 'Mac CLI silently fell back to native inference without its resolver' >&2
    exit 1
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    services="$(EXTERNAL_LLM_CONTAINER_URL=http://host.docker.internal:11434 \
        EXTERNAL_LLM_MODEL=test-model EXTERNAL_LLM_PROVIDER=openai-compatible \
        WEBUI_SECRET=test-placeholder docker compose \
        -f "$root/docker-compose.base.yml" \
        -f "$root/docker-compose.cloud.yml" \
        -f "$root/extensions/services/litellm/compose.yaml" \
        -f "$root/docker-compose.external-llm.yml" \
        -f "$root/docker-compose.gateway-only.yml" config --services)"
    grep -qx 'litellm' <<< "$services" || {
        echo 'Apple external gateway lost LiteLLM' >&2; exit 1;
    }
    ! grep -Eq '^(llama-server|model-router|llama-server-ready|open-webui)$' <<< "$services" || {
        echo 'Apple external gateway retained managed inference or WebUI' >&2; exit 1;
    }
fi

echo 'PASS: Mac external gateway resolver and CLI fallback omit native inference'
