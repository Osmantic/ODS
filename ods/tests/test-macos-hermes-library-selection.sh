#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
installer="$root/installers/macos/install-macos.sh"
scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
INSTALL_DIR="$scratch/install"
mkdir -p "$INSTALL_DIR/extensions/services/hermes" \
    "$INSTALL_DIR/extensions/services/hermes-proxy"

ai_err() { printf '%s\n' "$*" >&2; }
ai_ok() { :; }
log() { :; }
for function_name in _macos_retained_builtin_state _macos_resolve_hermes_selection \
    _macos_validate_hermes_selection _macos_apply_custom_hermes_answer \
    _macos_set_builtin_compose_state \
    _macos_sync_builtin_compose_states; do
    eval "$(sed -n "/^${function_name}() {/,/^}/p" "$installer")"
done

HERMES_EXPLICIT=false ALL_FEATURES=false
ENABLE_HERMES=true ENABLE_HERMES_PROXY=true
HERMES_RETAINED="" HERMES_PROXY_RETAINED=""
_macos_resolve_hermes_selection
[[ "$ENABLE_HERMES" == false && "$ENABLE_HERMES_PROXY" == false ]]

printf 'ODS_MODE=local\n' >"$INSTALL_DIR/.env"
printf 'services: {}\n' >"$INSTALL_DIR/extensions/services/hermes/compose.yaml"
printf 'services: {}\n' >"$INSTALL_DIR/extensions/services/hermes-proxy/compose.yaml.disabled"
_macos_resolve_hermes_selection
[[ "$ENABLE_HERMES" == true && "$ENABLE_HERMES_PROXY" == false ]]
_macos_validate_hermes_selection

# Custom keeps an independently disabled proxy when Hermes remains selected.
_macos_apply_custom_hermes_answer ""
[[ "$ENABLE_HERMES" == true && "$ENABLE_HERMES_PROXY" == false ]]
_macos_apply_custom_hermes_answer "n"
[[ "$ENABLE_HERMES" == false && "$ENABLE_HERMES_PROXY" == false ]]
_macos_apply_custom_hermes_answer "y"
[[ "$ENABLE_HERMES" == true && "$ENABLE_HERMES_PROXY" == true ]]
ENABLE_HERMES_PROXY=false

# A source refresh can copy active fragments over installed disabled markers.
# The retained per-service choices must still win when the installer syncs.
printf 'services: {}\n' >"$INSTALL_DIR/extensions/services/hermes-proxy/compose.yaml"
ENABLE_LITELLM=false ENABLE_SEARXNG=false ENABLE_RECOMMENDED=false
ENABLE_VOICE=false ENABLE_WHISPER=false ENABLE_TTS=false ENABLE_WORKFLOWS=false ENABLE_RAG=false
ENABLE_OPENCLAW=false ENABLE_APE=false ENABLE_PERPLEXICA=false
ENABLE_PRIVACY_SHIELD=false ENABLE_ODS_PROXY=false ENABLE_TAILSCALE=false
ENABLE_LANGFUSE=false ENABLE_BRAVE_SEARCH=false
_macos_sync_builtin_compose_states
[[ -f "$INSTALL_DIR/extensions/services/hermes/compose.yaml" \
    && ! -e "$INSTALL_DIR/extensions/services/hermes/compose.yaml.disabled" \
    && ! -e "$INSTALL_DIR/extensions/services/hermes-proxy/compose.yaml" \
    && -f "$INSTALL_DIR/extensions/services/hermes-proxy/compose.yaml.disabled" ]]

# Explicit CLI selection remains authoritative over installed markers.
HERMES_EXPLICIT=true ENABLE_HERMES=false ENABLE_HERMES_PROXY=false
_macos_resolve_hermes_selection
[[ "$ENABLE_HERMES" == false && "$ENABLE_HERMES_PROXY" == false ]]
_macos_sync_builtin_compose_states
[[ -f "$INSTALL_DIR/extensions/services/hermes/compose.yaml.disabled" \
    && -f "$INSTALL_DIR/extensions/services/hermes-proxy/compose.yaml.disabled" ]]

HERMES_EXPLICIT=false
printf 'services: {}\n' >"$INSTALL_DIR/extensions/services/hermes-proxy/compose.yaml"
_macos_resolve_hermes_selection
[[ "$ENABLE_HERMES" == false && "$ENABLE_HERMES_PROXY" == true ]]
if _macos_validate_hermes_selection 2>/dev/null; then
    echo 'invalid proxy-only selection was accepted' >&2
    exit 1
fi
[[ -f "$INSTALL_DIR/extensions/services/hermes/compose.yaml.disabled" \
    && -f "$INSTALL_DIR/extensions/services/hermes-proxy/compose.yaml" ]]

echo 'PASS: Mac Hermes Library selections survive source refresh and explicit choices'
