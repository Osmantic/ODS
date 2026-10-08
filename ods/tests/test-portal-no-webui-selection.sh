#!/usr/bin/env bash
# Ordinary Portal installs can omit WebUI without leaving users with no chat UI.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture="$(mktemp -d)"
trap 'rm -rf -- "$fixture/existing"; rm -f -- "$fixture/output" "$fixture/log"; rmdir -- "$fixture"' EXIT

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
for optional in --voice --rag; do
    if env -u ODS_GATEWAY_ONLY -u ENABLE_OPEN_WEBUI \
        INSTALL_DIR="$fixture/install" LOG_FILE="$fixture/log" \
        "$ROOT/install-core.sh" --no-webui --pixel "$optional" \
        --non-interactive --skip-docker >"$fixture/output" 2>&1; then
        echo "FAIL: $optional accepted without its WebUI controls" >&2; exit 1
    fi
    grep -q 'currently require Open WebUI' "$fixture/output" || {
        echo "FAIL: $optional dependency error was unclear" >&2; exit 1
    }
done

# An installation whose Extensions Library added voice and RAG without Open
# WebUI keeps that selection on a rerun. The log directory is missing on
# purpose: install-core stops at the log check right after the selection
# checks, so nothing is installed or changed.
existing="$fixture/existing"
mkdir -p "$existing/extensions/services/whisper" "$existing/extensions/services/qdrant"
printf 'ENABLE_OPEN_WEBUI=false\n' > "$existing/.env"
: > "$existing/extensions/services/whisper/compose.yaml"
: > "$existing/extensions/services/qdrant/compose.yaml"
env -u ODS_GATEWAY_ONLY -u ENABLE_OPEN_WEBUI \
    INSTALL_DIR="$existing" LOG_FILE="$fixture/missing/log" \
    "$ROOT/install-core.sh" --pixel --non-interactive --skip-docker \
    >"$fixture/output" 2>&1 || true
if grep -q 'currently require Open WebUI' "$fixture/output" \
    || ! grep -q 'Keeping the installed voice and RAG services without Open WebUI' "$fixture/output" \
    || ! grep -q 'Installer log directory is missing or unsafe' "$fixture/output"; then
    echo 'FAIL: a rerun refused the voice and RAG selection the Library installed' >&2
    cat "$fixture/output" >&2
    exit 1
fi
[[ "$(cat "$existing/.env")" == 'ENABLE_OPEN_WEBUI=false' && ! -e "$fixture/missing" ]] || {
    echo 'FAIL: the rerun selection check changed installation state' >&2; exit 1;
}
mkdir -p "$existing/extensions/services/ods-proxy"
: > "$existing/extensions/services/ods-proxy/compose.yaml"
if env -u ODS_GATEWAY_ONLY -u ENABLE_OPEN_WEBUI \
    INSTALL_DIR="$existing" LOG_FILE="$fixture/missing/log" \
    "$ROOT/install-core.sh" --pixel --non-interactive --skip-docker \
    >"$fixture/output" 2>&1 \
    || ! grep -q 'currently require Open WebUI' "$fixture/output"; then
    echo 'FAIL: a rerun accepted the ODS proxy without Open WebUI' >&2; exit 1
fi

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
    if [[ "${PORTAL_TEST_REQUIRE_GID:-}" == true ]]; then
        [[ "${PIXEL_INGRESS_GID:-}" == 1 ]] || return 1
    fi
    case "${PORTAL_TEST_MODE:-}" in
        safe) printf 'dashboard\npixel-edge\n' ;;
        unsafe) printf 'dashboard\npixel-edge\nopen-webui\n' ;;
        *) return 1 ;;
    esac
}
DOCKER_COMPOSE_CMD=portal_compose_stub
INSTALL_DIR="$fixture" PORTAL_TEST_MODE=safe ods_compose_assert_no_webui -f fake.yml
PIXEL_INGRESS_GID='' PORTAL_TEST_REQUIRE_GID=true INSTALL_DIR="$fixture" \
    PORTAL_TEST_MODE=safe ods_compose_assert_no_webui_before_pixel_identity -f fake.yml
[[ -z "${PIXEL_INGRESS_GID:-}" ]] || {
    echo 'FAIL: early Compose check leaked its placeholder Pixel GID' >&2; exit 1;
}
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
    PIXEL_INGRESS_GID='' INSTALL_DIR="$ROOT" \
        ods_compose_assert_no_webui_before_pixel_identity "${flags[@]}"
    if COMPOSE_PROFILES=gateway-webui INSTALL_DIR="$ROOT" \
        ods_compose_assert_no_webui "${flags[@]}" 2>/dev/null; then
        echo 'FAIL: inherited gateway-webui profile bypassed the guard' >&2
        exit 1
    fi
fi

echo 'PASS: ordinary Portal selection hides WebUI and rejects unsafe profiles'
