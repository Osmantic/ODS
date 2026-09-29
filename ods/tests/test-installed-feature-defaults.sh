#!/usr/bin/env bash
# A rerun must recover the selected optional services before it parses flags.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/installers/lib/installed-feature-state.sh"
defaults="$(sed -n '/^DRY_RUN=false$/,/^INTERACTIVE=true$/p' "$ROOT/install-core.sh")"
[[ -n "$defaults" ]] || { echo 'FAIL: installer defaults block missing' >&2; exit 1; }

fixture="$(mktemp -d)"
trap 'rm -rf -- "$fixture"' EXIT
INSTALL_DIR="$fixture"
: >"$INSTALL_DIR/.env"

mark() {
    local service="$1" selection="$2" dir
    dir="$INSTALL_DIR/extensions/services/$service"
    mkdir -p "$dir"
    case "$selection" in
        on) : >"$dir/compose.yaml" ;;
        off) : >"$dir/compose.yaml.disabled" ;;
    esac
}
for service in whisper tts n8n qdrant embeddings token-spy hermes hermes-proxy \
               comfyui ape perplexica privacy-shield langfuse ods-proxy tailscale brave-search; do
    mark "$service" off
done

eval "$defaults"
for flag in ENABLE_VOICE ENABLE_WORKFLOWS ENABLE_RAG ENABLE_RECOMMENDED \
            ENABLE_HERMES ENABLE_COMFYUI ENABLE_APE ENABLE_PERPLEXICA \
            ENABLE_PRIVACY_SHIELD ENABLE_LANGFUSE ENABLE_ODS_PROXY \
            ENABLE_TAILSCALE ENABLE_BRAVE_SEARCH; do
    [[ "${!flag}" == false ]] || { echo "FAIL: $flag reenabled on lean rerun" >&2; exit 1; }
done
[[ "$ENABLE_OPENCODE" == false ]] || exit 1

# Mixed selections and a stale opposite file follow the active Compose name.
mark n8n on
mark qdrant on
mark token-spy on
eval "$defaults"
[[ "$ENABLE_WORKFLOWS" == true && "$ENABLE_RAG" == true && "$ENABLE_RECOMMENDED" == true ]] || {
    echo 'FAIL: active selected features were lost on rerun' >&2; exit 1;
}
[[ "$ENABLE_VOICE" == false && "$ENABLE_PERPLEXICA" == false ]] || {
    echo 'FAIL: disabled selected features were enabled on rerun' >&2; exit 1;
}

# Explicit --all remains after this block in install-core.sh and overrides it.
defaults_line="$(awk '/^INTERACTIVE=true$/ { print NR; exit }' "$ROOT/install-core.sh")"
all_line="$(awk '/^[[:space:]]*--all\)/ { print NR; exit }' "$ROOT/install-core.sh")"
[[ "$all_line" -gt "$defaults_line" ]] || { echo 'FAIL: --all precedes defaults' >&2; exit 1; }
echo 'PASS: installed optional Compose selections survive installer reruns'
