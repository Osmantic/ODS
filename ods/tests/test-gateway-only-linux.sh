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
echo 'PASS: gateway-only requires an upstream and selects the API-only stack'
