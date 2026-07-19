#!/bin/bash
# ============================================================================
# ODS macOS Installer -- Tier Map
# ============================================================================
# Part of: installers/macos/lib/
# Purpose: Map hardware tier to model name, GGUF file, URL, and context size
#
# Canonical source: installers/lib/tier-map.sh (keep values byte-identical)
#
# Modder notes:
#   Add new tiers or change model assignments here.
#   Each tier maps to a specific GGUF quantization and context window.
#
#   Apple Silicon unified memory:
#   All system RAM is shared between macOS, Docker, and the LLM.
#   ~8GB is consumed by system overhead, so tier thresholds are
#   set conservatively compared to discrete-GPU platforms.
# ============================================================================

normalize_model_profile() {
    local profile="${1:-${MODEL_PROFILE:-qwen}}"
    profile="$(printf '%s' "$profile" | tr '[:upper:]' '[:lower:]')"
    case "$profile" in
        auto)                 echo "auto" ;;
        gemma|gemma4|gemma-4) echo "gemma4" ;;
        *)                    echo "qwen" ;;
    esac
}

effective_model_profile() {
    local tier="$1"
    local requested
    requested="$(normalize_model_profile "${2:-}")"
    if [[ "$requested" == "auto" ]]; then
        case "$tier" in
            CLOUD|0) echo "qwen" ;;
            *)       echo "gemma4" ;;
        esac
    else
        echo "$requested"
    fi
}

configure_llama_runtime_defaults() {
    LLAMA_CPP_RELEASE_TAG_OVERRIDE=""
    case "$MODEL_PROFILE_EFFECTIVE" in
        gemma4) LLAMA_CPP_RELEASE_TAG_OVERRIDE="b9014" ;;
    esac
}

set_qwen_tier_config() {
    local tier="$1"

    case "$tier" in
        CLOUD)
            TIER_NAME="Cloud (API)"
            LLM_MODEL="anthropic/claude-sonnet-4-5-20250514"
            GGUF_FILE=""
            GGUF_URL=""
            GGUF_SHA256=""
            MAX_CONTEXT=200000
            ;;
        4)
            TIER_NAME="Enterprise"
            LLM_MODEL="qwen3-30b-a3b"
            GGUF_FILE="Qwen3-30B-A3B-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/unsloth/Qwen3-30B-A3B-GGUF/resolve/main/Qwen3-30B-A3B-Q4_K_M.gguf"
            GGUF_SHA256="9f1a24700a339b09c06009b729b5c809e0b64c213b8af5b711b3dbdfd0c5ba48"
            MAX_CONTEXT=131072
            ;;
        3)
            TIER_NAME="Pro"
            LLM_MODEL="qwen3-30b-a3b"
            GGUF_FILE="Qwen3-30B-A3B-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/unsloth/Qwen3-30B-A3B-GGUF/resolve/main/Qwen3-30B-A3B-Q4_K_M.gguf"
            GGUF_SHA256="9f1a24700a339b09c06009b729b5c809e0b64c213b8af5b711b3dbdfd0c5ba48"
            MAX_CONTEXT=32768
            ;;
        2)
            TIER_NAME="Prosumer"
            LLM_MODEL="qwen3.5-9b"
            GGUF_FILE="Qwen3.5-9B-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf"
            GGUF_SHA256="03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8"
            MAX_CONTEXT=32768
            ;;
        0)
            TIER_NAME="Lightweight"
            LLM_MODEL="qwen3.5-2b"
            GGUF_FILE="Qwen3.5-2B-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/Qwen3.5-2B-Q4_K_M.gguf"
            GGUF_SHA256=""
            MAX_CONTEXT=8192
            ;;
        1)
            TIER_NAME="Entry Level"
            LLM_MODEL="qwen3.5-9b"
            GGUF_FILE="Qwen3.5-9B-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf"
            GGUF_SHA256="03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8"
            MAX_CONTEXT=16384
            ;;
        *)
            ai_err "Invalid tier: $tier. Valid tiers: 0, 1, 2, 3, 4, CLOUD"
            exit 1
            ;;
    esac
}

set_gemma4_tier_config() {
    local tier="$1"

    case "$tier" in
        CLOUD)
            TIER_NAME="Cloud (API)"
            LLM_MODEL="anthropic/claude-sonnet-4-5-20250514"
            GGUF_FILE=""
            GGUF_URL=""
            GGUF_SHA256=""
            MAX_CONTEXT=200000
            ;;
        4)
            TIER_NAME="Enterprise"
            LLM_MODEL="gemma-4-31b-it"
            GGUF_FILE="gemma-4-31B-it-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/ggml-org/gemma-4-31B-it-GGUF/resolve/main/gemma-4-31B-it-Q4_K_M.gguf"
            GGUF_SHA256=""
            MAX_CONTEXT=65536
            ;;
        3)
            TIER_NAME="Pro"
            LLM_MODEL="gemma-4-26b-a4b-it"
            GGUF_FILE="gemma-4-26B-A4B-it-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/ggml-org/gemma-4-26B-A4B-it-GGUF/resolve/main/gemma-4-26B-A4B-it-Q4_K_M.gguf"
            GGUF_SHA256=""
            MAX_CONTEXT=16384
            ;;
        2)
            TIER_NAME="Prosumer"
            LLM_MODEL="gemma-4-e4b-it"
            GGUF_FILE="gemma-4-E4B-it-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-Q4_K_M.gguf"
            GGUF_SHA256=""
            MAX_CONTEXT=32768
            ;;
        0)
            TIER_NAME="Lightweight"
            LLM_MODEL="qwen3.5-2b"
            GGUF_FILE="Qwen3.5-2B-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/Qwen3.5-2B-Q4_K_M.gguf"
            GGUF_SHA256=""
            MAX_CONTEXT=8192
            ;;
        1)
            TIER_NAME="Entry Level"
            LLM_MODEL="gemma-4-e2b-it"
            GGUF_FILE="gemma-4-E2B-it-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_K_M.gguf"
            GGUF_SHA256=""
            MAX_CONTEXT=16384
            ;;
        *)
            ai_err "Invalid tier: $tier. Valid tiers: 0, 1, 2, 3, 4, CLOUD"
            exit 1
            ;;
    esac
}

resolve_tier_config() {
    local tier="$1"

    MODEL_PROFILE_REQUESTED="$(normalize_model_profile)"
    MODEL_PROFILE_EFFECTIVE="$(effective_model_profile "$tier" "$MODEL_PROFILE_REQUESTED")"

    case "$MODEL_PROFILE_EFFECTIVE" in
        gemma4)
            if ! set_gemma4_tier_config "$tier"; then
                return 1
            fi
            ;;
        *)
            if ! set_qwen_tier_config "$tier"; then
                return 1
            fi
            ;;
    esac

    configure_llama_runtime_defaults
}

# Auto-select tier based on Apple Silicon unified memory
#
# Unlike discrete GPUs, Apple Silicon shares all system RAM between
# macOS (~4-5GB), Docker + services (~2-3GB), and the LLM model.
# Effective free memory ≈ total RAM minus ~8GB system overhead.
#
# Thresholds are set conservatively so the model + KV cache fit
# comfortably alongside everything else.
auto_select_tier() {
    local ram_gb="$1"
    local chip_variant="${2:-base}"

    if [[ "$ram_gb" -ge 64 ]]; then
        echo "4"
    elif [[ "$ram_gb" -ge 48 ]]; then
        echo "3"
    elif [[ "$ram_gb" -ge 32 ]]; then
        echo "2"
    elif [[ "$ram_gb" -ge 16 ]]; then
        # 16–31 GB unified → 9B model
        echo "1"
    else
        # < 16 GB unified → ultra-lightweight 2B model
        echo "0"
    fi
}

# ============================================================================
# Bootstrap Fast-Start
# ============================================================================

BOOTSTRAP_GGUF_FILE="Qwen3.5-2B-Q4_K_M.gguf"
BOOTSTRAP_GGUF_URL="https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/Qwen3.5-2B-Q4_K_M.gguf"
BOOTSTRAP_LLM_MODEL="qwen3.5-2b"
# Hermes requires at least a 64K context window. Keep the fast-start model at
# that floor so Hermes works during the first-run bootstrap experience too.
BOOTSTRAP_MAX_CONTEXT=65536

macos_tier_rank() {
    case "$1" in
        4)          echo 4 ;;
        3)          echo 3 ;;
        2)          echo 2 ;;
        1)          echo 1 ;;
        0)          echo 0 ;;
        *)          echo 1 ;;
    esac
}

bootstrap_needed() {
    local tier="$1"
    local install_dir="$2"
    local gguf_file="$3"

    [[ "${NO_BOOTSTRAP:-false}" == "true" ]] && return 1
    [[ "${CLOUD_MODE:-false}" == "true" ]] && return 1
    [[ "${OFFLINE_MODE:-false}" == "true" ]] && return 1
    [[ "$(macos_tier_rank "$tier")" -le 0 ]] && return 1
    [[ -f "${install_dir}/data/models/${gguf_file}" ]] && return 1
    return 0
}
