#!/bin/bash
#=============================================================================
# pre-download.sh — Download Models Before Installation
#
# Part of Dream Server — Phase 3
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

# Model definitions by tier — GGUF files from model-library.json
# These match the Qwen profile defaults in installers/lib/tier-map.sh
declare -A TIER_MODELS
TIER_MODELS[nano]="qwen3.5-2b-q4"
TIER_MODELS[edge]="qwen3.5-9b-q4"
TIER_MODELS[pro]="qwen3.5-27b-q4"
TIER_MODELS[cluster]="deepseek-r1-70b-q4"

# GGUF download URLs (from model-library.json gguf_url fields)
declare -A TIER_GGUF_URLS
TIER_GGUF_URLS[nano]="https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/Qwen3.5-2B-Q4_K_M.gguf"
TIER_GGUF_URLS[edge]="https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf"
TIER_GGUF_URLS[pro]="https://huggingface.co/unsloth/Qwen3.5-27B-GGUF/resolve/main/Qwen3.5-27B-Q4_K_M.gguf"
TIER_GGUF_URLS[cluster]="https://huggingface.co/unsloth/DeepSeek-R1-Distill-Llama-70B-GGUF/resolve/main/DeepSeek-R1-Distill-Llama-70B-Q4_K_M.gguf"

# GGUF filenames (from model-library.json gguf_file fields)
declare -A TIER_GGUF_FILES
TIER_GGUF_FILES[nano]="Qwen3.5-2B-Q4_K_M.gguf"
TIER_GGUF_FILES[edge]="Qwen3.5-9B-Q4_K_M.gguf"
TIER_GGUF_FILES[pro]="Qwen3.5-27B-Q4_K_M.gguf"
TIER_GGUF_FILES[cluster]="DeepSeek-R1-Distill-Llama-70B-Q4_K_M.gguf"

# Approximate sizes in GB (from model-library.json size_mb fields)
declare -A MODEL_SIZES_GB
MODEL_SIZES_GB[nano]="2"
MODEL_SIZES_GB[edge]="6"
MODEL_SIZES_GB[pro]="17"
MODEL_SIZES_GB[cluster]="43"

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
    ║         Dream Server — Model Pre-Download                 ║
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

    if ! command -v curl &>/dev/null && ! command -v wget &>/dev/null; then
        missing+=("curl or wget")
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        error "Missing dependencies: ${missing[*]}"
        echo "Please install them first."
        exit 1
    fi
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

# Models directory — where the installer expects GGUF files
MODELS_DIR="${MODELS_DIR:-$HOME/dream-server/data/models}"

download_gguf() {
    local url="$1" filename="$2" label="$3"

    mkdir -p "$MODELS_DIR"
    local target="$MODELS_DIR/$filename"

    if [[ -f "$target" ]]; then
        local existing_size
        if [[ "$(uname -s)" == "Darwin" ]]; then
            existing_size=$(stat -f%z "$target" 2>/dev/null || echo 0)
        else
            existing_size=$(stat -c%s "$target" 2>/dev/null || echo 0)
        fi
        if [[ "$existing_size" -gt 1048576 ]]; then
            success "$label already downloaded ($filename)"
            return 0
        fi
    fi

    log "Downloading $label: $filename"
    log "  URL: $url"
    log "  Destination: $target"

    local part_file="${target}.part"

    if command -v curl &>/dev/null; then
        curl -L -# -C - -o "$part_file" "$url"
    elif command -v wget &>/dev/null; then
        wget -c -O "$part_file" "$url"
    else
        error "Neither curl nor wget found"
        return 1
    fi

    if [[ $? -eq 0 ]] && [[ -f "$part_file" ]]; then
        mv "$part_file" "$target"
        success "Downloaded $label ($filename)"
        return 0
    else
        error "Failed to download $label"
        rm -f "$part_file"
        return 1
    fi
}

verify_gguf() {
    local filename="$1" label="$2"
    local target="$MODELS_DIR/$filename"

    if [[ -f "$target" ]]; then
        local size_mb
        if [[ "$(uname -s)" == "Darwin" ]]; then
            size_mb=$(( $(stat -f%z "$target" 2>/dev/null || echo 0) / 1048576 ))
        else
            size_mb=$(( $(stat -c%s "$target" 2>/dev/null || echo 0) / 1048576 ))
        fi
        if [[ "$size_mb" -gt 1 ]]; then
            echo -e "  ${GREEN}✓${NC} $label: $filename (${size_mb}MB)"
            return 0
        fi
    fi
    echo -e "  ${RED}✗${NC} $label: $filename (not found)"
    return 1
}

#=============================================================================
# Main Functions
#=============================================================================

list_models() {
    echo -e "\n${BOLD}Available Models by Tier (Qwen profile defaults):${NC}\n"

    echo -e "${CYAN}Tier     │ Model                              │ GGUF File                              │ Size${NC}"
    echo "─────────┼────────────────────────────────────┼──────────────────────────────────────────┼──────"

    for tier in nano edge pro cluster; do
        local model="${TIER_MODELS[$tier]}"
        local filename="${TIER_GGUF_FILES[$tier]}"
        local size="${MODEL_SIZES_GB[$tier]}"
        printf "%-8s │ %-34s │ %-38s │ ~%sGB\n" "$tier" "$model" "$filename" "$size"
    done

    echo ""
    echo -e "${BOLD}Optional Components:${NC}"
    echo "  STT (Whisper): $STT_MODEL (~3GB)"
    echo "  TTS (Kokoro):  $TTS_MODEL (~0.2GB)"
    echo ""
    echo "Models are downloaded to: $MODELS_DIR"
}

verify_cache() {
    echo -e "\n${BOLD}Verifying cached GGUF models in $MODELS_DIR...${NC}\n"

    local found=0
    local missing=0

    for tier in nano edge pro cluster; do
        local filename="${TIER_GGUF_FILES[$tier]}"
        local model="${TIER_MODELS[$tier]}"
        if verify_gguf "$filename" "$model" 2>/dev/null; then
            ((found++)) || true
        else
            ((missing++)) || true
        fi
    done

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
    local url="${TIER_GGUF_URLS[$tier]}"
    local filename="${TIER_GGUF_FILES[$tier]}"
    local size="${MODEL_SIZES_GB[$tier]}"

    echo -e "\n${BOLD}Downloading ${tier} tier model${NC}"
    echo -e "  Model:    $model"
    echo -e "  File:     $filename"
    echo -e "  Size:     ~${size}GB"
    echo -e "  Dest:     $MODELS_DIR/$filename"
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

    # Download GGUF
    download_gguf "$url" "$filename" "LLM ($tier tier)" || exit 1

    # Download voice components if requested (via huggingface_hub)
    if [[ "$include_voice" == "true" ]]; then
        echo ""
        if ! command -v python3 &>/dev/null; then
            warn "python3 not found — skipping voice model downloads"
        else
            python3 << VOICE_EOF
from huggingface_hub import snapshot_download
import sys

for repo, label in [("$STT_MODEL", "STT (Whisper)"), ("$TTS_MODEL", "TTS (Kokoro)")]:
    try:
        path = snapshot_download(repo_id=repo, resume_download=True)
        print(f"✓ {label} downloaded to: {path}")
    except Exception as e:
        print(f"✗ {label} failed: {e}", file=sys.stderr)
VOICE_EOF
        fi
    fi

    echo ""
    success "Pre-download complete!"
    echo ""
    echo "You can now run install.sh — it will find the model in $MODELS_DIR."
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
Dream Server Model Pre-Download

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
