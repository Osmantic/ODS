#!/usr/bin/env bash
# Exercise flag parsing and the real feature phase using isolated directories.
# No agent prompt may override an explicitly selected CLI value.
# Variables below are consumed by the sourced phase or parsed argument loop.
# shellcheck disable=SC2034
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
parse_source="$(sed -n '/^while \[\[ \$# -gt 0 \]\]; do$/,/^done$/p' "$ROOT/install-core.sh")"
[[ -n "$parse_source" ]]
phase_source="$(cat "$ROOT/installers/phases/03-features.sh")"
# Use our read stub rather than the controlling terminal.
phase_source="${phase_source//< \/dev\/tty/}"

run_case() (
    local label="$1" explicit="$2" selected="$3" answer="$4" expected="$5"
    shift 5
    INTERACTIVE=true DRY_RUN=false INSTALL_CHOICE=3 TIER=2 ODS_MODE=lemonade
    ENABLE_VOICE=false ENABLE_WORKFLOWS=false ENABLE_RAG=false
    ENABLE_HERMES="$selected" ENABLE_OPENCLAW="$selected"
    HERMES_EXPLICIT=false OPENCLAW_EXPLICIT=false ENABLE_PIXEL=auto
    ENABLE_OPENCODE=false ENABLE_COMFYUI=false ENABLE_LANGFUSE=false
    ENABLE_RECOMMENDED=false ENABLE_APE=false ENABLE_PERPLEXICA=false
    ENABLE_PRIVACY_SHIELD=false ENABLE_ODS_PROXY=false ENABLE_TAILSCALE=false
    ENABLE_BRAVE_SEARCH=false GPU_COUNT=0 GPU_BACKEND=cpu
    HOST_ARCH=x86_64 HOST_PAGE_SIZE=4096 MAX_CONTEXT=65536 LLM_MODEL_SIZE_MB=0
    SCRIPT_DIR="$tmp/source" INSTALL_DIR="$tmp/install"
    mkdir -p "$SCRIPT_DIR" "$INSTALL_DIR"
    eval "$parse_source"

    ods_progress() { :; }; show_phase() { :; }; show_install_menu() { :; }
    ai() { :; }; ai_bad() { return 1; }; ai_warn() { :; }; log() { :; }
    warn() { :; }; success() { :; }; chapter() { :; }; bootline() { :; }
    signal() { :; }
    ods_pixel_resolve_enablement() { printf 'pixel\n'; }
    ods_pixel_model_route_class() { printf 'lemonade\n'; }
    prompts=0 agent_prompts=0
    read() {
        local prompt="$2" target="${*: -1}" response=''
        prompts=$((prompts + 1))
        if [[ "$prompt" == *'Hermes Agent?'* || "$prompt" == *'Enable OpenClaw'* ]]; then
            agent_prompts=$((agent_prompts + 1))
            response="$answer"
        fi
        printf -v "$target" '%s' "$response"
    }
    source <(printf '%s\n' "$phase_source") >/dev/null
    [[ "$ENABLE_HERMES" == "$expected" && "$ENABLE_OPENCLAW" == "$expected" ]]
    [[ "$HERMES_EXPLICIT" == "$explicit" && "$OPENCLAW_EXPLICIT" == "$explicit" ]]
    [[ "$ENABLE_PIXEL_RUNTIME" == true ]]
    if [[ "$explicit" == true ]]; then
        [[ "$prompts" == 6 && "$agent_prompts" == 0 ]]
    else
        [[ "$prompts" == 8 && "$agent_prompts" == 2 ]]
    fi
    printf 'PASS: %s\n' "$label"
)

run_case 'Custom skips explicitly disabled agents with Pixel enabled' true true y false \
    --pixel --no-hermes --no-openclaw
run_case 'Custom skips explicitly enabled agents' true false n true \
    --pixel --hermes --openclaw
run_case 'Custom still accepts agent opt-in without explicit flags' false false y true --pixel
run_case 'Custom still accepts agent opt-out without explicit flags' false true n false --pixel
run_case 'Custom preserves agent defaults on Enter' false false '' false --pixel
