#!/bin/bash
# ============================================================================
# ODS Installer — Orchestrator
# ============================================================================
# Unified installer - voice-enabled by default, uses docker-compose.yml
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
    echo -e "\033[0;31m[ERROR] Installation failed during phase: ${INSTALL_PHASE}\033[0m"
    echo -e "\033[0;33m        Log file: ${LOG_FILE:-/tmp/ods-install.log}\033[0m"
    echo ""
    echo "The install did not complete. Partial state may exist at:"
    echo "  ${INSTALL_DIR:-~/ods}"
    echo ""
    echo "To retry, run the installer again. It will resume safely."
    echo "To start fresh, remove the install directory first:"
    echo "  rm -rf ${INSTALL_DIR:-~/ods} && ./install.sh"
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
        echo -e "\033[0;33m[!] Install cancelled by user.\033[0m"
        echo -e "\033[0;32m    Log file: ${LOG_FILE:-/tmp/ods-install.log}\033[0m"
        exit 130
    fi
    LAST_SIGINT=$now
    echo ""
    echo -e "\033[0;33m[!] Press Ctrl+C again within 3 seconds to cancel the install.\033[0m"
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
source "$SCRIPT_DIR/installers/lib/logging.sh"
source "$SCRIPT_DIR/installers/lib/ui.sh"
source "$SCRIPT_DIR/installers/lib/sudo.sh"
source "$SCRIPT_DIR/installers/lib/detection.sh"
source "$SCRIPT_DIR/installers/lib/host-arch.sh"
source "$SCRIPT_DIR/installers/lib/tier-map.sh"
source "$SCRIPT_DIR/installers/lib/docker-images.sh"
source "$SCRIPT_DIR/installers/lib/compose-images.sh"
source "$SCRIPT_DIR/installers/lib/compose-select.sh"
source "$SCRIPT_DIR/installers/lib/compose-failure-report.sh"
source "$SCRIPT_DIR/installers/lib/readiness-summary.sh"
source "$SCRIPT_DIR/installers/lib/packaging.sh"
source "$SCRIPT_DIR/installers/lib/python-runtime.sh"
source "$SCRIPT_DIR/installers/lib/progress.sh"
if [[ -f "$SCRIPT_DIR/lib/service-registry.sh" ]]; then 
    source "$SCRIPT_DIR/lib/service-registry.sh" 
fi

#=============================================================================
# Command Line Args
#=============================================================================
DRY_RUN=false
SKIP_DOCKER=false
FORCE=false
TIER=""
ENABLE_VOICE=true
ENABLE_WORKFLOWS=true
ENABLE_RAG=true
ENABLE_RECOMMENDED=true
# Default agent flipped to Hermes Agent (Nous Research) on 2026-05-12.
# OpenClaw is deprecated and will be removed in the next release; new
# installs no longer enable it by default. Users who explicitly pass
# --openclaw or upgrade an existing install with OpenClaw enabled keep
# it working until the removal release. See docs/MIGRATION-OPENCLAW-TO-HERMES.md.
ENABLE_HERMES=true
ENABLE_OPENCLAW=false
OPENCLAW_EXPLICIT=false
ENABLE_COMFYUI=true
ENABLE_APE=true
ENABLE_PERPLEXICA=true
ENABLE_PRIVACY_SHIELD=true
ENABLE_ODS_PROXY=false
ENABLE_TAILSCALE=false
ENABLE_BRAVE_SEARCH=false
# Langfuse (LLM observability) defaults OFF on all tiers because its
# clickhouse + postgres + minio stack adds ~500MB baseline memory that is
# nontrivial even on Tier 3+ systems. Users opt in via --langfuse, --all,
# the Custom menu, or post-install `ods enable langfuse`.
ENABLE_LANGFUSE=false
INTERACTIVE=true
ODS_MODE="${ODS_MODE:-local}"
LEMONADE_EXTERNAL="${LEMONADE_EXTERNAL:-false}"
LEMONADE_BASE_URL="${LEMONADE_BASE_URL:-}"
LEMONADE_API_KEY="${LEMONADE_API_KEY:-}"
OFFLINE_MODE=false   # M1 integration: fully air-gapped operation
NO_BOOTSTRAP=false  # Skip bootstrap fast-start, download full model in foreground
BIND_ADDRESS_EXPLICIT=false
[[ -n "${BIND_ADDRESS:-}" ]] && BIND_ADDRESS_EXPLICIT=true
BIND_ADDRESS="${BIND_ADDRESS:-127.0.0.1}"
SUMMARY_JSON_FILE="${SUMMARY_JSON_FILE:-}"

usage() {
    cat << EOF
ODS Installer v${VERSION}

Usage: $0 [OPTIONS]

Options:
    --dry-run         Show what would be done without making changes
    --skip-docker     Skip Docker installation (assume already installed)
    --force           Overwrite existing installation
    --tier N          Force specific tier (1-4) instead of auto-detect
    --cloud           Cloud mode: skip GPU detection, use LiteLLM + cloud APIs
    --use-existing-lemonade
                      Use an already-running Lemonade SDK server as the AMD LLM runtime
    --lemonade-url U  Lemonade server URL for --use-existing-lemonade
                      (auto-detects localhost:13305, then localhost:8000 when omitted)
    --lemonade-api-key K
                      API key LiteLLM should send to the existing Lemonade server
    --voice           Enable voice services (Whisper + Kokoro)
    --no-voice        Disable voice services
    --workflows       Enable n8n workflow automation
    --no-workflows    Disable n8n workflow automation
    --rag             Enable RAG with Qdrant vector database
    --no-rag          Disable RAG / Qdrant
    --recommended     Enable LiteLLM + SearXNG + Token Spy support services
    --no-recommended  Disable recommended support services
    --hermes          Enable Hermes Agent (default; new default agent as of 2026-05-12)
    --no-hermes       Disable Hermes Agent
    --openclaw        Enable OpenClaw (DEPRECATED — see docs/MIGRATION-OPENCLAW-TO-HERMES.md)
    --no-openclaw     Disable OpenClaw
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
        --skip-docker) SKIP_DOCKER=true; shift ;;
        --force) FORCE=true; shift ;;
        --tier) TIER="$2"; shift 2 ;;
        --cloud) ODS_MODE="cloud"; shift ;;
        --use-existing-lemonade) LEMONADE_EXTERNAL=true; ODS_MODE="lemonade"; shift ;;
        --lemonade-url) LEMONADE_EXTERNAL=true; ODS_MODE="lemonade"; LEMONADE_BASE_URL="$2"; shift 2 ;;
        --lemonade-api-key) LEMONADE_API_KEY="$2"; shift 2 ;;
        --voice) ENABLE_VOICE=true; shift ;;
        --no-voice) ENABLE_VOICE=false; shift ;;
        --workflows) ENABLE_WORKFLOWS=true; shift ;;
        --no-workflows) ENABLE_WORKFLOWS=false; shift ;;
        --rag) ENABLE_RAG=true; shift ;;
        --no-rag) ENABLE_RAG=false; shift ;;
        --recommended) ENABLE_RECOMMENDED=true; shift ;;
        --no-recommended) ENABLE_RECOMMENDED=false; shift ;;
        --hermes) ENABLE_HERMES=true; shift ;;
        --no-hermes) ENABLE_HERMES=false; shift ;;
        --openclaw) ENABLE_OPENCLAW=true; OPENCLAW_EXPLICIT=true; shift ;;
        --no-openclaw) ENABLE_OPENCLAW=false; OPENCLAW_EXPLICIT=true; shift ;;
        --comfyui) ENABLE_COMFYUI=true; shift ;;
        --no-comfyui) ENABLE_COMFYUI=false; shift ;;
        --odsforge) warn "ODSForge has been removed; ignoring --odsforge"; shift ;;
        --no-odsforge) warn "ODSForge has been removed; ignoring --no-odsforge"; shift ;;
        --langfuse) ENABLE_LANGFUSE=true; shift ;;
        # NOTE: with --all, --no-langfuse must appear AFTER --all on the command
        # line (flag processing is case-loop ordered, matching comfyui).
        --no-langfuse) ENABLE_LANGFUSE=false; shift ;;
        # --all enables Hermes (the new default agent) but NOT OpenClaw —
        # the deprecated agent is opt-in via --openclaw for the deprecation
        # release. Will be dropped entirely in the removal release.
        # ENABLE_ODS_PROXY is included so magic-link invite URLs
        # (http://auth.<device>.local/magic-link/<token>) actually resolve.
        # Without ods-proxy on host :80, mDNS publishes the hostname but
        # nothing serves it, and a phone clicking the invite gets
        # "site can't be reached." Operators who don't want the LAN-facing
        # surface can set ENABLE_ODS_PROXY=false in .env after install.
        --all) ENABLE_VOICE=true; ENABLE_WORKFLOWS=true; ENABLE_RAG=true; ENABLE_RECOMMENDED=true; ENABLE_HERMES=true; ENABLE_OPENCLAW=false; ENABLE_COMFYUI=true; ENABLE_APE=true; ENABLE_PERPLEXICA=true; ENABLE_PRIVACY_SHIELD=true; ENABLE_LANGFUSE=true; ENABLE_ODS_PROXY=true; shift ;;
        --non-interactive) INTERACTIVE=false; shift ;;
        --offline) OFFLINE_MODE=true; shift ;;
        --lan) BIND_ADDRESS="0.0.0.0"; BIND_ADDRESS_EXPLICIT=true; shift ;;
        --no-bootstrap) NO_BOOTSTRAP=true; shift ;;
        --summary-json) SUMMARY_JSON_FILE="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) error "Unknown option: $1" ;;
    esac
done

if [[ "${LEMONADE_EXTERNAL,,}" == "true" ]]; then
    ODS_MODE="lemonade"
    ENABLE_RECOMMENDED=true
    export LEMONADE_EXTERNAL LEMONADE_BASE_URL LEMONADE_API_KEY
fi

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
INSTALL_PHASE="03-features";     source "$SCRIPT_DIR/installers/phases/03-features.sh"
INSTALL_PHASE="04-requirements"; source "$SCRIPT_DIR/installers/phases/04-requirements.sh"
INSTALL_PHASE="05-docker";       source "$SCRIPT_DIR/installers/phases/05-docker.sh"
INSTALL_PHASE="06-directories";  source "$SCRIPT_DIR/installers/phases/06-directories.sh"
INSTALL_PHASE="07-devtools";     source "$SCRIPT_DIR/installers/phases/07-devtools.sh"
INSTALL_PHASE="08-images";       source "$SCRIPT_DIR/installers/phases/08-images.sh"
INSTALL_PHASE="09-offline";      source "$SCRIPT_DIR/installers/phases/09-offline.sh"
INSTALL_PHASE="10-amd-tuning";   source "$SCRIPT_DIR/installers/phases/10-amd-tuning.sh"
INSTALL_PHASE="11-services";     source "$SCRIPT_DIR/installers/phases/11-services.sh"
INSTALL_PHASE="12-health";       source "$SCRIPT_DIR/installers/phases/12-health.sh"
# Phase 13 is informational (URLs, shortcuts, preflight). It must never fail
# the install — any error here is cosmetic. Run with set +e to prevent
# stray non-zero exit codes (e.g., a crashing privacy-shield health probe)
# from triggering the cleanup_on_error trap.
INSTALL_PHASE="13-summary"
set +e
source "$SCRIPT_DIR/installers/phases/13-summary.sh"
set -e
