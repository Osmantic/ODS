#!/usr/bin/env bash
# Gateway-only must fail before installation without an external model and
# select the no-WebUI Compose overlay only for the API-first choice.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture="$(mktemp -d)"
trap 'rm -f -- "$fixture/output" "$fixture/log"; rmdir -- "$fixture"' EXIT

if env -u EXTERNAL_LLM_URL -u ODS_GATEWAY_ONLY \
    INSTALL_DIR="$fixture/install" LOG_FILE="$fixture/log" \
    "$ROOT/install-core.sh" --gateway-only --non-interactive --skip-docker \
    >"$fixture/output" 2>&1; then
    echo 'FAIL: gateway-only accepted a missing upstream' >&2
    exit 1
fi
grep -q 'requires --external-llm-url' "$fixture/output" || {
    echo 'FAIL: missing-upstream error was unclear' >&2; exit 1;
}
[[ ! -e "$fixture/install" && ! -e "$fixture/log" ]] || {
    echo 'FAIL: missing-upstream path changed installation state' >&2; exit 1;
}

resolve() {
    ODS_GATEWAY_ONLY=true ENABLE_OPEN_WEBUI="$1" \
        EXTERNAL_LLM_URL=http://127.0.0.1:18080 \
        "$ROOT/scripts/resolve-compose-stack.sh" --script-dir "$ROOT" \
        --ods-mode local --gpu-backend nvidia
}
without_ui="$(resolve false)"
with_ui="$(resolve true)"
[[ "$without_ui" == *'docker-compose.external-llm.yml'* \
    && "$without_ui" == *'docker-compose.gateway-only.yml'* ]] || {
    echo 'FAIL: gateway-only Compose overlays missing' >&2; exit 1;
}
[[ "$with_ui" == *'docker-compose.external-llm.yml'* \
    && "$with_ui" != *'docker-compose.gateway-only.yml'* ]] || {
    echo 'FAIL: opted-in WebUI remained profiled out' >&2; exit 1;
}

# The installer checks the final active service set before any image pull and
# again before launch. An inherited Compose profile must fail closed.
SCRIPT_DIR="$ROOT"
source "$ROOT/installers/lib/compose-select.sh"
gateway_compose_stub() {
    case "${GATEWAY_TEST_MODE:-}" in
        safe) printf 'dashboard\nlitellm\n' ;;
        managed) printf 'litellm\nllama-server\nmodel-router\n' ;;
        *) return 1 ;;
    esac
}
DOCKER_COMPOSE_CMD=gateway_compose_stub
GATEWAY_TEST_MODE=safe ods_gateway_assert_no_managed_inference -f fake.yml
if GATEWAY_TEST_MODE=managed ods_gateway_assert_no_managed_inference -f fake.yml 2>/dev/null; then
    echo 'FAIL: gateway-only accepted active managed inference' >&2; exit 1;
fi
if GATEWAY_TEST_MODE=error ods_gateway_assert_no_managed_inference -f fake.yml 2>/dev/null; then
    echo 'FAIL: gateway-only accepted an unverified Compose service set' >&2; exit 1;
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    cd "$ROOT"
    compose_flags=(-f docker-compose.base.yml -f docker-compose.cpu.yml
        -f extensions/services/litellm/compose.yaml
        -f docker-compose.external-llm.yml -f docker-compose.gateway-only.yml)
    if COMPOSE_PROFILES=local-inference WEBUI_SECRET=testing \
        EXTERNAL_LLM_CONTAINER_URL=http://host.docker.internal:18080 \
        DOCKER_COMPOSE_CMD='docker compose' \
        ods_gateway_assert_no_managed_inference "${compose_flags[@]}" 2>/dev/null; then
        echo 'FAIL: inherited local-inference profile bypassed gateway guard' >&2; exit 1;
    fi
fi
echo 'PASS: gateway-only requires an upstream and selects the API-only stack'
