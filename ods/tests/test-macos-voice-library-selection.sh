#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
installer="$root/installers/macos/install-macos.sh"
scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
INSTALL_DIR="$scratch/install"
mkdir -p "$INSTALL_DIR/extensions/services/whisper" \
    "$INSTALL_DIR/extensions/services/tts"

ai_err() { printf '%s\n' "$*" >&2; }
ai_ok() { :; }
log() { :; }
for function_name in _macos_retained_builtin_state _macos_resolve_voice_selection \
    _macos_finalize_voice_selection \
    _macos_set_builtin_compose_state _macos_sync_builtin_compose_states; do
    eval "$(sed -n "/^${function_name}() {/,/^}/p" "$installer")"
done

ENABLE_VOICE=false ENABLE_WHISPER=false ENABLE_TTS=false
VOICE_EXPLICIT=false ALL_FEATURES=false
WHISPER_RETAINED="" TTS_RETAINED=""
_macos_resolve_voice_selection
[[ "$ENABLE_WHISPER" == false && "$ENABLE_TTS" == false ]]

printf 'ODS_MODE=local\n' >"$INSTALL_DIR/.env"
printf 'services: {}\n' >"$INSTALL_DIR/extensions/services/whisper/compose.yaml"
printf 'services: {}\n' >"$INSTALL_DIR/extensions/services/tts/compose.yaml.disabled"
mkdir -p "$INSTALL_DIR/data/tts"
printf 'keep\n' >"$INSTALL_DIR/data/tts/retained.txt"
_macos_resolve_voice_selection
[[ "$ENABLE_WHISPER" == true && "$ENABLE_TTS" == false && "$ENABLE_VOICE" == true ]]
ENABLE_VOICE=true  # Existing-install menu default displays Full.
_macos_finalize_voice_selection ""
[[ "$ENABLE_WHISPER" == true && "$ENABLE_TTS" == false ]]

# Source refresh restores the active fragment; the retained disabled choice wins.
printf 'services: {}\n' >"$INSTALL_DIR/extensions/services/tts/compose.yaml"
ENABLE_LITELLM=false ENABLE_SEARXNG=false ENABLE_RECOMMENDED=false
ENABLE_WORKFLOWS=false ENABLE_RAG=false ENABLE_HERMES=false
ENABLE_HERMES_PROXY=false ENABLE_OPENCLAW=false ENABLE_APE=false
ENABLE_PERPLEXICA=false ENABLE_PRIVACY_SHIELD=false ENABLE_ODS_PROXY=false
ENABLE_TAILSCALE=false ENABLE_LANGFUSE=false ENABLE_BRAVE_SEARCH=false
_macos_sync_builtin_compose_states
[[ -f "$INSTALL_DIR/extensions/services/whisper/compose.yaml" \
    && ! -e "$INSTALL_DIR/extensions/services/tts/compose.yaml" \
    && -f "$INSTALL_DIR/extensions/services/tts/compose.yaml.disabled" ]]
[[ "$(cat "$INSTALL_DIR/data/tts/retained.txt")" == keep ]]

mv "$INSTALL_DIR/extensions/services/whisper/compose.yaml" \
    "$INSTALL_DIR/extensions/services/whisper/compose.yaml.disabled"
mv "$INSTALL_DIR/extensions/services/tts/compose.yaml.disabled" \
    "$INSTALL_DIR/extensions/services/tts/compose.yaml"
_macos_resolve_voice_selection
[[ "$ENABLE_WHISPER" == false && "$ENABLE_TTS" == true && "$ENABLE_VOICE" == true ]]
ENABLE_VOICE=false
_macos_finalize_voice_selection "2"
[[ "$ENABLE_WHISPER" == false && "$ENABLE_TTS" == false ]]

VOICE_EXPLICIT=true ENABLE_VOICE=true
_macos_resolve_voice_selection
[[ "$ENABLE_WHISPER" == true && "$ENABLE_TTS" == true ]]
VOICE_EXPLICIT_VALUE=false ENABLE_VOICE=false
_macos_resolve_voice_selection
[[ "$ENABLE_WHISPER" == false && "$ENABLE_TTS" == false ]]

VOICE_EXPLICIT=false ALL_FEATURES=true ENABLE_VOICE=true
_macos_resolve_voice_selection
_macos_finalize_voice_selection ""
[[ "$ENABLE_WHISPER" == true && "$ENABLE_TTS" == true ]]

echo 'PASS: Mac Whisper/Kokoro Library selections survive source refresh and explicit choices'
