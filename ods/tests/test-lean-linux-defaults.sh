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
    [[ "$WEBUI_EXPLICIT" == false ]] || exit 1
    [[ "$ENABLE_OPEN_WEBUI" == true ]] || {
        echo "FAIL: initial WebUI fallback was lost on existing=$existing" >&2
        exit 1
    }
    [[ "$ENABLE_ODS_PROXY" == false ]] || {
        echo "FAIL: unexpected LAN proxy default would require WebUI" >&2
        exit 1
    }
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

# A fresh source layout carries optional compose files for every service but
# has no INSTALL_DIR/.env yet. Those files must NOT enable optional features:
# only an installed tree (.env present) can express a previous selection.
check_fresh_source_layout() (
    local dir
    dir="$(mktemp -d)"
    trap 'rm -rf -- "$dir"' EXIT
    INSTALL_DIR="$dir"
    for service in whisper tts n8n qdrant embeddings token-spy hermes hermes-proxy \
                   comfyui ape perplexica privacy-shield langfuse ods-proxy tailscale brave-search; do
        mkdir -p "$dir/extensions/services/$service"
        : >"$dir/extensions/services/$service/compose.yaml"
    done
    eval "$defaults"
    [[ "$ODS_EXISTING_INSTALL" == false ]] || {
        echo 'FAIL: fresh source layout treated as existing install' >&2
        exit 1
    }
    [[ "$ENABLE_OPEN_WEBUI" == true ]] || {
        echo 'FAIL: fresh source layout lost initial WebUI fallback' >&2
        exit 1
    }
    for flag in ENABLE_VOICE ENABLE_WORKFLOWS ENABLE_RAG ENABLE_RECOMMENDED \
                ENABLE_HERMES ENABLE_COMFYUI ENABLE_APE ENABLE_PERPLEXICA \
                ENABLE_PRIVACY_SHIELD ENABLE_LANGFUSE ENABLE_ODS_PROXY \
                ENABLE_TAILSCALE ENABLE_BRAVE_SEARCH; do
        [[ "${!flag}" == false ]] || {
            echo "FAIL: fresh source $flag=${!flag}; expected false" >&2
            exit 1
        }
    done
    [[ "$ENABLE_OPENCODE" == false && "$ENABLE_OPENCLAW" == false ]]
)
check_fresh_source_layout
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
check_portal_chat_choice() {
    local label="$1" expected="$2" actual=false
    shift 2
    ods_should_default_portal_chat "$@" && actual=true
    [[ "$actual" == "$expected" ]] || {
        echo "FAIL: $label selected Portal=$actual; expected $expected" >&2
        exit 1
    }
}
check_portal_chat_choice 'fresh qualified Pixel' true false false false true false false false
check_portal_chat_choice 'unqualified Pixel fallback' false false false false false false false false
check_portal_chat_choice 'explicit WebUI' false false true false true false false false
check_portal_chat_choice 'legacy upgrade' false true false false true false false false
check_portal_chat_choice 'gateway-only' false false false true true false false false
check_portal_chat_choice 'voice needs WebUI' false false false false true true false false
check_portal_chat_choice 'RAG needs WebUI' false false false false true false true false
check_portal_chat_choice 'LAN proxy needs WebUI' false false false false true false false true

check_all_then_core_menu() (
    local dir all_clause core_clause portal_selected=false
    dir="$(mktemp -d)"
    trap 'rmdir -- "$dir"' EXIT
    INSTALL_DIR="$dir"
    eval "$defaults"
    all_clause="$(awk '/^[[:space:]]*--all\)/ {
        sub(/^[[:space:]]*--all\)[[:space:]]*/, "")
        sub(/[[:space:]]*shift[[:space:]]*;;[[:space:]]*$/, "")
        print; exit
    }' "$ROOT/install-core.sh")"
    [[ -n "$all_clause" ]] || { echo 'FAIL: --all case missing' >&2; exit 1; }
    eval "$all_clause"
    [[ "$ENABLE_VOICE" == true && "$ENABLE_ODS_PROXY" == true &&
       "$ENABLE_OPEN_WEBUI" == true ]] || {
        echo 'FAIL: --all stopped selecting full stack' >&2; exit 1;
    }

    core_clause="$(awk '/^        2\)$/ { in_core=1; next }
        in_core && /^            ;;$/ { exit }
        in_core { print }' "$ROOT/installers/lib/ui.sh")"
    [[ -n "$core_clause" ]] || { echo 'FAIL: Core menu case missing' >&2; exit 1; }
    signal() { :; }
    log() { :; }
    eval "$core_clause"
    [[ "$ENABLE_VOICE" == false && "$ENABLE_ODS_PROXY" == false &&
       "$WEBUI_EXPLICIT" == false ]] || {
        echo 'FAIL: interactive Core choice retained --all voice, proxy, or WebUI pin' >&2; exit 1;
    }
    ods_should_default_portal_chat false "$WEBUI_EXPLICIT" false true \
        "$ENABLE_VOICE" "$ENABLE_RAG" "$ENABLE_ODS_PROXY" && portal_selected=true
    [[ "$portal_selected" == true ]] || {
        echo 'FAIL: --all then Core did not select Portal chat' >&2; exit 1;
    }

    eval "$defaults"
    eval "$all_clause"
    WEBUI_EXPLICIT=true ENABLE_OPEN_WEBUI=true
    eval "$core_clause"
    [[ "$WEBUI_EXPLICIT" == true && "$ENABLE_OPEN_WEBUI" == true ]] || {
        echo 'FAIL: Core overrode an explicit WebUI switch' >&2; exit 1;
    }
)
check_all_then_core_menu

echo 'PASS: Linux fresh and legacy defaults preserve a safe chat UI'
