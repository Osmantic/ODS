#!/usr/bin/env bash
# `--rebuild-images` must rebuild the local-built services this stack selects.
# Compose refuses a whole build that names a service outside the stack, so a
# disabled ape/token-spy/privacy-shield used to stop every rebuild; and ComfyUI
# was gated on ENABLE_COMFYUI, which the installer never writes to .env.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

eval "$(awk '$0 == "_ods_cli_rebuild_images() {" { f=1 } f { print } f && $0 == "}" { exit }' \
    "$ROOT/ods-cli")"
declare -F _ods_cli_rebuild_images >/dev/null || { echo "FAIL: function not extracted"; exit 1; }

log() { printf '%s\n' "$*"; }
warn() { printf 'WARN %s\n' "$*"; }
_check_docker_access() { :; }
docker() {
    printf '%s\n' "$*" >> "$TMP/calls"
    case "$*" in
        *" config --services")
            [[ "${CONFIG_FAILS:-0}" == 0 ]] || return 1
            printf '%s\n' "${STACK_SERVICES[@]}"
            ;;
        *" build --no-cache "*)
            local service named=false
            for service in "$@"; do
                if [[ "$named" == true ]]; then
                    printf '%s\n' "${STACK_SERVICES[@]}" | grep -Fxq -- "$service" \
                        || { echo "no such service: $service" >&2; return 1; }
                fi
                [[ "$service" != --no-cache ]] || named=true
            done
            ;;
    esac
}

fail=0
pass() { echo "PASS: $1"; }
failed() { echo "FAIL: $1"; fail=1; }

# A default NVIDIA stack: ComfyUI selected, the optional built extensions not.
STACK_SERVICES=(dashboard dashboard-api llama-server litellm comfyui)
: > "$TMP/calls"
output=$(ENABLE_IMAGE_GENERATION=true _ods_cli_rebuild_images -f docker-compose.base.yml 2>&1)
if [[ "$(tail -n 1 "$TMP/calls")" == "compose -f docker-compose.base.yml build --no-cache dashboard dashboard-api comfyui" ]]; then
    pass "only selected local-built services are rebuilt, ComfyUI included"
else failed "only selected local-built services are rebuilt, ComfyUI included: $(tail -n 1 "$TMP/calls")"; fi
if [[ "$output" != *"failed to rebuild"* ]]; then pass "the rebuild does not fail on a disabled extension"
else failed "the rebuild does not fail on a disabled extension: $output"; fi

# Every optional built extension selected.
STACK_SERVICES=(dashboard dashboard-api ape token-spy privacy-shield llama-server)
: > "$TMP/calls"
_ods_cli_rebuild_images -f docker-compose.base.yml >/dev/null 2>&1
if [[ "$(tail -n 1 "$TMP/calls")" == "compose -f docker-compose.base.yml build --no-cache dashboard dashboard-api ape token-spy privacy-shield" ]]; then
    pass "selected optional extensions are rebuilt"
else failed "selected optional extensions are rebuilt: $(tail -n 1 "$TMP/calls")"; fi

# An unresolvable stack builds nothing and says so.
: > "$TMP/calls"
output=$(CONFIG_FAILS=1 _ods_cli_rebuild_images -f docker-compose.base.yml 2>&1)
if ! grep -q " build " "$TMP/calls"; then pass "an unresolvable stack runs no build"
else failed "an unresolvable stack runs no build"; fi
if [[ "$output" == *"no images were rebuilt"* ]]; then pass "an unresolvable stack is reported"
else failed "an unresolvable stack is reported: $output"; fi

exit "$fail"
