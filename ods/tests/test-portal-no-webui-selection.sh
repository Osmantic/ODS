#!/usr/bin/env bash
# Ordinary Portal installs can omit WebUI without leaving users with no chat UI.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture="$(mktemp -d)"
trap 'rm -f -- "$fixture/output" "$fixture/log"; rmdir -- "$fixture"' EXIT

if env -u ODS_GATEWAY_ONLY -u ENABLE_OPEN_WEBUI \
    INSTALL_DIR="$fixture/install" LOG_FILE="$fixture/log" \
    "$ROOT/install-core.sh" --no-webui --no-pixel --non-interactive --skip-docker \
    >"$fixture/output" 2>&1; then
    echo 'FAIL: fresh ordinary install accepted no chat UI' >&2
    exit 1
fi
grep -q 'requires --pixel' "$fixture/output" || {
    echo 'FAIL: missing Portal error was unclear' >&2; exit 1;
}
[[ ! -e "$fixture/install" && ! -e "$fixture/log" ]] || {
    echo 'FAIL: missing Portal path changed installation state' >&2; exit 1;
}

resolve() {
    ODS_GATEWAY_ONLY=false ENABLE_OPEN_WEBUI="$1" \
        "$ROOT/scripts/resolve-compose-stack.sh" --script-dir "$ROOT" \
        --ods-mode cloud --gpu-backend cpu
}
without_ui="$(resolve false)"
with_ui="$(resolve true)"
[[ "$without_ui" == *'docker-compose.gateway-only.yml'* \
    && "$with_ui" != *'docker-compose.gateway-only.yml'* ]] || {
    echo 'FAIL: Portal-only Compose profile selection is wrong' >&2; exit 1;
}

SCRIPT_DIR="$ROOT"
source "$ROOT/installers/lib/compose-select.sh"
portal_compose_stub() {
    case "${PORTAL_TEST_MODE:-}" in
        safe) printf 'dashboard\npixel-edge\n' ;;
        unsafe) printf 'dashboard\npixel-edge\nopen-webui\n' ;;
        *) return 1 ;;
    esac
}
DOCKER_COMPOSE_CMD=portal_compose_stub
INSTALL_DIR="$fixture" PORTAL_TEST_MODE=safe ods_compose_assert_no_webui -f fake.yml
if INSTALL_DIR="$fixture" PORTAL_TEST_MODE=unsafe \
    ods_compose_assert_no_webui -f fake.yml 2>/dev/null; then
    echo 'FAIL: active WebUI bypassed the no-WebUI guard' >&2; exit 1;
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    export PIXEL_OPENWEBUI_KEY="$(printf 'a%.0s' {1..64})"
    export PIXEL_INGRESS_GID=1234 PIXEL_INGRESS_RUNTIME_DIR="$fixture"
    export PIXEL_PREVIEW_RUNTIME_DIR="$fixture"
    export DASHBOARD_API_KEY="$(printf 'c%.0s' {1..64})"
    export WEBUI_SECRET="$(printf 'b%.0s' {1..64})"
    flags=(-f docker-compose.base.yml
        -f extensions/services/pixel-edge/compose.yaml.disabled
        -f docker-compose.gateway-only.yml)
    DOCKER_COMPOSE_CMD='docker compose'
    INSTALL_DIR="$ROOT" ods_compose_assert_no_webui "${flags[@]}"
    if COMPOSE_PROFILES=gateway-webui INSTALL_DIR="$ROOT" \
        ods_compose_assert_no_webui "${flags[@]}" 2>/dev/null; then
        echo 'FAIL: inherited gateway-webui profile bypassed the guard' >&2
        exit 1
    fi
fi

echo 'PASS: ordinary Portal selection hides WebUI and rejects unsafe profiles'
