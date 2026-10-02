#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
installer="$root/installers/macos/install-macos.sh"
scratch="$(mktemp -d /tmp/ods-macos-voice.XXXXXXXX)"
[[ "$scratch" == /tmp/ods-macos-voice.* && ! -L "$scratch" ]]
trap '[[ "$scratch" == /tmp/ods-macos-voice.* && ! -L "$scratch" ]] && rm -rf -- "$scratch"' EXIT
INSTALL_DIR="$scratch/install"
mkdir -p "$INSTALL_DIR/extensions/services/whisper" \
    "$INSTALL_DIR/extensions/services/tts" \
    "$INSTALL_DIR/data/whisper" "$INSTALL_DIR/data/tts"
printf 'keep-stt-cache\n' > "$INSTALL_DIR/data/whisper/cache"
printf 'keep-tts-data\n' > "$INSTALL_DIR/data/tts/owner"

ai_err() { printf '%s\n' "$*" >&2; }
ai_ok() { :; }
log() { :; }
for function_name in _macos_retained_builtin_state _macos_resolve_voice_selection \
    _macos_gateway_library_selected _macos_set_builtin_compose_state \
    _macos_sync_builtin_compose_states; do
    eval "$(sed -n "/^${function_name}() {/,/^}/p" "$installer")"
done

VOICE_ENABLE_EXPLICIT=false VOICE_DISABLE_EXPLICIT=false ALL_FEATURES=false
GATEWAY_ONLY=false _saved_gateway_only=""
ENABLE_VOICE=false ENABLE_WHISPER=false ENABLE_TTS=false
WHISPER_RETAINED="" TTS_RETAINED=""
_macos_resolve_voice_selection
[[ "$ENABLE_WHISPER" == false && "$ENABLE_TTS" == false ]]

printf 'ODS_MODE=local\n' > "$INSTALL_DIR/.env"
ENABLE_LITELLM=false ENABLE_SEARXNG=false ENABLE_RECOMMENDED=false
ENABLE_WORKFLOWS=false ENABLE_RAG=false ENABLE_HERMES=false
ENABLE_HERMES_PROXY=false ENABLE_OPENCLAW=false ENABLE_APE=false
ENABLE_PERPLEXICA=false ENABLE_PRIVACY_SHIELD=false ENABLE_ODS_PROXY=false
ENABLE_TAILSCALE=false ENABLE_LANGFUSE=false ENABLE_BRAVE_SEARCH=false

for state in none whisper tts both; do
    for service in whisper tts; do
        rm -f "$INSTALL_DIR/extensions/services/$service/compose.yaml" \
            "$INSTALL_DIR/extensions/services/$service/compose.yaml.disabled"
        if [[ "$state" == "$service" || "$state" == both ]]; then
            printf 'services: {}\n' > "$INSTALL_DIR/extensions/services/$service/compose.yaml"
        else
            printf 'services: {}\n' > "$INSTALL_DIR/extensions/services/$service/compose.yaml.disabled"
        fi
    done
    ENABLE_VOICE=false ENABLE_WHISPER=false ENABLE_TTS=false
    _macos_resolve_voice_selection
    expected_whisper=false expected_tts=false
    [[ "$state" == whisper || "$state" == both ]] && expected_whisper=true
    [[ "$state" == tts || "$state" == both ]] && expected_tts=true
    [[ "$ENABLE_WHISPER" == "$expected_whisper" && "$ENABLE_TTS" == "$expected_tts" ]]
    if [[ "$state" == none ]]; then [[ "$ENABLE_VOICE" == false ]];
    else [[ "$ENABLE_VOICE" == true ]]; fi

    # A source refresh may copy an active fragment over a retained disabled
    # marker. The resolved per-service choice must win at synchronization.
    printf 'services: {}\n' > "$INSTALL_DIR/extensions/services/whisper/compose.yaml"
    printf 'services: {}\n' > "$INSTALL_DIR/extensions/services/tts/compose.yaml"
    _macos_sync_builtin_compose_states
    for service in whisper tts; do
        case "$service" in
            whisper) expected="$expected_whisper" ;;
            tts) expected="$expected_tts" ;;
        esac
        if [[ "$expected" == true ]]; then
            [[ -f "$INSTALL_DIR/extensions/services/$service/compose.yaml" \
                && ! -e "$INSTALL_DIR/extensions/services/$service/compose.yaml.disabled" ]]
        else
            [[ ! -e "$INSTALL_DIR/extensions/services/$service/compose.yaml" \
                && -f "$INSTALL_DIR/extensions/services/$service/compose.yaml.disabled" ]]
        fi
    done
done
[[ "$(cat "$INSTALL_DIR/data/whisper/cache")" == keep-stt-cache ]]
[[ "$(cat "$INSTALL_DIR/data/tts/owner")" == keep-tts-data ]]
echo 'PASS: Mac retains all four independent voice selections through source refresh'

VOICE_ENABLE_EXPLICIT=true
_macos_resolve_voice_selection
[[ "$ENABLE_WHISPER" == true && "$ENABLE_TTS" == true ]]
VOICE_ENABLE_EXPLICIT=false VOICE_DISABLE_EXPLICIT=true
_macos_resolve_voice_selection
[[ "$ENABLE_WHISPER" == false && "$ENABLE_TTS" == false ]]
VOICE_DISABLE_EXPLICIT=false ALL_FEATURES=true
_macos_resolve_voice_selection
[[ "$ENABLE_WHISPER" == true && "$ENABLE_TTS" == true ]]
ALL_FEATURES=false
echo 'PASS: explicit Voice, No Voice and All override retained selections'

printf 'services: {}\n' > "$INSTALL_DIR/extensions/services/tts/compose.yaml"
printf 'services: {}\n' > "$INSTALL_DIR/extensions/services/tts/compose.yaml.disabled"
if _macos_resolve_voice_selection 2>/dev/null; then
    echo 'Ambiguous TTS Compose markers were accepted' >&2
    exit 1
fi
VOICE_ENABLE_EXPLICIT=true
if _macos_resolve_voice_selection 2>/dev/null; then
    echo 'Explicit Voice accepted ambiguous TTS Compose markers' >&2
    exit 1
fi
VOICE_ENABLE_EXPLICIT=false
rm -f "$INSTALL_DIR/extensions/services/tts/compose.yaml"
ln -s "$INSTALL_DIR/data/tts/owner" "$INSTALL_DIR/extensions/services/tts/compose.yaml"
if _macos_resolve_voice_selection 2>/dev/null; then
    echo 'Symlinked TTS Compose marker was accepted' >&2
    exit 1
fi
VOICE_DISABLE_EXPLICIT=true
if _macos_resolve_voice_selection 2>/dev/null; then
    echo 'Explicit No Voice accepted symlinked TTS Compose marker' >&2
    exit 1
fi
[[ "$(cat "$INSTALL_DIR/data/tts/owner")" == keep-tts-data ]]
echo 'PASS: ambiguous and symlinked voice markers fail before mutation'
