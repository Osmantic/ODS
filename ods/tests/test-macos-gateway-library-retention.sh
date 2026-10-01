#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture="$(mktemp -d)"
trap 'rm -rf -- "$fixture"' EXIT

installer="$root/installers/macos/install-macos.sh"
for function_name in _macos_capture_gateway_library_selections \
    _macos_gateway_library_selected _macos_effective_service_enabled \
    _macos_set_builtin_compose_state \
    _macos_sync_builtin_compose_states; do
    eval "$(sed -n "/^${function_name}() {/,/^}/p" "$installer")"
done
ai_ok() { :; }
log() { :; }

for setting in ENABLE_RECOMMENDED ENABLE_VOICE ENABLE_WORKFLOWS ENABLE_RAG \
    ENABLE_HERMES ENABLE_OPENCLAW ENABLE_APE ENABLE_PERPLEXICA \
    ENABLE_PRIVACY_SHIELD ENABLE_ODS_PROXY ENABLE_TAILSCALE ENABLE_LANGFUSE \
    ENABLE_BRAVE_SEARCH ENABLE_SEARXNG; do
    printf -v "$setting" '%s' false
done
# The extracted installer functions read these globals through eval.
# shellcheck disable=SC2034
ENABLE_LITELLM=true
# shellcheck disable=SC2034
GATEWAY_ONLY=true

# Fresh gateway installs start with shipped active files, but select only
# LiteLLM. The optional Library choices belong to the user after install.
INSTALL_DIR="$fixture/fresh"
_saved_gateway_only=""
for service_id in litellm searxng whisper tts hermes; do
    mkdir -p "$INSTALL_DIR/extensions/services/$service_id"
    printf 'shipped\n' > "$INSTALL_DIR/extensions/services/$service_id/compose.yaml"
done
_macos_capture_gateway_library_selections
[[ -z "$MACOS_GATEWAY_RETAINED_COMPOSE_IDS" ]]
if _macos_effective_service_enabled whisper "$ENABLE_VOICE"; then
    echo 'Fresh gateway unexpectedly selected Whisper' >&2
    exit 1
fi
_macos_sync_builtin_compose_states
[[ -f "$INSTALL_DIR/extensions/services/litellm/compose.yaml" ]]
for service_id in searxng whisper tts hermes; do
    [[ ! -e "$INSTALL_DIR/extensions/services/$service_id/compose.yaml" ]]
    [[ -f "$INSTALL_DIR/extensions/services/$service_id/compose.yaml.disabled" ]]
done

# On a retained gateway install, Library enabled Whisper and SearXNG while
# TTS stayed disabled. Source rsync reintroduces an active shipped TTS file;
# the installer must retain each independent Library choice.
INSTALL_DIR="$fixture/retained"
_saved_gateway_only=true
for service_id in litellm searxng whisper tts hermes; do
    mkdir -p "$INSTALL_DIR/extensions/services/$service_id"
done
printf 'library\n' > "$INSTALL_DIR/extensions/services/searxng/compose.yaml"
printf 'library\n' > "$INSTALL_DIR/extensions/services/whisper/compose.yaml"
printf 'library\n' > "$INSTALL_DIR/extensions/services/hermes/compose.yaml"
printf 'disabled\n' > "$INSTALL_DIR/extensions/services/tts/compose.yaml.disabled"
_macos_capture_gateway_library_selections
_macos_gateway_library_selected searxng
_macos_gateway_library_selected whisper
_macos_gateway_library_selected hermes
if _macos_gateway_library_selected tts; then
    echo 'Disabled TTS was captured as enabled' >&2
    exit 1
fi
_macos_effective_service_enabled whisper "$ENABLE_VOICE"
_macos_effective_service_enabled searxng "$ENABLE_SEARXNG"
_macos_effective_service_enabled hermes "$ENABLE_HERMES"
if _macos_effective_service_enabled tts "$ENABLE_VOICE"; then
    echo 'Disabled TTS inherited the Whisper feature group' >&2
    exit 1
fi
printf 'shipped\n' > "$INSTALL_DIR/extensions/services/litellm/compose.yaml"
printf 'shipped\n' > "$INSTALL_DIR/extensions/services/searxng/compose.yaml"
printf 'shipped\n' > "$INSTALL_DIR/extensions/services/whisper/compose.yaml"
printf 'shipped\n' > "$INSTALL_DIR/extensions/services/hermes/compose.yaml"
printf 'shipped\n' > "$INSTALL_DIR/extensions/services/tts/compose.yaml"
_macos_sync_builtin_compose_states
for service_id in litellm searxng whisper hermes; do
    [[ -f "$INSTALL_DIR/extensions/services/$service_id/compose.yaml" ]]
done
[[ ! -e "$INSTALL_DIR/extensions/services/tts/compose.yaml" ]]
[[ -f "$INSTALL_DIR/extensions/services/tts/compose.yaml.disabled" ]]

# A second rerun sees the same Library selection after the installer has
# normalized the shipped files. No paired service is introduced.
_macos_capture_gateway_library_selections
_macos_gateway_library_selected whisper
if _macos_gateway_library_selected tts; then
    echo 'Second rerun unexpectedly selected TTS' >&2
    exit 1
fi

echo 'PASS: Mac gateway fresh and retained Library selections stay independent'
