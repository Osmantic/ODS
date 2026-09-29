#!/usr/bin/env bash
# Fresh CLI installs should stay small; reruns recover installed selections.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/installers/lib/installed-feature-state.sh"
source "$ROOT/installers/lib/external-services.sh"
defaults="$(sed -n '/^DRY_RUN=false$/,/^INTERACTIVE=true$/p' "$ROOT/install-core.sh")"
[[ -n "$defaults" ]] || { echo 'FAIL: installer defaults block missing' >&2; exit 1; }

check_defaults() (
    local existing="$1" expected="$2" dir
    dir="$(mktemp -d)"
    trap 'rm -f -- "$dir/.env"; rmdir -- "$dir"' EXIT
    INSTALL_DIR="$dir"
    [[ "$existing" == true ]] && : >"$dir/.env"
    eval "$defaults"
    [[ "$ODS_EXISTING_INSTALL" == "$existing" ]] || exit 1
    for flag in ENABLE_VOICE ENABLE_WORKFLOWS ENABLE_RAG ENABLE_RECOMMENDED \
                ENABLE_HERMES ENABLE_COMFYUI ENABLE_APE ENABLE_PERPLEXICA \
                ENABLE_PRIVACY_SHIELD; do
        [[ "${!flag}" == "$expected" ]] || {
            echo "FAIL: $flag=${!flag} on existing=$existing" >&2
            exit 1
        }
    done
    [[ "$ENABLE_OPENCODE" == false && "$ENABLE_OPENCLAW" == false ]]
)

check_defaults false false
check_defaults true true
check_gateway_default() (
    local dir
    dir="$(mktemp -d)"
    trap 'rm -f -- "$dir/.env"; rmdir -- "$dir"' EXIT
    INSTALL_DIR="$dir"
    printf 'ODS_GATEWAY_ONLY=true\nENABLE_OPEN_WEBUI=false\n' > "$dir/.env"
    eval "$defaults"
    [[ "$ODS_GATEWAY_ONLY" == true && "$ENABLE_OPEN_WEBUI" == false ]] || {
        echo 'FAIL: retained API-only gateway selection was lost' >&2
        exit 1
    }
)
check_gateway_default
check_portal_only_default() (
    local dir
    dir="$(mktemp -d)"
    trap 'rm -f -- "$dir/.env"; rmdir -- "$dir"' EXIT
    INSTALL_DIR="$dir"
    printf 'ODS_GATEWAY_ONLY=false\nENABLE_OPEN_WEBUI=false\n' > "$dir/.env"
    eval "$defaults"
    [[ "$ODS_GATEWAY_ONLY" == false && "$ENABLE_OPEN_WEBUI" == false ]] || {
        echo 'FAIL: retained ordinary Portal-only selection was lost' >&2
        exit 1
    }
)
check_portal_only_default
echo 'PASS: Linux fresh and markerless legacy installer defaults are distinct'
