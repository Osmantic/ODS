#!/usr/bin/env bash
# Exercise flag parsing and the real feature phase using isolated directories.
# No agent prompt may override an explicitly selected CLI value.
# Variables below are consumed by the sourced phase or parsed argument loop.
# shellcheck disable=SC2034
set -euo pipefail

# Phase 03 uses Bash 4 features; macOS ships 3.2. Re-exec under a modern Bash
# as the other phase 03 and ods-cli suites do.
if (( BASH_VERSINFO[0] < 4 )); then
    for modern_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        if [[ -x "$modern_bash" ]]; then
            exec "$modern_bash" "$0" "$@"
        fi
    done
    printf '[SKIP] phase 03 requires Bash 4+\n'
    exit 0
fi

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
    INTERACTIVE=true DRY_RUN=false INSTALL_CHOICE=3 TIER=2 ODS_MODE=local
    ENABLE_VOICE=false ENABLE_WORKFLOWS=false ENABLE_RAG=false
    ENABLE_HERMES="$selected"
    HERMES_EXPLICIT=false ENABLE_PIXEL=auto
    ENABLE_OPENCODE=false ENABLE_COMFYUI=false ENABLE_LANGFUSE=false
    ENABLE_RECOMMENDED=false ENABLE_APE=false ENABLE_PERPLEXICA=false
    ENABLE_PRIVACY_SHIELD=false ENABLE_ODS_PROXY=false ENABLE_TAILSCALE=false
    ENABLE_BRAVE_SEARCH=false GPU_COUNT=0 GPU_BACKEND=cpu
    HOST_ARCH=x86_64 HOST_PAGE_SIZE=4096 MAX_CONTEXT=65536 LLM_MODEL_SIZE_MB=0
    SCRIPT_DIR="$tmp/source" INSTALL_DIR="$tmp/install"
    mkdir -p "$SCRIPT_DIR" "$INSTALL_DIR"
    local legacy_flag=false arg
    for arg in "$@"; do
        [[ "$arg" == --openclaw || "$arg" == --no-openclaw ]] && legacy_flag=true
    done
    eval "$parse_source" 2>"$tmp/parse.err"
    # The legacy OpenClaw extension was removed: its flags only print a notice.
    if $legacy_flag; then
        grep -Fq 'The legacy OpenClaw extension was removed;' "$tmp/parse.err"
    else
        [[ ! -s "$tmp/parse.err" ]]
    fi
    [[ -z "${ENABLE_OPENCLAW+x}${OPENCLAW_EXPLICIT+x}" ]]

    ods_progress() { :; }; show_phase() { :; }; show_install_menu() { :; }
    ai() { :; }; ai_bad() { return 1; }; ai_warn() { :; }; log() { :; }
    warn() { :; }; success() { :; }; chapter() { :; }; bootline() { :; }
    signal() { :; }
    ods_sudo_available() { return 0; }
    ods_pixel_resolve_enablement() { printf 'pixel\n'; }
    ods_pixel_model_route_class() { printf 'managed-gateway\n'; }
    # Phase 03 resolves Pixel's web search provider and asks whether Portal
    # should be the default chat; both live in libraries this fixture omits.
    ods_pixel_resolve_search_provider() { printf 'searxng\n'; }
    ods_should_default_portal_chat() { return 1; }
    prompts=0 agent_prompts=0 legacy_prompts=0
    read() {
        local prompt="$2" target="${*: -1}" response=''
        prompts=$((prompts + 1))
        if [[ "$prompt" == *'Hermes Agent?'* ]]; then
            agent_prompts=$((agent_prompts + 1))
            response="$answer"
        fi
        [[ "$prompt" != *'OpenClaw'* ]] || legacy_prompts=$((legacy_prompts + 1))
        printf -v "$target" '%s' "$response"
    }
    source <(printf '%s\n' "$phase_source") >/dev/null
    [[ "$ENABLE_HERMES" == "$expected" ]]
    [[ "$HERMES_EXPLICIT" == "$explicit" ]]
    [[ "$ENABLE_PIXEL_RUNTIME" == true ]]
    [[ "$legacy_prompts" == 0 ]]
    if [[ "$explicit" == true ]]; then
        [[ "$prompts" == 7 && "$agent_prompts" == 0 ]]
    else
        [[ "$prompts" == 8 && "$agent_prompts" == 1 ]]
    fi
    printf 'PASS: %s\n' "$label"
)

run_case 'Custom skips an explicitly disabled agent; --no-openclaw is only a notice' true true y false \
    --pixel --no-hermes --no-openclaw
run_case 'Custom skips an explicitly enabled agent; --openclaw is only a notice' true false n true \
    --pixel --hermes --openclaw
run_case 'Custom still accepts agent opt-in without explicit flags' false false y true --pixel
run_case 'Custom still accepts agent opt-out without explicit flags' false true n false --pixel
run_case 'Custom preserves agent defaults on Enter' false false '' false --pixel
