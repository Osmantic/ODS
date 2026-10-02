#!/bin/bash
# ============================================================================
# ODS Installer — Orchestrator
# ============================================================================
# Unified installer, uses docker-compose.yml
# profiles for optional features.
# Mission: M5 (Clonable ODS Setup Server)
#
# This file sources library modules (pure functions, no side effects) then
# runs each install phase in order.  Individual modules live under:
#   installers/lib/      — reusable function libraries
#   installers/phases/   — sequential install steps (execute on source)
#
# See each module's header for what it expects and provides.
# ============================================================================

set -euo pipefail

#=============================================================================
# Cleanup on Failure
#=============================================================================
# Track what phases have completed so we can provide useful context on failure.
export INSTALL_PHASE="init"
cleanup_on_error() {
    local exit_code=$?
    echo ""
    echo -e "${RED:-}[ERROR] Installation failed during phase: ${INSTALL_PHASE}${NC:-}"
    echo -e "${AMB:-}        Log file: ${LOG_FILE:-/tmp/ods-install.log}${NC:-}"
    echo ""
    echo "The install did not complete. Partial state may exist at:"
    echo "  ${INSTALL_DIR:-~/ods}"
    echo ""
    echo "Keep this directory and its recovery receipts intact."
    echo "Review the failed phase and log before retrying; some phases require recovery."
    echo "For a fresh install, use the installed ods-uninstall.sh and resolve any"
    echo "cleanup refusal before reinstalling. Do not delete the directory manually:"
    echo "ODS services and protected Pixel state may exist outside it."
    exit "$exit_code"
}
trap cleanup_on_error ERR

#=============================================================================
# Interrupt Protection
#=============================================================================
# Accidental keypresses (Ctrl+C, Ctrl+Z) shouldn't silently kill the install.
# We require a double-tap of Ctrl+C within 3 seconds to actually abort.
LAST_SIGINT=0
interrupt_handler() {
    local now
    now=$(date +%s)
    if (( now - LAST_SIGINT <= 3 )); then
        echo ""
        echo -e "${AMB:-}[!] Install cancelled by user.${NC:-}"
        if declare -F cancel_active_download >/dev/null 2>&1; then
            cancel_active_download
        fi
        echo -e "${GRN:-}    Log file: ${LOG_FILE:-/tmp/ods-install.log}${NC:-}"
        exit 130
    fi
    LAST_SIGINT=$now
    echo ""
    echo -e "${AMB:-}[!] Press Ctrl+C again within 3 seconds to cancel the install.${NC:-}"
}
trap interrupt_handler INT
# Ignore Ctrl+Z (SIGTSTP) entirely — backgrounding the installer breaks things
trap '' TSTP

#=============================================================================
# Load libraries (pure functions, no side effects)
#=============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$(uname -s 2>/dev/null || true)" == "Linux" ]]; then
    export ODS_PYTHON_PREFER_SYSTEM="${ODS_PYTHON_PREFER_SYSTEM:-1}"
fi

source "$SCRIPT_DIR/installers/lib/constants.sh"
source "$SCRIPT_DIR/installers/lib/secure-log.sh"
source "$SCRIPT_DIR/installers/lib/logging.sh"
source "$SCRIPT_DIR/installers/lib/ui.sh"
source "$SCRIPT_DIR/installers/lib/sudo.sh"
source "$SCRIPT_DIR/installers/lib/detection.sh"
source "$SCRIPT_DIR/installers/lib/host-arch.sh"
source "$SCRIPT_DIR/installers/lib/tier-map.sh"
source "$SCRIPT_DIR/installers/lib/model-selector.sh"
source "$SCRIPT_DIR/installers/lib/docker-images.sh"
source "$SCRIPT_DIR/installers/lib/compose-images.sh"
source "$SCRIPT_DIR/installers/lib/compose-select.sh"
source "$SCRIPT_DIR/installers/lib/compose-failure-report.sh"
source "$SCRIPT_DIR/installers/lib/readiness-summary.sh"
source "$SCRIPT_DIR/installers/lib/packaging.sh"
source "$SCRIPT_DIR/installers/lib/python-runtime.sh"
source "$SCRIPT_DIR/installers/lib/progress.sh"
source "$SCRIPT_DIR/installers/lib/model-lifecycle-lock.sh"
source "$SCRIPT_DIR/installers/lib/cli-link.sh"
source "$SCRIPT_DIR/installers/lib/install-mode.sh"
source "$SCRIPT_DIR/installers/lib/installed-feature-state.sh"
source "$SCRIPT_DIR/installers/lib/external-services.sh"
source "$SCRIPT_DIR/installers/lib/pixel-integration.sh"
source "$SCRIPT_DIR/installers/lib/pixel-host-install.sh"
source "$SCRIPT_DIR/lib/pixel-uninstall.sh"
if [[ -f "$SCRIPT_DIR/lib/service-registry.sh" ]]; then 
    source "$SCRIPT_DIR/lib/service-registry.sh" 
fi

#=============================================================================
# Command Line Args
#=============================================================================
DRY_RUN=false
PREFLIGHT_ONLY=false
SKIP_DOCKER=false
FORCE=false
TIER=""
# Phase 03 selects Portal chat on fresh, qualified Pixel hosts. Keep WebUI as
# the provisional choice until that host check completes. Reruns retain their
# installed selection, and older installs without the key keep WebUI.
ODS_EXISTING_INSTALL=false
[[ -f "$INSTALL_DIR/.env" ]] && ODS_EXISTING_INSTALL=true
ODS_GATEWAY_ONLY=false
ENABLE_OPEN_WEBUI=true
WEBUI_EXPLICIT=false
if $ODS_EXISTING_INSTALL &&
   [[ "$(external_llm_env_value "$INSTALL_DIR/.env" ODS_GATEWAY_ONLY || true)" == true ]]; then
    ODS_GATEWAY_ONLY=true
    ENABLE_OPEN_WEBUI="$(external_llm_env_value "$INSTALL_DIR/.env" ENABLE_OPEN_WEBUI || true)"
    [[ "$ENABLE_OPEN_WEBUI" == true ]] || ENABLE_OPEN_WEBUI=false
fi
if $ODS_EXISTING_INSTALL &&
   [[ "$(external_llm_env_value "$INSTALL_DIR/.env" ENABLE_OPEN_WEBUI || true)" == false ]]; then
    ENABLE_OPEN_WEBUI=false
fi
ENABLE_WHISPER="$(ods_installed_service_default "$INSTALL_DIR" whisper "$ODS_EXISTING_INSTALL")"
ENABLE_TTS="$(ods_installed_service_default "$INSTALL_DIR" tts "$ODS_EXISTING_INSTALL")"
ENABLE_VOICE=false
[[ "$ENABLE_WHISPER" == true || "$ENABLE_TTS" == true ]] && ENABLE_VOICE=true
ENABLE_WORKFLOWS="$(ods_installed_service_default "$INSTALL_DIR" n8n "$ODS_EXISTING_INSTALL")"
ENABLE_RAG="$(ods_installed_service_default "$INSTALL_DIR" qdrant "$ODS_EXISTING_INSTALL")"
ENABLE_RECOMMENDED="$(ods_installed_service_default "$INSTALL_DIR" token-spy "$ODS_EXISTING_INSTALL")"
# Pixel is the core conversational experience on qualified Linux hosts after a separate
# written license agreement is acknowledged. Existing ODS tools remain available.
# OpenClaw is deprecated and remains explicit opt-in.
ENABLE_HERMES="$(ods_installed_service_default "$INSTALL_DIR" hermes "$ODS_EXISTING_INSTALL")"
ENABLE_HERMES_PROXY="$(ods_installed_service_default "$INSTALL_DIR" hermes-proxy "$ENABLE_HERMES")"
ENABLE_PIXEL="${ENABLE_PIXEL:-auto}"
PIXEL_EXPLICIT=false
HERMES_EXPLICIT=false
ENABLE_OPENCLAW=false
OPENCLAW_EXPLICIT=false
ENABLE_OPENCODE=false
if $ODS_EXISTING_INSTALL && command -v systemctl >/dev/null 2>&1 \
    && ods_systemctl_user is-enabled --quiet opencode-web.service 2>/dev/null; then
    ENABLE_OPENCODE=true
fi
ENABLE_DEVTOOLS=false
if $ODS_EXISTING_INSTALL &&
   [[ "$(external_llm_env_value "$INSTALL_DIR/.env" ENABLE_DEVTOOLS || true)" == true ]]; then
    ENABLE_DEVTOOLS=true
fi
DEVTOOLS_EXPLICIT=false
ENABLE_COMFYUI="$(ods_installed_service_default "$INSTALL_DIR" comfyui "$ODS_EXISTING_INSTALL")"
ENABLE_APE="$(ods_installed_service_default "$INSTALL_DIR" ape "$ODS_EXISTING_INSTALL")"
ENABLE_PERPLEXICA="$(ods_installed_service_default "$INSTALL_DIR" perplexica "$ODS_EXISTING_INSTALL")"
ENABLE_PRIVACY_SHIELD="$(ods_installed_service_default "$INSTALL_DIR" privacy-shield "$ODS_EXISTING_INSTALL")"
ENABLE_ODS_PROXY="$(ods_installed_service_default "$INSTALL_DIR" ods-proxy false)"
ENABLE_TAILSCALE="$(ods_installed_service_default "$INSTALL_DIR" tailscale false)"
ENABLE_BRAVE_SEARCH="$(ods_installed_service_default "$INSTALL_DIR" brave-search false)"
# Langfuse (LLM observability) defaults OFF on fresh installs because its
# clickhouse + postgres + minio stack adds ~500MB baseline memory that is
# nontrivial even on Tier 3+ systems. Users opt in via --langfuse, --all,
# the Custom menu, or post-install `ods enable langfuse`.
ENABLE_LANGFUSE="$(ods_installed_service_default "$INSTALL_DIR" langfuse false)"
INTERACTIVE=true
ODS_MODE_EXPLICIT=false
[[ -n "${ODS_MODE:-}" ]] && ODS_MODE_EXPLICIT=true
ODS_MODE="${ODS_MODE:-local}"
LEMONADE_EXTERNAL="${LEMONADE_EXTERNAL:-false}"
LEMONADE_BASE_URL="${LEMONADE_BASE_URL:-}"
# Keep omission distinct from an explicit direct override until .env is read.
LEMONADE_HOST_TRANSPORT="${LEMONADE_HOST_TRANSPORT:-}"
ODS_WINDOWS_SYSTEM_DIRECTORY="${ODS_WINDOWS_SYSTEM_DIRECTORY:-}"
LEMONADE_API_KEY="${LEMONADE_API_KEY:-}"
LEMONADE_MODEL="${LEMONADE_MODEL:-}"
# Display only: the GPU that runs an external Lemonade (e.g. Windows under WSL).
LEMONADE_GPU_NAME="${LEMONADE_GPU_NAME:-}"
LEMONADE_GPU_VRAM_MB="${LEMONADE_GPU_VRAM_MB:-}"
OFFLINE_MODE=false   # M1 integration: fully air-gapped operation
NO_BOOTSTRAP=false  # Skip bootstrap fast-start, download full model in foreground
BIND_ADDRESS_EXPLICIT=false
[[ -n "${BIND_ADDRESS:-}" ]] && BIND_ADDRESS_EXPLICIT=true
BIND_ADDRESS="${BIND_ADDRESS:-127.0.0.1}"
SUMMARY_JSON_FILE="${SUMMARY_JSON_FILE:-}"
EXTERNAL_LLM_URL="${EXTERNAL_LLM_URL:-}"
EXTERNAL_LLM_PROVIDER="${EXTERNAL_LLM_PROVIDER:-auto}"
EXTERNAL_LLM_MODEL="${EXTERNAL_LLM_MODEL:-}"
EXTERNAL_LLM_API_KEY_FILE="${EXTERNAL_LLM_API_KEY_FILE:-}"
EXTERNAL_LLM_API_KEY_DISABLE=false
EXTERNAL_LLM_AUTO_REUSE="${EXTERNAL_LLM_AUTO_REUSE:-false}"
EXTERNAL_LLM_DISABLE=false
ODS_RESELECT_MODEL="${ODS_RESELECT_MODEL:-false}"

usage() {
    cat << EOF
ODS Installer v${VERSION}

Usage: $0 [OPTIONS]

Options:
    --dry-run         Show what would be done without making changes
    --preflight-only  Run only the pre-flight environment checks, change nothing,
                      and exit (get-ods.sh --force runs this before removing
                      an existing installation)
    --skip-docker     Skip Docker installation (assume already installed)
    --force           Overwrite existing installation
    --tier N          Force specific tier (1-4) instead of auto-detect
    --cloud           Cloud mode: skip GPU detection, use LiteLLM + cloud APIs
    --use-existing-lemonade
                      Use an already-running Lemonade SDK server as the AMD LLM runtime
    --lemonade-url U  Lemonade server URL for --use-existing-lemonade
                      (auto-detects localhost:13305, then localhost:8000 when omitted)
    --lemonade-host-transport direct|model-router
                      Host-agent verification network: direct (default), or this
                      installation's model-router container for Windows/WSL Lemonade
    --windows-system-directory PATH
                      Windows System32 directory supplied by Windows setup
    --lemonade-api-key K
                      API key LiteLLM should send to the existing Lemonade server
    --lemonade-model M
                      Exact model id the existing Lemonade server serves
    --lemonade-gpu-name N, --lemonade-gpu-vram-mb MB
                      GPU that runs the existing Lemonade, shown in the hardware scan
    --external-llm-url U
                      Reuse an OpenAI-compatible local or LAN endpoint
    --external-llm-provider P
                      External provider: auto, ollama, lmstudio, or openai-compatible
    --external-llm-model M
                      Exact model id exposed by the external provider
    --gateway-only    API-first install using a verified external model; skip Open WebUI
                      and ODS-managed llama-server (requires --external-llm-url)
    --with-webui      Keep or restore Open WebUI
    --no-webui        Use Portal as the only chat UI (requires --pixel on a fresh ordinary install)
    --no-gateway-only Return a gateway install to the ordinary UI selection
    --external-llm-key-file PATH
                      Owner-only API key file for an authenticated external model
    --no-external-llm-key
                      Stop sending the saved key to the selected external model
    --reuse-external-llm
                      Allow non-interactive reuse of a detected matching model
    --no-external-llm
                      Disable a persisted external LLM selection on this rerun
    --reselect-model  Replace a valid active local model with the current installer recommendation
    --voice           Enable voice services (Whisper + Kokoro)
    --no-voice        Disable voice services
    --workflows       Enable n8n workflow automation
    --no-workflows    Disable n8n workflow automation
    --rag             Enable RAG with Qdrant vector database
    --no-rag          Disable RAG / Qdrant
    --recommended     Enable LiteLLM + SearXNG + Token Spy support services
    --no-recommended  Disable recommended support services
    --hermes          Enable Hermes Agent alongside Pixel
    --no-hermes       Disable Hermes Agent
    --pixel           Require Pixel alongside the existing ODS tools on a qualified Linux host
    --no-pixel        Disable Pixel; keep the other configured ODS tools
    --openclaw        Enable OpenClaw (DEPRECATED — see docs/MIGRATION-OPENCLAW-TO-HERMES.md)
    --no-openclaw     Disable OpenClaw
    --opencode        Enable the optional OpenCode browser IDE
    --no-opencode     Disable the optional OpenCode browser IDE (default)
    --with-devtools   Install Claude Code and Codex CLI on this host
    --no-devtools     Skip developer CLI installation; keep existing binaries
    --comfyui         Enable ComfyUI image generation
    --no-comfyui      Disable ComfyUI image generation (saves ~34GB)
    --odsforge      Deprecated no-op; ODSForge has been removed
    --no-odsforge   Deprecated no-op; ODSForge has been removed
    --langfuse        Enable Langfuse LLM observability (off by default)
    --no-langfuse     Explicitly disable Langfuse (for --all overrides)
    --all             Enable all optional services (including Langfuse)
    --non-interactive Run without prompts (use defaults or flags)
    --offline         M1 mode: Configure for fully offline/air-gapped operation
    --lan             Bind services to 0.0.0.0 for LAN access (headless servers)
    --no-bootstrap    Skip bootstrap fast-start (download full model in foreground)
    --summary-json P  Write machine-readable install summary JSON to path P
    -h, --help        Show this help

Tiers:
    1 - Entry Level   (8GB+ VRAM, 7B models)
    2 - Prosumer      (12GB+ VRAM, 14B-32B AWQ models)
    3 - Pro           (24GB+ VRAM, 32B models)
    4 - Enterprise    (48GB+ VRAM or dual GPU, 72B models)

Port Configuration:
    All service ports are configurable via .env (see .env.example).
    Example: WEBUI_PORT=8080 OLLAMA_PORT=11435 ./install.sh

Examples:
    $0                           # Interactive setup
    $0 --tier 2 --voice          # Tier 2 with voice
    $0 --all --non-interactive   # Full stack, no prompts
    $0 --cloud                   # Cloud mode (no GPU needed, uses API keys)
    $0 --use-existing-lemonade   # Wrap an existing Lemonade SDK runtime
    $0 --offline --all           # Fully offline (M1 mode) with all services
    $0 --dry-run                 # Preview installation

EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run) DRY_RUN=true; shift ;;
        --preflight-only) PREFLIGHT_ONLY=true; shift ;;
        --skip-docker) SKIP_DOCKER=true; shift ;;
        --force) FORCE=true; shift ;;
        --tier) TIER="$2"; shift 2 ;;
        --cloud) ODS_MODE="cloud"; ODS_MODE_EXPLICIT=true; shift ;;
        --use-existing-lemonade) LEMONADE_EXTERNAL=true; ODS_MODE="lemonade"; ODS_MODE_EXPLICIT=true; shift ;;
        --lemonade-url) LEMONADE_EXTERNAL=true; ODS_MODE="lemonade"; ODS_MODE_EXPLICIT=true; LEMONADE_BASE_URL="$2"; shift 2 ;;
        --lemonade-host-transport)
            case "${2:-}" in direct|model-router) LEMONADE_HOST_TRANSPORT="$2" ;; *) echo "--lemonade-host-transport requires direct or model-router" >&2; exit 1 ;; esac
            shift 2 ;;
        --windows-system-directory)
            [[ -n "${2:-}" && "${2:-}" != --* ]] || { echo "--windows-system-directory requires a Windows System32 path" >&2; exit 1; }
            ODS_WINDOWS_SYSTEM_DIRECTORY="$2"
            shift 2 ;;
        --lemonade-api-key) LEMONADE_API_KEY="$2"; shift 2 ;;
        --lemonade-model) LEMONADE_MODEL="$2"; shift 2 ;;
        --lemonade-gpu-name) LEMONADE_GPU_NAME="$2"; shift 2 ;;
        --lemonade-gpu-vram-mb)
            [[ -n "${2:-}" ]] || { echo "--lemonade-gpu-vram-mb needs a number of megabytes" >&2; exit 1; }
            LEMONADE_GPU_VRAM_MB="$2"; shift 2 ;;
        --external-llm-url) EXTERNAL_LLM_URL="$2"; shift 2 ;;
        --external-llm-provider) EXTERNAL_LLM_PROVIDER="$2"; shift 2 ;;
        --external-llm-model) EXTERNAL_LLM_MODEL="$2"; shift 2 ;;
        --gateway-only) ODS_GATEWAY_ONLY=true; ENABLE_OPEN_WEBUI=false; WEBUI_EXPLICIT=true; ODS_MODE=local; ODS_MODE_EXPLICIT=true; shift ;;
        --with-webui) ENABLE_OPEN_WEBUI=true; WEBUI_EXPLICIT=true; shift ;;
        --no-webui) ENABLE_OPEN_WEBUI=false; WEBUI_EXPLICIT=true; shift ;;
        --no-gateway-only) ODS_GATEWAY_ONLY=false; ENABLE_OPEN_WEBUI=true; WEBUI_EXPLICIT=true; shift ;;
        --external-llm-key-file) EXTERNAL_LLM_API_KEY_FILE="$2"; EXTERNAL_LLM_API_KEY_DISABLE=false; shift 2 ;;
        --no-external-llm-key) EXTERNAL_LLM_API_KEY_FILE=""; EXTERNAL_LLM_API_KEY_DISABLE=true; shift ;;
        --reuse-external-llm) EXTERNAL_LLM_AUTO_REUSE=true; shift ;;
        --no-external-llm) EXTERNAL_LLM_DISABLE=true; shift ;;
        --reselect-model) ODS_RESELECT_MODEL=true; shift ;;
        --voice) ENABLE_VOICE=true; ENABLE_WHISPER=true; ENABLE_TTS=true; shift ;;
        --no-voice) ENABLE_VOICE=false; ENABLE_WHISPER=false; ENABLE_TTS=false; shift ;;
        --workflows) ENABLE_WORKFLOWS=true; shift ;;
        --no-workflows) ENABLE_WORKFLOWS=false; shift ;;
        --rag) ENABLE_RAG=true; shift ;;
        --no-rag) ENABLE_RAG=false; shift ;;
        --recommended) ENABLE_RECOMMENDED=true; shift ;;
        --no-recommended) ENABLE_RECOMMENDED=false; shift ;;
        --hermes) ENABLE_HERMES=true; ENABLE_HERMES_PROXY=true; HERMES_EXPLICIT=true; shift ;;
        --no-hermes) ENABLE_HERMES=false; ENABLE_HERMES_PROXY=false; HERMES_EXPLICIT=true; shift ;;
        --pixel) ENABLE_PIXEL=true; PIXEL_EXPLICIT=true; shift ;;
        --no-pixel) ENABLE_PIXEL=false; PIXEL_EXPLICIT=true; shift ;;
        --openclaw) ENABLE_OPENCLAW=true; OPENCLAW_EXPLICIT=true; shift ;;
        --no-openclaw) ENABLE_OPENCLAW=false; OPENCLAW_EXPLICIT=true; shift ;;
        --opencode) ENABLE_OPENCODE=true; shift ;;
        --no-opencode) ENABLE_OPENCODE=false; shift ;;
        --with-devtools) ENABLE_DEVTOOLS=true; DEVTOOLS_EXPLICIT=true; shift ;;
        --no-devtools) ENABLE_DEVTOOLS=false; DEVTOOLS_EXPLICIT=true; shift ;;
        --comfyui) ENABLE_COMFYUI=true; shift ;;
        --no-comfyui) ENABLE_COMFYUI=false; shift ;;
        --odsforge) printf '%s\n' '[WARN] ODSForge has been removed; ignoring --odsforge' >&2; shift ;;
        --no-odsforge) printf '%s\n' '[WARN] ODSForge has been removed; ignoring --no-odsforge' >&2; shift ;;
        --langfuse) ENABLE_LANGFUSE=true; shift ;;
        # NOTE: with --all, --no-langfuse must appear AFTER --all on the command
        # line (flag processing is case-loop ordered, matching comfyui).
        --no-langfuse) ENABLE_LANGFUSE=false; shift ;;
        # --all enables the Hermes fallback but NOT deprecated OpenClaw —
        # the deprecated agent is opt-in via --openclaw for the deprecation
        # release. Will be dropped entirely in the removal release.
        # ENABLE_ODS_PROXY is included so magic-link invite URLs
        # (http://auth.<device>.local/magic-link/<token>) actually resolve.
        # Without ods-proxy on host :80, mDNS publishes the hostname but
        # nothing serves it, and a phone clicking the invite gets
        # "site can't be reached." Operators who don't want the LAN-facing
        # surface can set ENABLE_ODS_PROXY=false in .env after install.
        --all) ENABLE_VOICE=true; ENABLE_WHISPER=true; ENABLE_TTS=true; ENABLE_WORKFLOWS=true; ENABLE_RAG=true; ENABLE_RECOMMENDED=true; ENABLE_HERMES=true; ENABLE_HERMES_PROXY=true; ENABLE_OPENCLAW=false; ENABLE_OPENCODE=true; ENABLE_DEVTOOLS=true; ENABLE_COMFYUI=true; ENABLE_APE=true; ENABLE_PERPLEXICA=true; ENABLE_PRIVACY_SHIELD=true; ENABLE_LANGFUSE=true; ENABLE_ODS_PROXY=true; ENABLE_OPEN_WEBUI=true; WEBUI_EXPLICIT=true; shift ;;
        --non-interactive) INTERACTIVE=false; shift ;;
        --offline) OFFLINE_MODE=true; shift ;;
        --lan) BIND_ADDRESS="0.0.0.0"; BIND_ADDRESS_EXPLICIT=true; shift ;;
        --no-bootstrap) NO_BOOTSTRAP=true; shift ;;
        --summary-json) SUMMARY_JSON_FILE="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) printf '[ERROR] Unknown option: %s\n' "$1" >&2; exit 1 ;;
    esac
done

if ! $ODS_GATEWAY_ONLY && [[ "$ENABLE_OPEN_WEBUI" != true ]] &&
   ! $ODS_EXISTING_INSTALL && [[ "$ENABLE_PIXEL" != true ]]; then
    echo "--no-webui on a fresh ordinary install requires --pixel so Portal supplies chat" >&2
    exit 1
fi
if [[ "$ENABLE_OPEN_WEBUI" != true ]] &&
   { [[ "$ENABLE_VOICE" == true ]] || [[ "$ENABLE_RAG" == true ]] ||
     [[ "$ENABLE_ODS_PROXY" == true ]]; }; then
    echo "Voice, RAG documents, and ODS proxy currently require Open WebUI; use --with-webui or leave those services off" >&2
    exit 1
fi

if $ODS_GATEWAY_ONLY; then
    if [[ "$ODS_MODE" != local || "$EXTERNAL_LLM_DISABLE" == true ]]; then
        echo "--gateway-only requires the local external-model route" >&2
        exit 1
    fi
    _gateway_external_url="$EXTERNAL_LLM_URL"
    if [[ -z "$_gateway_external_url" && "$ODS_EXISTING_INSTALL" == true ]]; then
        _gateway_external_url="$(external_llm_env_value "$INSTALL_DIR/.env" EXTERNAL_LLM_URL || true)"
    fi
    if [[ -z "$_gateway_external_url" ]]; then
        echo "--gateway-only requires --external-llm-url or a saved external route" >&2
        exit 1
    fi
    if [[ "$ENABLE_OPEN_WEBUI" != true ]]; then
        if [[ "$ENABLE_PIXEL" == true ]]; then
            echo "--pixel requires --with-webui in gateway-only mode" >&2
            exit 1
        fi
        [[ "$ENABLE_PIXEL" != auto ]] || ENABLE_PIXEL=false
    fi
    unset _gateway_external_url
fi
export ODS_GATEWAY_ONLY ENABLE_OPEN_WEBUI

# Validate external Lemonade VRAM from either flags or the environment before
# any phase can evaluate it as Bash arithmetic. Empty retains auto-detection.
if [[ -n "$LEMONADE_GPU_VRAM_MB" ]]; then
    [[ "$LEMONADE_GPU_VRAM_MB" =~ ^[0-9]+$ ]] || {
        echo "LEMONADE_GPU_VRAM_MB must be a nonnegative decimal number of megabytes" >&2
        exit 1
    }
    _lemonade_vram="${LEMONADE_GPU_VRAM_MB#"${LEMONADE_GPU_VRAM_MB%%[!0]*}"}"
    _lemonade_vram="${_lemonade_vram:-0}"
    # Phase 02 adds 512 before converting MiB to GiB; leave room in int64.
    # Compare equal-length decimal strings without overflowing the validator.
    # shellcheck disable=SC2071
    if [[ ${#_lemonade_vram} -gt 19 ||
        ( ${#_lemonade_vram} -eq 19 && "$_lemonade_vram" > 9223372036854775295 ) ]]; then
        echo "LEMONADE_GPU_VRAM_MB exceeds the supported integer range" >&2
        exit 1
    fi
    LEMONADE_GPU_VRAM_MB="$_lemonade_vram"
fi
unset _lemonade_vram

# Help and malformed options exit without creating a log. Every remaining
# path prepares a private diagnostic file before the first logging call.
if ! ods_prepare_install_log "$LOG_FILE"; then
    exit 1
fi

# Argument parsing establishes interactivity. Resolve the presentation once so
# non-interactive/CI/GUI output cannot inherit terminal color from a real TTY.
ods_apply_presentation_mode

_requested_ods_mode="$ODS_MODE"
ODS_MODE="$(ods_preserve_existing_install_mode "$ODS_MODE" "$ODS_MODE_EXPLICIT" "$INSTALL_DIR/.env")"
if [[ "$ODS_MODE_EXPLICIT" != "true" && "$ODS_MODE" != "$_requested_ods_mode" ]]; then
    log "Existing ODS mode detected; preserving ODS_MODE=$ODS_MODE for this installer rerun"
fi
unset _requested_ods_mode

if [[ "${LEMONADE_EXTERNAL,,}" == "true" ]]; then
    ODS_MODE="lemonade"
    ENABLE_RECOMMENDED=true
    # An empty LEMONADE_MODEL still lets phase 06 discover the model.
    export LEMONADE_EXTERNAL LEMONADE_BASE_URL LEMONADE_HOST_TRANSPORT ODS_WINDOWS_SYSTEM_DIRECTORY LEMONADE_API_KEY LEMONADE_MODEL LEMONADE_GPU_NAME LEMONADE_GPU_VRAM_MB
fi

export EXTERNAL_LLM_URL EXTERNAL_LLM_PROVIDER EXTERNAL_LLM_MODEL
export EXTERNAL_LLM_AUTO_REUSE EXTERNAL_LLM_DISABLE ODS_RESELECT_MODEL

# OpenClaw deprecation back-compat: preserve OpenClaw on UPGRADES of installs
# that previously had it enabled. The earlier heuristic — "does the compose
# file exist on disk?" — was wrong: extensions/services/openclaw/compose.yaml
# is part of the source tree, so every fresh install (including `--all` which
# explicitly sets ENABLE_OPENCLAW=false) was being silently re-enabled. That
# tacked ~20 minutes onto every install (a slow OpenClaw container blocking
# the phase-12 health-link loop) and contradicted both the deprecation policy
# AND what `--all` claims to do.
#
# Correct heuristic: there's an actual OpenClaw container on this host (running
# or stopped from a prior install), OR there's persisted OpenClaw data on disk.
# Either signal means the user already opted in once, so preserve their choice
# through the deprecation window. A fresh install matches neither and leaves
# ENABLE_OPENCLAW at its --no-openclaw / --all / default-false value.
if [[ "$OPENCLAW_EXPLICIT" != "true" ]]; then
    _existing_openclaw=false
    if command -v docker >/dev/null 2>&1 \
       && docker ps -a --filter "name=^/ods-openclaw$" --format '{{.Names}}' 2>/dev/null \
            | grep -q '^ods-openclaw$'; then
        _existing_openclaw=true
    fi
    if [[ -d "$INSTALL_DIR/data/openclaw" ]] \
       && [[ -n "$(ls -A "$INSTALL_DIR/data/openclaw" 2>/dev/null)" ]]; then
        _existing_openclaw=true
    fi
    if $_existing_openclaw; then
        ENABLE_OPENCLAW=true
        log "Existing OpenClaw install detected; preserving it for this deprecation release"
    fi
    unset _existing_openclaw
fi

# Detect distro + package manager (after arg parsing so --help still shows
# the correct VERSION before /etc/os-release overwrites it)
detect_pkg_manager
log "Installer run started: pid=$$, script=$0"

# get-ods.sh --force runs this through installers/reinstall-preflight.sh while
# the installation it will replace is still intact. Run the phase-01
# environment checks that stop an install (root, OS, required tools and
# network, install-dir filesystem, Docker Desktop sharing) and exit before the
# sudo prompt, prerequisite installs and every later phase. Disk and other
# requirement shortfalls are not install-stopping here: phase 04 only warns
# about them (or asks, when interactive), and phase 05 provisions Docker.
if [[ "$PREFLIGHT_ONLY" == "true" ]]; then
    trap 'echo "[ERROR] Preflight stopped during phase: ${INSTALL_PHASE}. No changes were made." >&2; exit 1' ERR
    INSTALL_PHASE="01-preflight"; source "$SCRIPT_DIR/installers/phases/01-preflight.sh"
    ai_ok "Preflight passed; no changes were made."
    exit 0
fi

ods_prepare_sudo "ODS installer setup"
export ODS_SR_AUTO_INSTALL_PYYAML=1
ods_ensure_python_module yaml python3-pyyaml pyyaml PyYAML
if declare -f sr_load >/dev/null 2>&1; then
    sr_load
fi

#=============================================================================
# Splash
#=============================================================================
show_stranger_boot
[[ "$INTERACTIVE" == "true" ]] && sleep 5

$DRY_RUN && echo -e "${AMB}>>> DRY RUN MODE — I will simulate everything. No changes made. <<<${NC}\n"

#=============================================================================
# Run phases
#=============================================================================
INSTALL_PHASE="01-preflight";    source "$SCRIPT_DIR/installers/phases/01-preflight.sh"
INSTALL_PHASE="02-detection";    source "$SCRIPT_DIR/installers/phases/02-detection.sh"
INSTALL_PHASE="02b-external-services"; source "$SCRIPT_DIR/installers/phases/02b-external-services.sh"
INSTALL_PHASE="03-features";     source "$SCRIPT_DIR/installers/phases/03-features.sh"
INSTALL_PHASE="04-requirements"; source "$SCRIPT_DIR/installers/phases/04-requirements.sh"
INSTALL_PHASE="05-docker";       source "$SCRIPT_DIR/installers/phases/05-docker.sh"
if ! $DRY_RUN; then
    INSTALL_PHASE="model-lifecycle-lock"
    ods_model_lifecycle_lock_acquire "$INSTALL_DIR" "Linux installer model configuration"
fi
INSTALL_PHASE="06-directories";  source "$SCRIPT_DIR/installers/phases/06-directories.sh"
INSTALL_PHASE="07-devtools";     source "$SCRIPT_DIR/installers/phases/07-devtools.sh"
INSTALL_PHASE="08-images";       source "$SCRIPT_DIR/installers/phases/08-images.sh"
INSTALL_PHASE="09-offline";      source "$SCRIPT_DIR/installers/phases/09-offline.sh"
INSTALL_PHASE="10-amd-tuning";   source "$SCRIPT_DIR/installers/phases/10-amd-tuning.sh"
INSTALL_PHASE="11-services";     source "$SCRIPT_DIR/installers/phases/11-services.sh"
ods_model_lifecycle_lock_release
INSTALL_PHASE="12-health";       source "$SCRIPT_DIR/installers/phases/12-health.sh"
# Phase 13 is informational (URLs, shortcuts, preflight). It must never fail
# the install — any error here is cosmetic. Run with set +e to prevent
# stray non-zero exit codes (e.g., a crashing privacy-shield health probe)
# from triggering the cleanup_on_error trap.
INSTALL_PHASE="13-summary"
set +e
source "$SCRIPT_DIR/installers/phases/13-summary.sh"
set -e
