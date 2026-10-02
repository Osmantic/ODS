#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
installer="$root/installers/macos/install-macos.sh"
scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT

# Execute the installer's actual interactive choice block with a controlled
# answer. No installer phase, Docker operation, or host service runs here.
choice_block="$(sed -n '/^if ! \$NON_INTERACTIVE && ! \$ALL_FEATURES && ! \$DRY_RUN; then$/,/^_macos_restore_retained_optional_features || exit 1$/p' "$installer")"
[[ -n "$choice_block" ]] || { echo 'FAIL: Mac feature menu not found' >&2; exit 1; }
choice_block="$(printf '%s\n' "$choice_block" | sed 's#< /dev/tty#< "$choice_file"#g')"

ai() { :; }
ai_ok() { :; }
ai_err() { printf '%s\n' "$*" >&2; }
chapter() { :; }
log() { :; }
read_env_value() {
    [[ "$2" == PIXEL_WEB_SEARCH_PROVIDER ]] && printf 'parallel-free\n' || :
}
BGRN='' WHT='' NC=''
NON_INTERACTIVE=false ALL_FEATURES=false DRY_RUN=false
GATEWAY_ONLY=false _saved_gateway_only=''
ENABLE_PIXEL=true CLOUD_MODE=false
OPENCODE_DISABLE_EXPLICIT=false OPENCODE_ENABLE_EXPLICIT=false
RECOMMENDED_EXPLICIT=false NO_LANGFUSE_EXPLICIT=false
OPENCLAW_EXPLICIT=false

eval "$(sed -n '/^_macos_retained_builtin_state() {/,/^}/p' "$installer")"
eval "$(sed -n '/^_macos_retained_optional_state() {/,/^}/p' "$installer")"
eval "$(sed -n '/^_macos_restore_retained_optional_features() {/,/^}/p' "$installer")"
eval "$(sed -n '/^_macos_set_builtin_compose_state() {/,/^}/p' "$installer")"
eval "$(sed -n '/^_macos_sync_builtin_compose_states() {/,/^}/p' "$installer")"
eval "$(sed -n '/^_macos_resolve_support_services() {/,/^}/p' "$installer")"

reset_features() {
    ENABLE_VOICE=false ENABLE_WHISPER=false ENABLE_TTS=false
    ENABLE_WORKFLOWS=false ENABLE_RAG=false ENABLE_RECOMMENDED=true
    ENABLE_HERMES=false ENABLE_HERMES_PROXY=false ENABLE_OPENCLAW=false
    ENABLE_OPENCODE=false OPENCODE_DISABLE_SELECTED=false
    ENABLE_APE=true ENABLE_PERPLEXICA=false ENABLE_PRIVACY_SHIELD=false
    ENABLE_LANGFUSE=false ENABLE_OPEN_WEBUI=false
    ENABLE_LITELLM=true ENABLE_SEARXNG=false ENABLE_ODS_PROXY=false
    ENABLE_TAILSCALE=false ENABLE_BRAVE_SEARCH=false
    ENABLE_PIXEL=true CLOUD_MODE=false
    HERMES_EXPLICIT=false HERMES_RETAINED='' HERMES_PROXY_RETAINED=''
    VOICE_ENABLE_EXPLICIT=false VOICE_DISABLE_EXPLICIT=false
    WHISPER_RETAINED='' TTS_RETAINED=''
    WEBUI_RETAINED='' WEBUI_ENABLE_EXPLICIT=false WEBUI_DISABLE_EXPLICIT=false
    unset _MACOS_RETAINED_SEARXNG || true
    unset feature_choice || true
}

set_marker() {
    local service="$1" selected="$2" path
    path="$INSTALL_DIR/extensions/services/$service"
    mkdir -p "$path"
    rm -f "$path/compose.yaml" "$path/compose.yaml.disabled"
    if [[ "$selected" == true ]]; then
        printf 'services: {}\n' > "$path/compose.yaml"
    else
        printf 'services: {}\n' > "$path/compose.yaml.disabled"
    fi
}

assert_marker() {
    local service="$1" selected="$2" path
    path="$INSTALL_DIR/extensions/services/$service"
    if [[ "$selected" == true ]]; then
        [[ -f "$path/compose.yaml" && ! -e "$path/compose.yaml.disabled" ]]
    else
        [[ -f "$path/compose.yaml.disabled" && ! -e "$path/compose.yaml" ]]
    fi || { echo "FAIL: retained $service marker changed (expected $selected)" >&2; exit 1; }
}

run_choice() {
    local answer="$1"
    choice_file="$scratch/answer"
    printf '%s\n' "$answer" > "$choice_file"
    eval "$choice_block"
    _macos_resolve_support_services
    _macos_sync_builtin_compose_states
}

services=(n8n qdrant embeddings token-spy searxng perplexica privacy-shield langfuse)
INSTALL_DIR="$scratch/core"
mkdir -p "$INSTALL_DIR"
printf 'ODS_MODE=local\n' > "$INSTALL_DIR/.env"
for service in "${services[@]}"; do set_marker "$service" false; done
reset_features
run_choice ''
for service in "${services[@]}"; do assert_marker "$service" false; done

# Unattended reruns also use the installed selection, rather than the shell's
# defaults (which include Token Spy and APE before fresh-install resolution).
reset_features
NON_INTERACTIVE=true
run_choice ''
for service in "${services[@]}"; do assert_marker "$service" false; done
NON_INTERACTIVE=false

# A retained custom install must keep each actual optional choice, not be
# flattened to either Full Stack or Core Only.
INSTALL_DIR="$scratch/custom"
mkdir -p "$INSTALL_DIR"
printf 'ODS_MODE=local\n' > "$INSTALL_DIR/.env"
for service in "${services[@]}"; do set_marker "$service" false; done
set_marker n8n true
set_marker langfuse true
set_marker searxng true
reset_features
run_choice ''
assert_marker n8n true
assert_marker langfuse true
assert_marker searxng true
for service in qdrant embeddings token-spy perplexica privacy-shield; do
    assert_marker "$service" false
done

reset_features
NON_INTERACTIVE=true
run_choice ''
assert_marker n8n true
assert_marker langfuse true
assert_marker searxng true
for service in qdrant embeddings token-spy perplexica privacy-shield; do
    assert_marker "$service" false
done
NON_INTERACTIVE=false

# Enter on a fresh install still means Core; selecting 1 explicitly still
# enables the Full Stack bundle on a retained installation.
INSTALL_DIR="$scratch/fresh"
mkdir -p "$INSTALL_DIR"
for service in "${services[@]}"; do set_marker "$service" false; done
reset_features
run_choice ''
for service in "${services[@]}"; do assert_marker "$service" false; done

INSTALL_DIR="$scratch/explicit"
mkdir -p "$INSTALL_DIR"
printf 'ODS_MODE=local\n' > "$INSTALL_DIR/.env"
for service in "${services[@]}"; do set_marker "$service" false; done
reset_features
run_choice 1
for service in "${services[@]}"; do assert_marker "$service" true; done

# Explicit non-interactive flags can enable services over a disabled retained
# marker; the retained default must not silently override these choices.
INSTALL_DIR="$scratch/cli"
mkdir -p "$INSTALL_DIR"
printf 'ODS_MODE=local\n' > "$INSTALL_DIR/.env"
for service in "${services[@]}"; do set_marker "$service" false; done
reset_features
NON_INTERACTIVE=true
ENABLE_WORKFLOWS=true ENABLE_RAG=true ENABLE_RECOMMENDED=true
RECOMMENDED_EXPLICIT=true ENABLE_LANGFUSE=true
run_choice ''
for service in n8n qdrant embeddings token-spy searxng langfuse; do
    assert_marker "$service" true
done
NON_INTERACTIVE=false RECOMMENDED_EXPLICIT=false

# A mistyped answer must fail before any optional recipe is changed.
INSTALL_DIR="$scratch/invalid"
mkdir -p "$INSTALL_DIR"
printf 'ODS_MODE=local\n' > "$INSTALL_DIR/.env"
for service in "${services[@]}"; do set_marker "$service" false; done
reset_features
if (run_choice x >/dev/null 2>&1); then
    echo 'FAIL: invalid menu answer was accepted' >&2
    exit 1
fi
for service in "${services[@]}"; do assert_marker "$service" false; done

# A legacy Compose selection without a marker remains selected on rerun.
INSTALL_DIR="$scratch/legacy"
mkdir -p "$INSTALL_DIR/extensions/services/n8n"
printf 'ODS_MODE=local\n' > "$INSTALL_DIR/.env"
printf '%s\n' '-f docker-compose.base.yml -f extensions/services/n8n/compose.yaml' \
    > "$INSTALL_DIR/.compose-flags"
reset_features
run_choice ''
[[ "$ENABLE_WORKFLOWS" == true ]] \
    || { echo 'FAIL: legacy selected workflow was lost' >&2; exit 1; }

# Older installs could leave every shipped recipe active on disk even when
# their saved Compose plan selected only one. Those source files are not a
# reason to enable unselected services during the next rerun.
INSTALL_DIR="$scratch/legacy-unselected"
mkdir -p "$INSTALL_DIR"
printf 'ODS_MODE=local\n' > "$INSTALL_DIR/.env"
set_marker n8n true
set_marker langfuse true
printf '%s\n' '-f docker-compose.base.yml -f extensions/services/langfuse/compose.yaml' \
    > "$INSTALL_DIR/.compose-flags"
reset_features
run_choice ''
assert_marker n8n false
assert_marker langfuse true

INSTALL_DIR="$scratch/ambiguous"
mkdir -p "$INSTALL_DIR"
printf 'ODS_MODE=local\n' > "$INSTALL_DIR/.env"
set_marker n8n false
printf 'services: {}\n' > "$INSTALL_DIR/extensions/services/n8n/compose.yaml"
reset_features
if (run_choice '' >/dev/null 2>&1); then
    echo 'FAIL: ambiguous retained selection was accepted' >&2
    exit 1
fi
[[ -f "$INSTALL_DIR/extensions/services/n8n/compose.yaml"
    && -f "$INSTALL_DIR/extensions/services/n8n/compose.yaml.disabled" ]] \
    || { echo 'FAIL: ambiguous selection mutated before rejection' >&2; exit 1; }

echo 'PASS: Mac retained Enter preserves optional selections; explicit Full Stack remains opt-in'
