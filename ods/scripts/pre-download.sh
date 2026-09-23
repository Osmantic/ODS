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

    if [[ ${#missing[@]} -gt 0 ]]; then
        error "Missing dependencies: ${missing[*]}"
        echo "Please install them first."
        exit 1
    fi

    # Ensure huggingface_hub is installed
    if ! "$pycmd" -c "import huggingface_hub" 2>/dev/null; then
        if ! "$pycmd" -m pip --version >/dev/null 2>&1; then
            error "pip is required for the selected Python: $pycmd"
            return 1
        fi
        local dependency_lock
        dependency_lock="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/installers/python-deps/host-agent.txt"
        [[ -f "$dependency_lock" ]] || {
            error "Reviewed Python dependency lock is missing: $dependency_lock"
            return 1
        }
        log "Installing huggingface_hub..."
        "$pycmd" -m pip install -q --require-hashes --only-binary=:all: -r "$dependency_lock" || return 1
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
    error "Legacy snapshot downloads are blocked: no exact file, revision, SHA-256 and reviewed terms record exists for this selection."
    error "Use the current ODS installer or dashboard Model Library to review and download an exact catalog GGUF."
    return 1
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
    # These historical Qwen2.5/AWQ/voice snapshots are not catalog GGUF
    # identities. Neither a tier choice, EOF nor a global yes flag is consent.
    download_model
}

interactive_menu() {
    print_banner
    
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
  --tier TIER      Legacy snapshot download (blocked; use the installer/model library)
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
