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
    case "${INSTALL_PHASE}" in
        init|01-preflight|02-detection|02b-external-services)
            # These phases check the host and choose a route; they change no
            # ODS files or services, so an existing install is as it was. Say
            # that first, not "Installation failed" (fleet row 30).
            echo -e "${AMB:-}[!] Nothing was changed: the install stopped during its checks (${INSTALL_PHASE}).${NC:-}"
            echo "An existing ODS installation keeps working as it was."
            echo "Fix the problem above, then run the same command again."
            ;;
        *)
            echo -e "${RED:-}[ERROR] Installation failed during phase: ${INSTALL_PHASE}${NC:-}"
            echo "The install did not complete. Partial state may exist at:"
            echo "  ${INSTALL_DIR:-~/ods}"
            echo ""
            echo "Keep this directory and its recovery receipts intact."
            echo "Review the failed phase and log before retrying; some phases require recovery."
            echo "For a fresh install, use the installed ods-uninstall.sh and resolve any"
            echo "cleanup refusal before reinstalling. Do not delete the directory manually:"
            echo "ODS services and protected Pixel state may exist outside it."
            ;;
    esac
    echo ""
    printf '        Log file: %s\n' "${LOG_FILE:-/tmp/ods-install.log}"
    if [[ -n "${WSL_DISTRO_NAME:-}" && "${LOG_FILE:-}" == /* ]]; then
        # Windows setup runs this installer in WSL; its /tmp path means
        # nothing in Windows (fleet row 30). printf keeps the backslashes.
        printf '        From Windows: \\\\wsl$\\%s%s\n' "$WSL_DISTRO_NAME" "${LOG_FILE//\//\\}"
    fi
    echo -e "${AMB:-}Need help? Ask on the ODS Discord: ${ODS_HELP_DISCORD_URL:-https://discord.gg/4ntNp9MAwC}${NC:-}"
    echo -e "${AMB:-}Share the phase above and the end of the log file.${NC:-}"
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
source "$SCRIPT_DIR/installers/lib/amd-runtime.sh"
source "$SCRIPT_DIR/installers/lib/native-llm.sh"
source "$SCRIPT_DIR/installers/lib/lemonade-migration.sh"
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
ENABLE_VOICE="$(ods_installed_service_default "$INSTALL_DIR" whisper "$ODS_EXISTING_INSTALL")"
ENABLE_WORKFLOWS="$(ods_installed_service_default "$INSTALL_DIR" n8n "$ODS_EXISTING_INSTALL")"
ENABLE_RAG="$(ods_installed_service_default "$INSTALL_DIR" qdrant "$ODS_EXISTING_INSTALL")"
ENABLE_RECOMMENDED="$(ods_installed_service_default "$INSTALL_DIR" token-spy "$ODS_EXISTING_INSTALL")"
# Pixel is the core conversational experience on qualified Linux hosts after a separate
# written license agreement is acknowledged. Existing ODS tools remain available.
ENABLE_HERMES="$(ods_installed_service_default "$INSTALL_DIR" hermes "$ODS_EXISTING_INSTALL")"
ENABLE_PIXEL="${ENABLE_PIXEL:-auto}"
PIXEL_EXPLICIT=false
HERMES_EXPLICIT=false
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
# An ODS-managed llama-server outside the stack (the Windows Portal runs
# llama-server.exe while this stack runs in WSL). Set by --native-llm-* only.
NATIVE_LLM_BASE_URL=""
# Keep omission distinct from an explicit direct override until .env is read.
ODS_HOST_LLM_TRANSPORT="${ODS_HOST_LLM_TRANSPORT:-}"
NATIVE_LLM_MODEL=""
# An id from the retired --lemonade-model (GGUF stem or "extra.<file>");
# Phase 02 resolves it to the catalog GGUF. One release only.
NATIVE_LLM_LEGACY_MODEL_ID=""
NATIVE_LLM_CONTEXT_SIZE=""
# Display only: the GPU that runs the native server (e.g. Windows under WSL).
NATIVE_LLM_GPU_NAME=""
NATIVE_LLM_GPU_VRAM_MB=""
NATIVE_LLM_API_KEY_ENV=""
ODS_WINDOWS_SYSTEM_DIRECTORY="${ODS_WINDOWS_SYSTEM_DIRECTORY:-}"
# Retired Lemonade flags, kept parseable for one release (see the shims below).
_legacy_lemonade_flags=()
_legacy_lemonade_external=false
_legacy_lemonade_url=""
_legacy_lemonade_transport=""
_legacy_lemonade_model=""
_legacy_lemonade_context=""
_legacy_lemonade_gpu_name=""
_legacy_lemonade_gpu_vram=""
_legacy_lemonade_api_key=""
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
EXTERNAL_LLM_API_KEY_ENV=""
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
    --native-llm-url U
                      Origin (http://host:port) of an ODS-managed llama-server that runs
                      outside this stack; Windows setup passes it for the Portal
    --native-llm-host-transport direct|model-router
                      Host-agent verification network: direct (default), or this
                      installation's model-router container for Windows/WSL
    --native-llm-model GGUF
                      GGUF file the native llama-server serves (its model id); required
                      with --native-llm-url. Recorded from its ODS catalog entry
    --native-llm-context-size N
                      Context size (from 1024 tokens) the native llama-server loaded
    --native-llm-gpu-name N, --native-llm-gpu-vram-mb MB
                      GPU that runs the native llama-server, shown in the hardware scan
    --native-llm-api-key-env VAR
                      Name of an environment variable holding the native server's API
                      key (never the key itself)
    --windows-system-directory PATH
                      Windows System32 directory supplied by Windows setup
    --use-existing-lemonade, --lemonade-*
                      Retired; mapped to --external-llm-* or --native-llm-* with a notice
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
    --external-llm-key-env VAR
                      Read that key from environment variable VAR instead of a file
                      (Windows setup passes it this way, never on a command line)
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
    --openclaw        Ignored; the legacy OpenClaw extension was removed
    --no-openclaw     Ignored; the legacy OpenClaw extension was removed
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
    $0 --external-llm-url http://localhost:13305 --external-llm-provider openai-compatible
                                 # Use a server you run yourself (Lemonade included)
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
        --native-llm-url)
            [[ -n "${2:-}" && "${2:-}" != --* ]] || { echo "--native-llm-url requires an http://host:port origin" >&2; exit 1; }
            NATIVE_LLM_BASE_URL="$2"; shift 2 ;;
        --native-llm-host-transport)
            case "${2:-}" in direct|model-router) ODS_HOST_LLM_TRANSPORT="$2" ;; *) echo "--native-llm-host-transport requires direct or model-router" >&2; exit 1 ;; esac
            shift 2 ;;
        --native-llm-model)
            [[ -n "${2:-}" && "${2:-}" != --* ]] || { echo "--native-llm-model requires a GGUF file name" >&2; exit 1; }
            NATIVE_LLM_MODEL="$2"; shift 2 ;;
        --native-llm-context-size)
            [[ -n "${2:-}" ]] || { echo "--native-llm-context-size needs a number of tokens" >&2; exit 1; }
            NATIVE_LLM_CONTEXT_SIZE="$2"; shift 2 ;;
        --native-llm-gpu-name) NATIVE_LLM_GPU_NAME="${2:-}"; shift 2 ;;
        --native-llm-gpu-vram-mb)
            [[ -n "${2:-}" ]] || { echo "--native-llm-gpu-vram-mb needs a number of megabytes" >&2; exit 1; }
            NATIVE_LLM_GPU_VRAM_MB="$2"; shift 2 ;;
        --native-llm-api-key-env)
            [[ "${2:-}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || { echo "--native-llm-api-key-env requires an environment variable name" >&2; exit 1; }
            NATIVE_LLM_API_KEY_ENV="$2"; shift 2 ;;
        --windows-system-directory)
            [[ -n "${2:-}" && "${2:-}" != --* ]] || { echo "--windows-system-directory requires a Windows System32 path" >&2; exit 1; }
            ODS_WINDOWS_SYSTEM_DIRECTORY="$2"
            shift 2 ;;
        # Retired Lemonade flags: collected here, mapped after parsing.
        --use-existing-lemonade) _legacy_lemonade_flags+=("$1"); _legacy_lemonade_external=true; shift ;;
        --lemonade-url) _legacy_lemonade_flags+=("$1"); _legacy_lemonade_url="${2:-}"; shift 2 ;;
        --lemonade-host-transport)
            case "${2:-}" in direct|model-router) _legacy_lemonade_transport="$2" ;; *) echo "--lemonade-host-transport requires direct or model-router" >&2; exit 1 ;; esac
            _legacy_lemonade_flags+=("$1"); shift 2 ;;
        --lemonade-api-key) _legacy_lemonade_flags+=("$1"); _legacy_lemonade_api_key="${2:-}"; shift 2 ;;
        --lemonade-model) _legacy_lemonade_flags+=("$1"); _legacy_lemonade_model="${2:-}"; shift 2 ;;
        --lemonade-context-size) _legacy_lemonade_flags+=("$1"); _legacy_lemonade_context="${2:-}"; shift 2 ;;
        --lemonade-gpu-name) _legacy_lemonade_flags+=("$1"); _legacy_lemonade_gpu_name="${2:-}"; shift 2 ;;
        --lemonade-gpu-vram-mb)
            [[ -n "${2:-}" ]] || { echo "--lemonade-gpu-vram-mb needs a number of megabytes" >&2; exit 1; }
            _legacy_lemonade_flags+=("$1"); _legacy_lemonade_gpu_vram="$2"; shift 2 ;;
        --external-llm-url) EXTERNAL_LLM_URL="$2"; shift 2 ;;
        --external-llm-provider) EXTERNAL_LLM_PROVIDER="$2"; shift 2 ;;
        --external-llm-model) EXTERNAL_LLM_MODEL="$2"; shift 2 ;;
        --gateway-only) ODS_GATEWAY_ONLY=true; ENABLE_OPEN_WEBUI=false; WEBUI_EXPLICIT=true; ODS_MODE=local; ODS_MODE_EXPLICIT=true; shift ;;
        --with-webui) ENABLE_OPEN_WEBUI=true; WEBUI_EXPLICIT=true; shift ;;
        --no-webui) ENABLE_OPEN_WEBUI=false; WEBUI_EXPLICIT=true; shift ;;
        --no-gateway-only) ODS_GATEWAY_ONLY=false; ENABLE_OPEN_WEBUI=true; WEBUI_EXPLICIT=true; shift ;;
        --external-llm-key-file) EXTERNAL_LLM_API_KEY_FILE="$2"; EXTERNAL_LLM_API_KEY_DISABLE=false; shift 2 ;;
        --external-llm-key-env)
            [[ "${2:-}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || { echo "--external-llm-key-env requires an environment variable name" >&2; exit 1; }
            EXTERNAL_LLM_API_KEY_ENV="$2"; EXTERNAL_LLM_API_KEY_DISABLE=false; shift 2 ;;
        --no-external-llm-key) EXTERNAL_LLM_API_KEY_FILE=""; EXTERNAL_LLM_API_KEY_DISABLE=true; shift ;;
        --reuse-external-llm) EXTERNAL_LLM_AUTO_REUSE=true; shift ;;
        --no-external-llm) EXTERNAL_LLM_DISABLE=true; shift ;;
        --reselect-model) ODS_RESELECT_MODEL=true; shift ;;
        --voice) ENABLE_VOICE=true; shift ;;
        --no-voice) ENABLE_VOICE=false; shift ;;
        --workflows) ENABLE_WORKFLOWS=true; shift ;;
        --no-workflows) ENABLE_WORKFLOWS=false; shift ;;
        --rag) ENABLE_RAG=true; shift ;;
        --no-rag) ENABLE_RAG=false; shift ;;
        --recommended) ENABLE_RECOMMENDED=true; shift ;;
        --no-recommended) ENABLE_RECOMMENDED=false; shift ;;
        --hermes) ENABLE_HERMES=true; HERMES_EXPLICIT=true; shift ;;
        --no-hermes) ENABLE_HERMES=false; HERMES_EXPLICIT=true; shift ;;
        --pixel) ENABLE_PIXEL=true; PIXEL_EXPLICIT=true; shift ;;
        --no-pixel) ENABLE_PIXEL=false; PIXEL_EXPLICIT=true; shift ;;
        # Kept parseable so existing scripts and automation that still pass
        # these flags keep working after the removal.
        --openclaw|--no-openclaw)
            printf '[WARN] The legacy OpenClaw extension was removed; %s is ignored. Portal (Pixel) and Hermes are the supported agents.\n' "$1" >&2
            shift ;;
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
        # --all enables the Hermes fallback alongside the other optional services.
        # ENABLE_ODS_PROXY is included so magic-link invite URLs
        # (http://auth.<device>.local/magic-link/<token>) actually resolve.
        # Without ods-proxy on host :80, mDNS publishes the hostname but
        # nothing serves it, and a phone clicking the invite gets
        # "site can't be reached." Operators who don't want the LAN-facing
        # surface can set ENABLE_ODS_PROXY=false in .env after install.
        --all) ENABLE_VOICE=true; ENABLE_WORKFLOWS=true; ENABLE_RAG=true; ENABLE_RECOMMENDED=true; ENABLE_HERMES=true; ENABLE_OPENCODE=true; ENABLE_DEVTOOLS=true; ENABLE_COMFYUI=true; ENABLE_APE=true; ENABLE_PERPLEXICA=true; ENABLE_PRIVACY_SHIELD=true; ENABLE_LANGFUSE=true; ENABLE_ODS_PROXY=true; ENABLE_OPEN_WEBUI=true; WEBUI_EXPLICIT=true; shift ;;
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
    # The Extensions Library adds voice and RAG documents without Open WebUI
    # (ODS Talk uses voice directly). Refusing that installed selection made
    # every later rerun and upgrade exit before it started (Strixy,
    # 2026-10-05), so an existing installation keeps it. The ODS proxy routes
    # to Open WebUI and still needs it.
    if $ODS_EXISTING_INSTALL && [[ "$ENABLE_ODS_PROXY" != true ]]; then
        echo "[WARN] Keeping the installed voice and RAG services without Open WebUI. ODS Talk uses voice; documents need Open WebUI (add it from Extensions, or rerun with --with-webui)." >&2
    else
        echo "Voice, RAG documents, and ODS proxy currently require Open WebUI; use --with-webui or leave those services off" >&2
        exit 1
    fi
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

# Retired Lemonade flags (one release). The Windows Portal sent them with
# --lemonade-host-transport model-router: they describe an ODS-managed server
# on Windows, now the host-native llama-server route. Otherwise they named the
# owner's own Lemonade, now the generic OpenAI-compatible external route.
if ((${#_legacy_lemonade_flags[@]} > 0)); then
    if [[ "$_legacy_lemonade_transport" == model-router ]]; then
        printf '[NOTICE] %s are retired; mapping them to --native-llm-url, --native-llm-model, --native-llm-context-size and --native-llm-gpu-* (host-native llama-server).\n' \
            "${_legacy_lemonade_flags[*]}" >&2
        NATIVE_LLM_BASE_URL="${NATIVE_LLM_BASE_URL:-${_legacy_lemonade_url:-}}"
        ODS_HOST_LLM_TRANSPORT="${ODS_HOST_LLM_TRANSPORT:-model-router}"
        # Lemonade named an ODS GGUF "extra.<file>" or by its file stem;
        # llama-server serves the file name. Phase 02 resolves a stem through
        # the catalog and stops when it names no single catalog model.
        if [[ -z "$NATIVE_LLM_MODEL" && "${_legacy_lemonade_model#extra.}" == *.gguf ]]; then
            NATIVE_LLM_MODEL="${_legacy_lemonade_model#extra.}"
        elif [[ -n "$_legacy_lemonade_model" && -z "$NATIVE_LLM_MODEL" ]]; then
            [[ "$_legacy_lemonade_model" =~ ^[A-Za-z0-9][A-Za-z0-9._:+-]{0,255}$ ]] || {
                echo "--lemonade-model is not a model id: $_legacy_lemonade_model" >&2
                exit 1
            }
            NATIVE_LLM_LEGACY_MODEL_ID="$_legacy_lemonade_model"
        fi
        NATIVE_LLM_CONTEXT_SIZE="${NATIVE_LLM_CONTEXT_SIZE:-$_legacy_lemonade_context}"
        NATIVE_LLM_GPU_NAME="${NATIVE_LLM_GPU_NAME:-$_legacy_lemonade_gpu_name}"
        NATIVE_LLM_GPU_VRAM_MB="${NATIVE_LLM_GPU_VRAM_MB:-$_legacy_lemonade_gpu_vram}"
        [[ -z "$_legacy_lemonade_api_key" ]] \
            || echo "[NOTICE] --lemonade-api-key is ignored for the Windows Portal route; pass --native-llm-api-key-env VAR." >&2
    else
        _legacy_lemonade_url="${_legacy_lemonade_url:-http://localhost:13305}"
        _legacy_lemonade_url="${_legacy_lemonade_url%/}"
        _legacy_lemonade_url="${_legacy_lemonade_url%/api/v1}"
        printf '[NOTICE] %s are retired; ODS no longer manages Lemonade. Using your server as a generic OpenAI-compatible endpoint: --external-llm-url %s --external-llm-provider openai-compatible%s. Pass those flags (and --external-llm-key-file PATH for a key) next time.\n' \
            "${_legacy_lemonade_flags[*]}" "$_legacy_lemonade_url" \
            "$([[ -z "$_legacy_lemonade_model" ]] || printf ' --external-llm-model %s' "$_legacy_lemonade_model")" >&2
        EXTERNAL_LLM_URL="${EXTERNAL_LLM_URL:-$_legacy_lemonade_url}"
        EXTERNAL_LLM_PROVIDER=openai-compatible
        EXTERNAL_LLM_MODEL="${EXTERNAL_LLM_MODEL:-$_legacy_lemonade_model}"
        # Phase 06 writes the key to config/litellm/external-upstream.key (0600).
        # It is not exported, so it never reaches a child process environment.
        EXTERNAL_LLM_API_KEY_VALUE="$_legacy_lemonade_api_key"
        [[ -z "$_legacy_lemonade_gpu_name$_legacy_lemonade_gpu_vram$_legacy_lemonade_context" ]] \
            || echo "[NOTICE] --lemonade-gpu-* and --lemonade-context-size do not apply to an external server and are ignored." >&2
        ODS_MODE=local
        ODS_MODE_EXPLICIT=true
    fi
fi
unset _legacy_lemonade_flags _legacy_lemonade_external _legacy_lemonade_url _legacy_lemonade_transport
unset _legacy_lemonade_model _legacy_lemonade_context _legacy_lemonade_gpu_name _legacy_lemonade_gpu_vram
unset _legacy_lemonade_api_key

# ODS_MODE=lemonade was the managed AMD local mode. Accept it for one release.
if [[ "$ODS_MODE" == lemonade ]]; then
    echo "[NOTICE] ODS_MODE=lemonade is retired; using ODS_MODE=local (AMD runs llama.cpp)." >&2
    ODS_MODE=local
fi

# Host-native llama-server route: validate every input before any phase runs.
if [[ -n "$NATIVE_LLM_BASE_URL" ]]; then
    _native_origin="$(ods_native_llm_normalize_origin "$NATIVE_LLM_BASE_URL")" || {
        echo "--native-llm-url must be an http://host:port origin, got: $NATIVE_LLM_BASE_URL" >&2
        exit 1
    }
    NATIVE_LLM_BASE_URL="$_native_origin"
    unset _native_origin
    if [[ -n "$NATIVE_LLM_MODEL" ]] && { [[ "$NATIVE_LLM_MODEL" != *.gguf ]] \
        || [[ "$NATIVE_LLM_MODEL" == */* || "$NATIVE_LLM_MODEL" == *\\* ]] \
        || [[ "$NATIVE_LLM_MODEL" == .* || "$NATIVE_LLM_MODEL" =~ [[:cntrl:]] ]]; }; then
        echo "--native-llm-model must be a GGUF file name, got: $NATIVE_LLM_MODEL" >&2
        exit 1
    fi
    if [[ -z "$NATIVE_LLM_MODEL" && -z "$NATIVE_LLM_LEGACY_MODEL_ID" ]]; then
        echo "--native-llm-url requires --native-llm-model FILE.gguf (the model the native llama-server serves)" >&2
        exit 1
    fi
    # Phase 02 records the model at this context; empty uses the catalog's.
    if [[ -n "$NATIVE_LLM_CONTEXT_SIZE" ]] && { [[ ! "$NATIVE_LLM_CONTEXT_SIZE" =~ ^[1-9][0-9]{3,15}$ ]] \
        || (( NATIVE_LLM_CONTEXT_SIZE < 1024 )); }; then
        echo "--native-llm-context-size must be a whole number of tokens from 1024" >&2
        exit 1
    fi
    if [[ -n "$NATIVE_LLM_API_KEY_ENV" ]]; then
        # The key travels through the environment (WSLENV on Windows), never argv.
        LLAMA_SERVER_API_KEY="${!NATIVE_LLM_API_KEY_ENV:-}"
        # Length checked apart: a regex interval above 255 is not portable.
        [[ "$LLAMA_SERVER_API_KEY" =~ ^[0-9A-Fa-f]+$ ]] \
            && (( ${#LLAMA_SERVER_API_KEY} >= 32 && ${#LLAMA_SERVER_API_KEY} <= 512 )) || {
            echo "--native-llm-api-key-env $NATIVE_LLM_API_KEY_ENV must name a variable holding a hex key of 32 to 512 digits" >&2
            exit 1
        }
    fi
    ODS_MODE=local
    ODS_MODE_EXPLICIT=true
    # LiteLLM holds the native server's key; every other service uses it.
    ENABLE_RECOMMENDED=true
elif [[ -n "$NATIVE_LLM_MODEL$NATIVE_LLM_CONTEXT_SIZE$NATIVE_LLM_API_KEY_ENV" ]]; then
    echo "--native-llm-model, --native-llm-context-size and --native-llm-api-key-env require --native-llm-url" >&2
    exit 1
fi
unset NATIVE_LLM_API_KEY_ENV
if [[ -n "$EXTERNAL_LLM_API_KEY_ENV" ]]; then
    # The key travels through the environment (WSLENV on Windows), never argv.
    # It stays in this unexported variable until phase 06 stores it as the
    # owner-only key file, so no copy is written anywhere else.
    EXTERNAL_LLM_API_KEY_VALUE="${!EXTERNAL_LLM_API_KEY_ENV:-}"
    [[ -n "$EXTERNAL_LLM_API_KEY_VALUE" && ${#EXTERNAL_LLM_API_KEY_VALUE} -le 4096 && "$EXTERNAL_LLM_API_KEY_VALUE" =~ ^[[:graph:]]+$ ]] || {
        echo "--external-llm-key-env $EXTERNAL_LLM_API_KEY_ENV must name a variable holding one API key (printable, no spaces)" >&2
        exit 1
    }
    EXTERNAL_LLM_API_KEY_FILE=""
    unset "$EXTERNAL_LLM_API_KEY_ENV"
fi
unset EXTERNAL_LLM_API_KEY_ENV

# Validate the native GPU VRAM from the flags before any phase can evaluate it
# as Bash arithmetic. Empty retains auto-detection.
if [[ -n "$NATIVE_LLM_GPU_VRAM_MB" ]]; then
    [[ "$NATIVE_LLM_GPU_VRAM_MB" =~ ^[0-9]+$ ]] || {
        echo "--native-llm-gpu-vram-mb must be a nonnegative decimal number of megabytes" >&2
        exit 1
    }
    _native_vram="${NATIVE_LLM_GPU_VRAM_MB#"${NATIVE_LLM_GPU_VRAM_MB%%[!0]*}"}"
    _native_vram="${_native_vram:-0}"
    # Phase 02 adds 512 before converting MiB to GiB; leave room in int64.
    # Compare equal-length decimal strings without overflowing the validator.
    # shellcheck disable=SC2071
    if [[ ${#_native_vram} -gt 19 ||
        ( ${#_native_vram} -eq 19 && "$_native_vram" > 9223372036854775295 ) ]]; then
        echo "--native-llm-gpu-vram-mb exceeds the supported integer range" >&2
        exit 1
    fi
    NATIVE_LLM_GPU_VRAM_MB="$_native_vram"
fi
unset _native_vram

# Help and malformed options exit without creating a log. Every remaining
# path prepares a private diagnostic file before the first logging call.
if ! ods_prepare_install_log "$LOG_FILE"; then
    exit 1
fi

# Argument parsing establishes interactivity. Resolve the presentation once so
# non-interactive/CI/GUI output cannot inherit terminal color from a real TTY.
ods_apply_presentation_mode

# Move a Lemonade-era .env to the llama.cpp runtime before anything reads it:
# Phase 02 preserves the active model only for ODS_MODE=local, and
# validate-env.sh runs on the result. Unchanged when nothing is Lemonade-era.
# The model lifecycle lock keeps a background model switch from writing .env
# at the same time.
if [[ -f "$INSTALL_DIR/.env" ]] && ! $DRY_RUN && [[ "$PREFLIGHT_ONLY" != "true" ]]; then
    ods_model_lifecycle_lock_acquire "$INSTALL_DIR" "Lemonade settings migration" || {
        echo "[ERROR] Could not take the model lifecycle lock to check $INSTALL_DIR/.env for Lemonade-era settings." >&2
        exit 1
    }
    if ! ods_migrate_lemonade_env "$INSTALL_DIR"; then
        echo "[ERROR] Could not migrate the Lemonade-era settings in $INSTALL_DIR/.env; nothing was changed. See $LOG_FILE." >&2
        exit 1
    fi
    ods_model_lifecycle_lock_release
fi

_requested_ods_mode="$ODS_MODE"
ODS_MODE="$(ods_preserve_existing_install_mode "$ODS_MODE" "$ODS_MODE_EXPLICIT" "$INSTALL_DIR/.env")"
if [[ "$ODS_MODE_EXPLICIT" != "true" && "$ODS_MODE" != "$_requested_ods_mode" ]]; then
    log "Existing ODS mode detected; preserving ODS_MODE=$ODS_MODE for this installer rerun"
fi
unset _requested_ods_mode
# A model API connected in Settings was active. This run keeps the install's
# own mode, which leaves that API route paused until the owner reconnects it.
# Pausing it also restores the LLM_API_URL the API replaced, as the agent's
# own disable does; phase 06 otherwise kept http://litellm:4000, and Portal
# chat reached LiteLLM without a key (fleet, laptop). An explicit local or
# hybrid mode pauses it too.
ODS_REMOTE_ROUTE_PAUSED=false
ODS_REMOTE_ROUTE_PREVIOUS_API_URL=""
if [[ "$ODS_MODE" != "cloud" ]] \
    && ODS_REMOTE_ROUTE_PREVIOUS_API_URL="$(ods_remote_route_previous_api_url "$INSTALL_DIR" "$ODS_MODE")"; then
    ODS_REMOTE_ROUTE_PAUSED=true
    log "A model API connected in Settings (Remote model) is active. This run keeps ODS in ${ODS_MODE} mode and pauses the API; select Reconnect in Settings > Remote model afterwards."
fi

# Exported (an empty value included) so the Compose resolver uses this run's
# selection instead of a value left in the installation's .env.
export NATIVE_LLM_BASE_URL ODS_HOST_LLM_TRANSPORT NATIVE_LLM_MODEL NATIVE_LLM_CONTEXT_SIZE
export NATIVE_LLM_GPU_NAME NATIVE_LLM_GPU_VRAM_MB ODS_WINDOWS_SYSTEM_DIRECTORY
[[ -z "${LLAMA_SERVER_API_KEY:-}" ]] || export LLAMA_SERVER_API_KEY

export EXTERNAL_LLM_URL EXTERNAL_LLM_PROVIDER EXTERNAL_LLM_MODEL
export EXTERNAL_LLM_AUTO_REUSE EXTERNAL_LLM_DISABLE ODS_RESELECT_MODEL

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
