#!/bin/bash
#=============================================================================
# pre-download.sh — Download Models Before Installation
#
# Part of ODS — Phase 3
#
# Downloads models ahead of time so install.sh can skip the download step.
# Useful for slow/metered connections or offline installs.
#
# Usage:
#   ./pre-download.sh                    # Auto-detect tier
#   ./pre-download.sh --tier edge        # Download edge tier models
#   ./pre-download.sh --tier pro         # Download pro tier models
#   ./pre-download.sh --list             # List available models
#   ./pre-download.sh --verify           # Verify cached models
#
# Download targets (same paths the installer uses):
#   LLM GGUF: <install-dir>/data/models/   (install-dir resolves like install.sh, default ~/ods)
#   Whisper:  <install-dir>/data/whisper/  (mounted as the speaches HF hub cache)
#=============================================================================

# Require Bash 4+ (associative arrays used for tier → model mapping)
if (( BASH_VERSINFO[0] < 4 )); then
    echo "ERROR: $(basename "$0") requires Bash 4.0+ (you have $BASH_VERSION)" >&2
    echo "  macOS ships Bash 3.2 due to licensing. Install a modern version:" >&2
    echo "    brew install bash" >&2
    exit 1
fi

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Resolve the same install dir the installer resolves. Phase 06 copies the
# source tree but excludes data/, so downloading under this checkout would be
# lost on a fresh install — the GGUF must land in $INSTALL_DIR/data/models.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$HOME/ods}"
if [[ -f "$SCRIPT_DIR/installers/lib/path-utils.sh" ]]; then
    . "$SCRIPT_DIR/installers/lib/path-utils.sh"
    INSTALL_DIR="$(resolve_install_dir)"
fi
MODEL_DIR="$INSTALL_DIR/data/models"
WHISPER_CACHE_DIR="$INSTALL_DIR/data/whisper"

# tier-map.sh substitutes the aarch64 build when HOST_ARCH=arm64.
HOST_ARCH="${HOST_ARCH:-$(uname -m)}"
[[ "$HOST_ARCH" == "aarch64" ]] && HOST_ARCH="arm64"

# installers/lib/tier-map.sh is the installer's single source of truth for
# which GGUF file each tier actually serves (model + URL + sha256 + size).
# Sourcing it keeps this script in sync as model assignments change.
if [[ -f "$SCRIPT_DIR/installers/lib/tier-map.sh" ]]; then
    . "$SCRIPT_DIR/installers/lib/tier-map.sh"
    HAVE_TIER_MAP=true
else
    HAVE_TIER_MAP=false
fi

# Legacy tier names kept for backwards compatibility with older docs/scripts,
# mapped by model size to the installer's tier ids (nano≈2B, edge≈4-9B,
# pro≈26-30B, cluster≈30-31B — the largest available).
declare -A TIER_ALIASES=(
    [nano]=0
    [edge]=2
    [pro]=3
    [cluster]=4
)
VALID_TIERS="0 1 2 3 4 T0 T1 T2 T3 T4 CLOUD NV_ULTRA SH_LARGE SH_COMPACT ARC ARC_LITE nano edge pro cluster"

# Optional components. The Kokoro TTS container has no host-mounted model
# cache, so only the Whisper STT model can be pre-warmed from the host.
stt_model() {
    # Mirrors installers/phases/06-directories.sh: cuda-capable hosts get the
    # ct2 build, everything else gets the portable base model.
    if command -v nvidia-smi &>/dev/null; then
        echo "deepdml/faster-whisper-large-v3-turbo-ct2"
    else
        echo "Systran/faster-whisper-base"
    fi
}

#=============================================================================
# Utility Functions
#=============================================================================

print_banner() {
    echo -e "${CYAN}"
    cat << 'EOF'
    ╔═══════════════════════════════════════════════════════════╗
    ║         ODS — Model Pre-Download                 ║
    ║                                                           ║
    ║  Download models before installation for faster setup.    ║
    ╚═══════════════════════════════════════════════════════════╝
EOF
    echo -e "${NC}"
}

log() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }

check_dependencies() {
    if ! command -v curl &>/dev/null; then
        error "Missing dependency: curl"
        echo "Please install it first."
        exit 1
    fi
}

# Python + huggingface_hub are only needed to pre-warm the Whisper STT cache;
# the LLM GGUF downloads via curl, so defer this check to the voice path.
ensure_hf_python() {
    local pycmd=""
    if command -v python3 &>/dev/null; then
        pycmd="python3"
    elif command -v python &>/dev/null; then
        pycmd="python"
    else
        error "Missing dependency: python (or python3) — required for --with-voice"
        return 1
    fi

    local pipcmd=""
    if command -v pip3 &>/dev/null; then
        pipcmd="pip3"
    elif command -v pip &>/dev/null; then
        pipcmd="pip"
    fi

    if ! "$pycmd" -c "import huggingface_hub" 2>/dev/null; then
        if [[ -z "$pipcmd" ]]; then
            error "huggingface_hub not installed and no pip available — required for --with-voice"
            return 1
        fi
        log "Installing huggingface_hub..."
        "$pipcmd" install -q huggingface_hub
    fi

    export ODS_PYTHON_CMD="$pycmd"
}

#=============================================================================
# Hardware Detection (simplified from install-core.sh)
#=============================================================================

detect_vram_gb() {
    if command -v nvidia-smi &>/dev/null; then
        nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | sed -n '1p' | awk '{print int($1/1024)}'
    else
        echo "0"
    fi
}

detect_ram_gb() {
    if [[ -f /proc/meminfo ]]; then
        awk '/MemTotal/ {printf "%.0f", $2/1024/1024}' /proc/meminfo
    elif command -v sysctl &>/dev/null; then
        sysctl -n hw.memsize 2>/dev/null | awk '{printf "%.0f", $1/1024/1024/1024}'
    else
        echo "0"
    fi
}

recommend_tier() {
    # Mirrors the VRAM thresholds in installers/phases/02-detection.sh.
    local vram ram
    vram=$(detect_vram_gb)
    ram=$(detect_ram_gb)

    if [[ $vram -ge 40 ]]; then
        echo "4"
    elif [[ $vram -ge 20 ]] || [[ $ram -ge 96 ]]; then
        echo "3"
    elif [[ $vram -ge 12 ]] || [[ $ram -ge 48 ]]; then
        echo "2"
    elif [[ $vram -lt 4 ]] && [[ $ram -lt 12 ]]; then
        echo "0"
    else
        echo "1"
    fi
}

#=============================================================================
# Model Download
#=============================================================================

# Normalize a user-supplied tier to the installer tier id used by tier-map.sh.
# Accepts the installer's ids plus the legacy names from older versions of
# this script (nano/edge/pro/cluster → 1/2/3/4).
normalize_tier() {
    local tier="$1"
    if [[ -n "${TIER_ALIASES[$tier]:-}" ]]; then
        tier="${TIER_ALIASES[$tier]}"
    fi
    # ods model swap accepts T0-T4; tier-map wants the bare number.
    [[ "$tier" =~ ^T([0-9]+)$ ]] && tier="${BASH_REMATCH[1]}"
    [[ "$tier" == "SH" ]] && tier="SH_COMPACT"
    echo "$tier"
}

is_valid_tier() {
    local t
    for t in $VALID_TIERS; do
        [[ "$1" == "$t" ]] && return 0
    done
    return 1
}

llm_sha_ok() {
    local file="$1"
    [[ -z "${GGUF_SHA256:-}" ]] && return 0
    command -v sha256sum &>/dev/null || return 0
    [[ "$(sha256sum "$file" | awk '{print $1}')" == "$GGUF_SHA256" ]]
}

download_llm() {
    local tier="$1"

    if ! is_valid_tier "$tier"; then
        error "Unknown tier: $tier"
        echo "Available tiers: 0-4, CLOUD, NV_ULTRA, SH_LARGE, SH_COMPACT, ARC, ARC_LITE (aliases: nano, edge, pro, cluster)"
        return 1
    fi
    if [[ "$HAVE_TIER_MAP" != "true" ]]; then
        error "installers/lib/tier-map.sh not found next to this script — cannot resolve tier $tier"
        return 1
    fi

    # tier-map.sh reads these globals; resolve_tier_config only sees valid
    # tiers here, so its error() path (which exits under the installer) is
    # unreachable.
    # shellcheck disable=SC2034  # TIER is read by tier-map.sh after sourcing
    TIER="$(normalize_tier "$tier")"
    if ! resolve_tier_config; then
        error "Could not resolve tier $tier"
        return 1
    fi

    if [[ -z "$GGUF_FILE" || -z "$GGUF_URL" ]]; then
        log "Tier $tier ($TIER_NAME) uses a remote/cloud model — nothing to pre-download."
        return 0
    fi

    mkdir -p "$MODEL_DIR"
    local dest="$MODEL_DIR/$GGUF_FILE"

    if [[ -f "$dest" ]] && llm_sha_ok "$dest"; then
        success "LLM already present: $GGUF_FILE"
        return 0
    fi

    log "Downloading LLM ($TIER_NAME): $GGUF_FILE"
    if curl -fL --retry 3 -C - -o "$dest.part" "$GGUF_URL"; then
        mv "$dest.part" "$dest"
        if llm_sha_ok "$dest"; then
            success "Downloaded LLM: $GGUF_FILE"
            return 0
        fi
        error "Checksum mismatch for $GGUF_FILE — removed corrupt download"
        rm -f "$dest"
        return 1
    fi
    rm -f "$dest.part"
    error "Failed to download LLM: $GGUF_FILE"
    return 1
}

download_stt() {
    local model
    model="$(stt_model)"

    ensure_hf_python || return 1
    mkdir -p "$WHISPER_CACHE_DIR"

    log "Downloading STT (Whisper): $model"
    # data/whisper is bind-mounted into the speaches container as its HF hub
    # cache, so snapshot_download's models--org--name layout pre-warms it.
    WHISPER_CACHE_DIR="$WHISPER_CACHE_DIR" STT_MODEL="$model" "${ODS_PYTHON_CMD:-python3}" << 'EOF'
import os, sys
from huggingface_hub import snapshot_download

try:
    path = snapshot_download(
        repo_id=os.environ["STT_MODEL"],
        cache_dir=os.environ["WHISPER_CACHE_DIR"],
        resume_download=True,
    )
    print(f"Downloaded to: {path}")
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
}

verify_llm() {
    local tier="$1"

    [[ "$HAVE_TIER_MAP" == "true" ]] || return 1
    TIER="$(normalize_tier "$tier")"
    resolve_tier_config 2>/dev/null || return 1
    [[ -z "$GGUF_FILE" ]] && return 0   # cloud tier: nothing to cache
    local dest="$MODEL_DIR/$GGUF_FILE"
    if [[ -f "$dest" ]] && llm_sha_ok "$dest"; then
        echo "  ✓ $tier: $GGUF_FILE"
        return 0
    fi
    return 1
}

verify_stt() {
    local model
    model="$(stt_model)"
    local dir_name="models--${model//\//--}"
    if [[ -d "$WHISPER_CACHE_DIR/$dir_name" ]]; then
        echo "  ✓ STT (Whisper): $model"
        return 0
    fi
    return 1
}

#=============================================================================
# Main Functions
#=============================================================================

list_models() {
    echo -e "\n${BOLD}Available Models by Tier:${NC}\n"

    echo -e "${CYAN}Tier     │ Model (GGUF)                       │ Approx size${NC}"
    echo "─────────┼────────────────────────────────────┼───────────"

    for tier in 0 1 2 3 4; do
        local model="(unavailable)" size="?"
        if [[ "$HAVE_TIER_MAP" == "true" ]]; then
            TIER="$tier"
            resolve_tier_config 2>/dev/null && model="${GGUF_FILE:-(cloud/remote)}" \
                && size="$(( ${LLM_MODEL_SIZE_MB:-0} / 1024 ))GB"
        fi
        printf "%-8s │ %-34s │ ~%s\n" "$tier" "$model" "$size"
    done
    echo "(legacy aliases: nano=0, edge=2, pro=3, cluster=4)"

    echo ""
    echo -e "${BOLD}Optional Components:${NC}"
    echo "  STT (Whisper): $(stt_model)"
    echo "  TTS (Kokoro):  downloaded by the container on first use (no host cache)"
}

verify_cache() {
    echo -e "\n${BOLD}Verifying cached models in $INSTALL_DIR/data ...${NC}\n"

    local found=0
    local missing=0

    for tier in 0 1 2 3 4; do
        if verify_llm "$tier"; then
            ((found++)) || true
        else
            echo -e "  ${RED}✗${NC} tier $tier: Not cached"
            ((missing++)) || true
        fi
    done

    # Check optional
    echo ""
    if verify_stt; then
        ((found++)) || true
    else
        echo -e "  ${YELLOW}○${NC} STT (Whisper): Not cached (optional)"
    fi

    echo ""
    echo "Found: $found cached | Missing: $missing required"
}

download_tier() {
    local tier="$1"
    local include_voice="${2:-false}"

    if ! is_valid_tier "$tier"; then
        error "Unknown tier: $tier"
        echo "Available tiers: 0-4, CLOUD, NV_ULTRA, SH_LARGE, SH_COMPACT, ARC, ARC_LITE (aliases: nano, edge, pro, cluster)"
        exit 1
    fi

    # Resolve once for the estimate + prompt; download_llm resolves again.
    local size_gb=0
    if [[ "$HAVE_TIER_MAP" == "true" ]]; then
        # shellcheck disable=SC2034  # TIER is read by tier-map.sh after sourcing
        TIER="$(normalize_tier "$tier")"
        resolve_tier_config 2>/dev/null && size_gb=$(( ${LLM_MODEL_SIZE_MB:-0} / 1024 ))
    fi

    echo -e "\n${BOLD}Downloading ${tier} tier models${NC}"
    echo -e "LLM: ${GGUF_FILE:-remote/cloud model} (~${size_gb}GB) → $MODEL_DIR"
    echo ""

    # Estimate time
    local est_minutes=$((size_gb * 2))  # ~0.5GB/min on average connection
    warn "Estimated download time: ${est_minutes}-$((est_minutes * 2)) minutes (depends on connection)"
    echo ""
    
    # `--tier X` is a documented non-interactive invocation but still reaches
    # this confirmation. Under `set -euo pipefail` a closed stdin (CI, a pipe,
    # nohup) makes read return non-zero and abort the whole script before the
    # download; tolerate EOF and proceed, since the tier was chosen explicitly
    # and the prompt defaults to yes ([Y/n]).
    read -p "Continue? [Y/n] " -n 1 -r || REPLY=""
    echo
    if [[ $REPLY =~ ^[Nn]$ ]]; then
        echo "Cancelled."
        exit 0
    fi
    
    # Download LLM
    download_llm "$tier" || exit 1

    # Download voice components if requested. Kokoro TTS has no host-mounted
    # cache, so only the Whisper STT model can be pre-warmed.
    if [[ "$include_voice" == "true" ]]; then
        echo ""
        download_stt || warn "STT download failed (optional)"
        log "TTS (Kokoro) has no host cache — it downloads inside the container on first use."
    fi
    
    echo ""
    success "Pre-download complete!"
    echo ""
    echo "You can now run install.sh — it will use the cached models."
    echo "  ./install.sh"
}

interactive_menu() {
    print_banner
    check_dependencies
    
    local recommended vram ram
    recommended=$(recommend_tier)
    vram=$(detect_vram_gb)
    ram=$(detect_ram_gb)
    
    echo -e "${BOLD}Detected Hardware:${NC}"
    echo "  RAM:  ${ram}GB"
    echo "  VRAM: ${vram}GB (GPU)"
    echo ""
    echo -e "  ${GREEN}Recommended tier: ${BOLD}$recommended${NC}"
    echo ""
    
    list_models
    
    echo ""
    read -p "Select tier to download [1-4 or nano/edge/pro/cluster] ($recommended): " tier_choice
    tier_choice="${tier_choice:-$recommended}"

    echo ""
    read -p "Also download voice components (STT)? [y/N] " -n 1 -r voice_choice
    echo
    
    local include_voice="false"
    [[ $voice_choice =~ ^[Yy]$ ]] && include_voice="true"
    
    download_tier "$tier_choice" "$include_voice"
}

#=============================================================================
# CLI Argument Parsing
#=============================================================================

show_help() {
    cat << EOF
ODS Model Pre-Download

Usage: $0 [options]

Options:
  --tier TIER      Download models for a tier: 0-4, CLOUD, NV_ULTRA, SH_LARGE,
                   SH_COMPACT, ARC, ARC_LITE (aliases: nano, edge, pro, cluster)
  --with-voice     Also download the STT (Whisper) model
  --list           List available models and sizes
  --verify         Check which models are already cached
  --help           Show this help message

Examples:
  $0                      # Interactive mode (auto-detect tier)
  $0 --tier 3             # Download tier-3 model (pro)
  $0 --tier edge --with-voice  # Download edge tier + voice model
  $0 --verify             # Check cache status
EOF
}

main() {
    local tier=""
    local include_voice="false"
    local action="interactive"
    
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --tier)
                tier="$2"
                action="download"
                shift 2
                ;;
            --with-voice)
                include_voice="true"
                shift
                ;;
            --list)
                action="list"
                shift
                ;;
            --verify)
                action="verify"
                shift
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                error "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done
    
    case "$action" in
        interactive)
            interactive_menu
            ;;
        download)
            print_banner
            check_dependencies
            download_tier "$tier" "$include_voice"
            ;;
        list)
            print_banner
            list_models
            ;;
        verify)
            print_banner
            check_dependencies
            verify_cache
            ;;
    esac
}

main "$@"
