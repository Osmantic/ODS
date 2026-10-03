#!/usr/bin/env bash
# The macOS native llama-server port must be relocatable end to end.
#
# ODS_NATIVE_LLAMA_PORT is the single source of truth: the host bind, the
# container readiness probe (OLLAMA_PORT) and every container-facing URL must
# follow it. Previously install-macos.sh and env-generator.sh hard-wired 8080
# in all three layers, so an operator whose 8080 was taken could not complete
# an install. Mirrors tests/contracts/test-windows-amd-local-compose.sh, which
# asserts no stale port survives a custom-port render.
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
    for candidate in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$candidate" ]] && exec "$candidate" "$0" "$@"
    done
    echo "[FAIL] Bash 4+ is required" >&2
    exit 1
fi
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_GENERATOR="${ODS_ENV_GENERATOR_UNDER_TEST:-$ROOT_DIR/installers/macos/lib/env-generator.sh}"
INSTALLER="${ODS_INSTALL_MACOS_UNDER_TEST:-$ROOT_DIR/installers/macos/install-macos.sh}"
COMPOSE="${ODS_MACOS_COMPOSE_UNDER_TEST:-$ROOT_DIR/installers/macos/docker-compose.macos.yml}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

for f in "$ENV_GENERATOR" "$INSTALLER" "$COMPOSE"; do
    [[ -f "$f" ]] || fail "missing $f"
done

CUSTOM_PORT=18081

# The generator only shells out for the Docker CPU budget and hostname.
STUB="$TMP_DIR/bin"; mkdir -p "$STUB"
printf '#!/bin/sh\nexit 1\n' > "$STUB/docker"
printf '#!/bin/sh\necho ods-test-mac\n' > "$STUB/hostname"
chmod +x "$STUB/docker" "$STUB/hostname"

render_env() {
    local install_dir="$1" port="$2"
    mkdir -p "$install_dir/config/searxng"
    (
        export PATH="$STUB:$PATH" ODS_INSTALL_DIR="$install_dir" HOME="$TMP_DIR/home"
        export ODS_NATIVE_LLAMA_PORT="$port"
        # shellcheck source=/dev/null
        . "$ROOT_DIR/installers/macos/lib/constants.sh"
        # shellcheck source=/dev/null
        . "$ROOT_DIR/installers/macos/lib/tier-map.sh"
        # shellcheck source=/dev/null
        . "$ENV_GENERATOR"
        # shellcheck disable=SC2329  # called by generate_ods_env
        calculate_llama_cpu_budget() { printf '4 1 8\n'; }
        SYSTEM_RAM_GB=64
        DOCKER_BACKEND="docker-desktop"
        ODS_MODEL_SWITCHBOARD="observe"
        resolve_tier_config 1 || exit 3
        generate_ods_env "$install_dir" 1 true
    ) >"$install_dir/gen.log" 2>&1 \
        || fail "generate_ods_env failed: $(tail -n 3 "$install_dir/gen.log")"
    [[ -f "$install_dir/.env" ]] || fail "no .env produced"
}

INSTALL="$TMP_DIR/install"
render_env "$INSTALL" "$CUSTOM_PORT"
env_file="$INSTALL/.env"

val() { grep -m1 "^$1=" "$env_file" | cut -d= -f2-; }

[[ "$(val ODS_NATIVE_LLAMA_PORT)" == "$CUSTOM_PORT" ]] \
    || fail "ODS_NATIVE_LLAMA_PORT is $(val ODS_NATIVE_LLAMA_PORT), expected $CUSTOM_PORT"
pass "ODS_NATIVE_LLAMA_PORT honours a pre-set port"

[[ "$(val OLLAMA_PORT)" == "$CUSTOM_PORT" ]] \
    || fail "OLLAMA_PORT (the container readiness probe) is $(val OLLAMA_PORT), expected $CUSTOM_PORT"
pass "the container readiness probe port follows the native port"

for key in LLM_API_URL HERMES_LLM_BASE_URL; do
    v="$(val "$key")"
    [[ "$v" == *":$CUSTOM_PORT"* ]] \
        || fail "$key does not use the custom port: $v"
done
pass "container-facing LLM URLs use the custom port"

# The Windows analogue of this assertion: no stale native port survives.
if grep -nE '(host\.docker\.internal|127\.0\.0\.1|localhost):8080' "$env_file"; then
    fail "a hard-wired :8080 survived a custom-port render (lines above)"
fi
pass "no hard-wired :8080 survives a custom-port render"

# The installer must not re-write the port or the URLs back to a literal 8080.
if grep -nE 'upsert_env_value[^\n]*"(ODS_NATIVE_LLAMA_PORT|LLM_API_URL|HERMES_LLM_BASE_URL)"[^\n]*:?"?8080' "$INSTALLER"; then
    fail "install-macos.sh still writes a literal 8080 for the native port or its URLs (lines above)"
fi
pass "install-macos.sh derives the native port and URLs from ODS_NATIVE_LLAMA_PORT"

# The macOS compose overlay must parameterise the llama host port.
if grep -nE '(host\.docker\.internal|ODS_MACOS_HOST_GATEWAY[^\n]*):8080' "$COMPOSE"; then
    fail "docker-compose.macos.yml still hard-wires :8080 (lines above)"
fi
pass "docker-compose.macos.yml parameterises the llama host port"
