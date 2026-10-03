#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
installer="$root/installers/macos/install-macos.sh"
SOURCE_ROOT="$root"
scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
INSTALL_DIR="$scratch/install"
mkdir -p "$INSTALL_DIR/data/pixel-native/preparation"

read_env_value() {
    local line
    line="$(grep -m1 "^${2}=" "$1" 2>/dev/null || true)"
    printf '%s\n' "${line#*=}"
}
ai_err() { printf '%s\n' "$*" >&2; }
ai_ok() { :; }
log() { :; }
eval "$(sed -n '/^_macos_resolve_support_services() {/,/^}/p' "$installer")"
eval "$(sed -n '/^_macos_apply_fresh_feature_defaults() {/,/^}/p' "$installer")"
eval "$(sed -n '/^_macos_set_builtin_compose_state() {/,/^}/p' "$installer")"
eval "$(sed -n '/^_macos_sync_builtin_compose_states() {/,/^}/p' "$installer")"

NON_INTERACTIVE=true DRY_RUN=false ALL_FEATURES=false
RECOMMENDED_EXPLICIT=false HERMES_EXPLICIT=false
ENABLE_RECOMMENDED=true ENABLE_HERMES=true
_macos_apply_fresh_feature_defaults
[[ "$ENABLE_RECOMMENDED" == false && "$ENABLE_HERMES" == false ]] \
    || { echo 'FAIL: fresh unattended install did not select Core' >&2; exit 1; }

ENABLE_RECOMMENDED=true ENABLE_HERMES=true ALL_FEATURES=true
_macos_apply_fresh_feature_defaults
[[ "$ENABLE_RECOMMENDED" == true && "$ENABLE_HERMES" == true ]] \
    || { echo 'FAIL: --all lost the Full selection' >&2; exit 1; }

ALL_FEATURES=false RECOMMENDED_EXPLICIT=true HERMES_EXPLICIT=true
_macos_apply_fresh_feature_defaults
[[ "$ENABLE_RECOMMENDED" == true && "$ENABLE_HERMES" == true ]] \
    || { echo 'FAIL: explicit support selections were overwritten' >&2; exit 1; }

RECOMMENDED_EXPLICIT=false HERMES_EXPLICIT=false
printf 'ODS_MODE=local\n' > "$INSTALL_DIR/.env"
_macos_apply_fresh_feature_defaults
[[ "$ENABLE_RECOMMENDED" == true && "$ENABLE_HERMES" == true ]] \
    || { echo 'FAIL: existing install selection changed' >&2; exit 1; }

rm "$INSTALL_DIR/.env"
NON_INTERACTIVE=false DRY_RUN=true
_macos_apply_fresh_feature_defaults
[[ "$ENABLE_RECOMMENDED" == false && "$ENABLE_HERMES" == false ]] \
    || { echo 'FAIL: fresh dry-run did not report Core' >&2; exit 1; }

reset_features() {
    ENABLE_RECOMMENDED=false ENABLE_PIXEL=true CLOUD_MODE=false
    ENABLE_PERPLEXICA=false ENABLE_HERMES=false ENABLE_OPENCLAW=false
    ENABLE_LITELLM=false ENABLE_SEARXNG=false ENABLE_WEB_SEARCH=false
    ENABLE_VOICE=false ENABLE_WORKFLOWS=false ENABLE_RAG=false
    ENABLE_APE=false ENABLE_PRIVACY_SHIELD=false ENABLE_ODS_PROXY=false
    ENABLE_TAILSCALE=false ENABLE_LANGFUSE=false ENABLE_BRAVE_SEARCH=false
    rm -f "$INSTALL_DIR/.env" "$INSTALL_DIR/data/pixel-native/preparation/onboarding.json"
}

reset_features
_macos_resolve_support_services
[[ "$ENABLE_LITELLM" == true && "$ENABLE_RECOMMENDED" == false
    && "$ENABLE_SEARXNG" == false && "$ENABLE_WEB_SEARCH" == false ]] \
    || { echo 'FAIL: fresh Core + Pixel selected optional support' >&2; exit 1; }

printf 'PIXEL_WEB_SEARCH_PROVIDER=searxng\n' > "$INSTALL_DIR/.env"
_macos_resolve_support_services
[[ "$ENABLE_SEARXNG" == true && "$ENABLE_WEB_SEARCH" == true ]] \
    || { echo 'FAIL: explicit Pixel SearXNG selection was lost' >&2; exit 1; }

reset_features
printf '{"webSearchProvider":"searxng"}\n' \
    > "$INSTALL_DIR/data/pixel-native/preparation/onboarding.json"
chmod 600 "$INSTALL_DIR/data/pixel-native/preparation/onboarding.json"
_macos_resolve_support_services
[[ "$ENABLE_SEARXNG" == true ]] \
    || { echo 'FAIL: retained Pixel SearXNG selection was lost' >&2; exit 1; }

printf 'PIXEL_WEB_SEARCH_PROVIDER=parallel-free\n' > "$INSTALL_DIR/.env"
_macos_resolve_support_services
[[ "$ENABLE_SEARXNG" == false ]] \
    || { echo 'FAIL: installed provider did not override retained answers' >&2; exit 1; }

reset_features
printf '{"webSearchProvider":"parallel-free"}\n' \
    > "$INSTALL_DIR/data/pixel-native/preparation/onboarding.json"
chmod 600 "$INSTALL_DIR/data/pixel-native/preparation/onboarding.json"
_macos_resolve_support_services
[[ "$ENABLE_SEARXNG" == false && "$ENABLE_WEB_SEARCH" == false ]] \
    || { echo 'FAIL: retained parallel-free provider selected SearXNG' >&2; exit 1; }

reset_features
ENABLE_RECOMMENDED=true
_macos_resolve_support_services
[[ "$ENABLE_LITELLM" == true && "$ENABLE_SEARXNG" == true ]] \
    || { echo 'FAIL: explicit recommended support was lost' >&2; exit 1; }

reset_features
ENABLE_PIXEL=false CLOUD_MODE=true
_macos_resolve_support_services
[[ "$ENABLE_LITELLM" == true && "$ENABLE_SEARXNG" == false ]] \
    || { echo 'FAIL: cloud gateway pulled optional search' >&2; exit 1; }

reset_features
ENABLE_PIXEL=false
_macos_resolve_support_services
[[ "$ENABLE_LITELLM" == true && "$ENABLE_SEARXNG" == false ]] \
    || { echo 'FAIL: base chat UI lost its gateway' >&2; exit 1; }

reset_features
ENABLE_PIXEL=false ENABLE_PERPLEXICA=true
_macos_resolve_support_services
[[ "$ENABLE_SEARXNG" == true ]] \
    || { echo 'FAIL: Perplexica lost SearXNG' >&2; exit 1; }

reset_features
ENABLE_PIXEL=false ENABLE_HERMES=true
_macos_resolve_support_services
[[ "$ENABLE_SEARXNG" == false ]] \
    || { echo 'FAIL: newly selected Hermes pulled optional SearXNG' >&2; exit 1; }

mkdir -p "$INSTALL_DIR/extensions/services/searxng"
printf 'ODS_MODE=local\n' > "$INSTALL_DIR/.env"
printf 'services: {}\n' > "$INSTALL_DIR/extensions/services/searxng/compose.yaml"
_macos_resolve_support_services
[[ "$ENABLE_SEARXNG" == true ]] \
    || { echo 'FAIL: retained SearXNG selection was lost' >&2; exit 1; }
mv "$INSTALL_DIR/extensions/services/searxng/compose.yaml" \
    "$INSTALL_DIR/extensions/services/searxng/compose.yaml.disabled"
_macos_resolve_support_services
[[ "$ENABLE_SEARXNG" == false ]] \
    || { echo 'FAIL: disabled SearXNG was restored by Hermes' >&2; exit 1; }

for service in litellm token-spy searxng; do
    mkdir -p "$INSTALL_DIR/extensions/services/$service"
    printf 'services: {}\n' > "$INSTALL_DIR/extensions/services/$service/compose.yaml"
done
reset_features
_macos_resolve_support_services
_macos_sync_builtin_compose_states
[[ -f "$INSTALL_DIR/extensions/services/litellm/compose.yaml"
    && -f "$INSTALL_DIR/extensions/services/token-spy/compose.yaml.disabled"
    && -f "$INSTALL_DIR/extensions/services/searxng/compose.yaml.disabled" ]] \
    || { echo 'FAIL: Core + Pixel compose state retained optional support' >&2; exit 1; }

ENABLE_RECOMMENDED=true
_macos_resolve_support_services
_macos_sync_builtin_compose_states
[[ -f "$INSTALL_DIR/extensions/services/token-spy/compose.yaml"
    && -f "$INSTALL_DIR/extensions/services/searxng/compose.yaml" ]] \
    || { echo 'FAIL: selected recommended services were not restored' >&2; exit 1; }

echo 'PASS: Mac gateway and optional search selection'
