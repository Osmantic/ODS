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
# Cache location: ~/.cache/huggingface/hub/
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

# Model definitions by tier
declare -A TIER_MODELS
TIER_MODELS[nano]="Qwen/Qwen2.5-1.5B-Instruct"
TIER_MODELS[edge]="Qwen/Qwen2.5-7B-Instruct"
TIER_MODELS[pro]="Qwen/Qwen2.5-32B-Instruct-AWQ"
TIER_MODELS[cluster]="Qwen/Qwen2.5-72B-Instruct-AWQ"

# Approximate sizes (for progress estimates)
declare -A MODEL_SIZES_GB
MODEL_SIZES_GB[nano]="3"
MODEL_SIZES_GB[edge]="14"
MODEL_SIZES_GB[pro]="18"
MODEL_SIZES_GB[cluster]="40"

# Optional components
STT_MODEL="Systran/faster-whisper-large-v3"
TTS_MODEL="hexgrad/Kokoro-82M"

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
    local missing=()

    local pycmd="python3"
    if command -v python3 &>/dev/null && python3 -c "import sys; sys.exit(0)" &>/dev/null; then
        pycmd="python3"
    elif command -v python &>/dev/null && python -c "import sys; sys.exit(0)" &>/dev/null; then
        pycmd="python"
    else
        missing+=("python (or python3)")
    fi

    local pipcmd=""
    if command -v pip3 &>/dev/null; then
        pipcmd="pip3"
    elif command -v pip &>/dev/null; then
        pipcmd="pip"
    else
        missing+=("pip")
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        error "Missing dependencies: ${missing[*]}"
        echo "Please install them first."
        exit 1
    fi

    # Ensure huggingface_hub is installed
    if ! "$pycmd" -c "import huggingface_hub" 2>/dev/null; then
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
    local vram ram
    vram=$(detect_vram_gb)
    ram=$(detect_ram_gb)
    
    if [[ $vram -ge 40 ]]; then
        echo "cluster"
    elif [[ $vram -ge 20 ]]; then
        echo "pro"
    elif [[ $vram -ge 6 ]] || [[ $ram -ge 16 ]]; then
        echo "edge"
    else
        echo "nano"
    fi
}

#=============================================================================
# Model Download
#=============================================================================

download_model() {
    local model="$1"
    local label="$2"
    
    log "Downloading $label: $model"
    
    "${ODS_PYTHON_CMD:-python3}" << EOF
from huggingface_hub import snapshot_download
import sys

try:
    path = snapshot_download(
        repo_id="$model",
        resume_download=True,
        local_files_only=False
    )
    print(f"Downloaded to: {path}")
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
    
    if [[ $? -eq 0 ]]; then
        success "Downloaded $label"
        return 0
    else
        error "Failed to download $label"
        return 1
    fi
}

verify_model() {
    local model="$1"
    
    "${ODS_PYTHON_CMD:-python3}" << EOF
from huggingface_hub import try_to_load_from_cache, get_hf_file_metadata
import sys

# Check if model is cached
try:
    from huggingface_hub import snapshot_download
    path = snapshot_download(
        repo_id="$model",
        local_files_only=True
    )
    print(f"✓ Cached: {path}")
except Exception:
    print(f"✗ Not cached: $model")
    sys.exit(1)
EOF
}

#=============================================================================
# Main Functions
#=============================================================================

list_models() {
    echo -e "\n${BOLD}Available Models by Tier:${NC}\n"
    
    echo -e "${CYAN}Tier     │ Model                              │ Size${NC}"
    echo "─────────┼────────────────────────────────────┼──────"
    
    for tier in nano edge pro cluster; do
        local model="${TIER_MODELS[$tier]}"
        local size="${MODEL_SIZES_GB[$tier]}"
        printf "%-8s │ %-34s │ ~%sGB\n" "$tier" "$model" "$size"
    done
    
    echo ""
    echo -e "${BOLD}Optional Components:${NC}"
    echo "  STT (Whisper): $STT_MODEL (~3GB)"
    echo "  TTS (Kokoro):  $TTS_MODEL (~0.2GB)"
}

verify_cache() {
    echo -e "\n${BOLD}Verifying cached models...${NC}\n"
    
    local found=0
    local missing=0
    
    for tier in nano edge pro cluster; do
        local tier_model="${TIER_MODELS[$tier]}"
        if verify_model "$tier_model" 2>/dev/null; then
            ((found++)) || true
        else
            echo -e "  ${RED}✗${NC} $tier: Not cached"
            ((missing++)) || true
        fi
    done

    # Check optional
    echo ""
    if verify_model "$STT_MODEL" 2>/dev/null; then
        ((found++)) || true
    else
        echo -e "  ${YELLOW}○${NC} STT (Whisper): Not cached (optional)"
    fi

    if verify_model "$TTS_MODEL" 2>/dev/null; then
        ((found++)) || true
    else
        echo -e "  ${YELLOW}○${NC} TTS (Kokoro): Not cached (optional)"
    fi
    
    echo ""
    echo "Found: $found cached | Missing: $missing required"
}

download_tier() {
    local tier="$1"
    local include_voice="${2:-false}"
    
    if [[ -z "${TIER_MODELS[$tier]:-}" ]]; then
        error "Unknown tier: $tier"
        echo "Available tiers: nano, edge, pro, cluster"
        exit 1
    fi
    
    local model="${TIER_MODELS[$tier]}"
    local size="${MODEL_SIZES_GB[$tier]}"
    
    echo -e "\n${BOLD}Downloading ${tier} tier models${NC}"
    echo -e "LLM: $model (~${size}GB)"
    echo ""
    
    # Estimate time
    local est_minutes
    est_minutes=$((size * 2))  # ~0.5GB/min on average connection
    warn "Estimated download time: ${est_minutes}-$((est_minutes * 2)) minutes (depends on connection)"
    echo ""
    
    read -p "Continue? [Y/n] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Nn]$ ]]; then
        echo "Cancelled."
        exit 0
    fi
    
    # Download LLM
    download_model "$model" "LLM ($tier tier)" || exit 1
    
    # Download voice components if requested
    if [[ "$include_voice" == "true" ]]; then
        echo ""
        download_model "$STT_MODEL" "STT (Whisper)" || warn "STT download failed (optional)"
        download_model "$TTS_MODEL" "TTS (Kokoro)" || warn "TTS download failed (optional)"
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
    read -p "Select tier to download [nano/edge/pro/cluster] ($recommended): " tier_choice
    tier_choice="${tier_choice:-$recommended}"
    
    echo ""
    read -p "Also download voice components (STT/TTS)? [y/N] " -n 1 -r voice_choice
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
  --tier TIER      Download models for specific tier (nano/edge/pro/cluster)
  --with-voice     Also download STT and TTS models
  --list           List available models and sizes
  --verify         Check which models are already cached
  --help           Show this help message

Examples:
  $0                      # Interactive mode (auto-detect tier)
  $0 --tier pro           # Download pro tier models
  $0 --tier edge --with-voice  # Download edge tier + voice models
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
