#!/bin/bash
# ============================================================================
# Dream Server Installer — Tier Map
# ============================================================================
# Part of: installers/lib/
# Purpose: Map hardware tier to model name, GGUF file, URL, and context size
#
# Expects: TIER (set by detection phase), error()
# Provides: resolve_tier_config() → sets TIER_NAME, LLM_MODEL, GGUF_FILE,
#           GGUF_URL, MAX_CONTEXT
#
# Modder notes:
#   Add new tiers or change model assignments here.
#   Each tier maps to a specific GGUF quantization and context window.
# ============================================================================

normalize_model_profile() {
    local profile="${1:-${MODEL_PROFILE:-qwen}}"
    profile="$(printf '%s' "$profile" | tr '[:upper:]' '[:lower:]')"
    case "$profile" in
        auto)               echo "auto" ;;
        gemma|gemma4|gemma-4) echo "gemma4" ;;
        *)                  echo "qwen" ;;
    esac
}

effective_model_profile() {
    local requested
    requested="$(normalize_model_profile "${1:-}")"
    if [[ "$requested" == "auto" ]]; then
        case "$TIER" in
            CLOUD|0) echo "qwen" ;;
            *)       echo "gemma4" ;;
        esac
    else
        echo "$requested"
    fi
}

configure_llama_runtime_defaults() {
    LLAMA_SERVER_IMAGE=""
    LLAMA_CPP_RELEASE_TAG_OVERRIDE=""

    case "$MODEL_PROFILE_EFFECTIVE" in
        gemma4)
            # Gemma 4 GGUFs require a newer llama.cpp than the legacy DreamServer pin.
            # Keep this aligned with docker-compose.nvidia.yml so the installer
            # pre-pulls the same image compose will start.
            LLAMA_SERVER_IMAGE="ghcr.io/ggml-org/llama.cpp:server-cuda-b9014"
            LLAMA_CPP_RELEASE_TAG_OVERRIDE="b9014"
            ;;
    esac
}

set_qwen_tier_config() {
    case $TIER in
        CLOUD)
            TIER_NAME="Cloud (API)"
            LLM_MODEL="anthropic/claude-sonnet-4-5-20250514"
            GGUF_FILE=""
            GGUF_URL=""
            GGUF_SHA256=""
            MAX_CONTEXT=200000
            LLM_MODEL_SIZE_MB=0
            ;;
        ARC)
            # Intel Arc A770 (16 GB) and future Arc B-series (≥12 GB VRAM)
            # llama.cpp SYCL backend: N_GPU_LAYERS=99 offloads all layers to GPU
            TIER_NAME="Intel Arc"
            LLM_MODEL="qwen3.5-9b"
            GGUF_FILE="Qwen3.5-9B-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf"
            GGUF_SHA256="03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8"
            MAX_CONTEXT=32768
            LLM_MODEL_SIZE_MB=5760    # Qwen3.5-9B-Q4_K_M (5.68 GB)
            GPU_BACKEND="sycl"
            N_GPU_LAYERS=99
            ;;
        ARC_LITE)
            # Intel Arc A750 (8 GB), A380 (6 GB) — smaller VRAM, lighter model
            # llama.cpp SYCL backend: N_GPU_LAYERS=99 offloads all layers to GPU
            TIER_NAME="Intel Arc Lite"
            LLM_MODEL="qwen3.5-4b"
            GGUF_FILE="Qwen3.5-4B-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf"
            GGUF_SHA256="00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4"
            MAX_CONTEXT=16384
            LLM_MODEL_SIZE_MB=2870    # Qwen3.5-4B-Q4_K_M (2.74 GB)
            GPU_BACKEND="sycl"
            N_GPU_LAYERS=99
            ;;
        NV_ULTRA)
            TIER_NAME="NVIDIA Ultra (90GB+)"
            LLM_MODEL="qwen3-coder-next"
            GGUF_FILE="qwen3-coder-next-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/unsloth/Qwen3-Coder-Next-GGUF/resolve/main/Qwen3-Coder-Next-Q4_K_M.gguf"
            GGUF_SHA256="9e6032d2f3b50a60f17ce8bf5a1d85c71af9b53b89c7978020ae7c660f29b090"
            MAX_CONTEXT=131072
            LLM_MODEL_SIZE_MB=48500   # 48.5 GB per HF file listing
            # NV_ULTRA on aarch64 (DGX Spark / GB10): qwen3-coder-next MoE
            # Q4_K_M produces all-`?` tokens on every chat completion against
            # llama.cpp b9014 on Blackwell-aarch64 (SHA256 verifies, server
            # starts cleanly, decode rate is normal, but every token is `?`).
            # Verified bug is coder-next-specific, not a general MoE kernel
            # bug: Qwen3.6-35B-A3B (UD-Q4_K_M) serves cleanly on the same
            # build, same hardware. Until upstream fixes coder-next on this
            # build, route Spark to the A3B MoE — same architectural fit
            # (large total / small active params on unified memory).
            if [[ "${HOST_ARCH:-}" == "arm64" ]]; then
                TIER_NAME="NVIDIA Ultra (90GB+, aarch64 — A3B substitution)"
                LLM_MODEL="qwen3.6-35b-a3b"
                GGUF_FILE="Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
                GGUF_URL="https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/resolve/main/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
                GGUF_SHA256="ac0e2c1189e055faa36eff361580e79c5bd6f8e76bffb4ce547f167d53e31a61"
                MAX_CONTEXT=131072
                LLM_MODEL_SIZE_MB=21110
            fi
            ;;
        SH_LARGE)
            TIER_NAME="Strix Halo 90+"
            LLM_MODEL="qwen3-coder-next"
            GGUF_FILE="qwen3-coder-next-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/unsloth/Qwen3-Coder-Next-GGUF/resolve/main/Qwen3-Coder-Next-Q4_K_M.gguf"
            GGUF_SHA256="9e6032d2f3b50a60f17ce8bf5a1d85c71af9b53b89c7978020ae7c660f29b090"
            MAX_CONTEXT=131072
            LLM_MODEL_SIZE_MB=48500   # 48.5 GB per HF file listing
            ;;
        SH_COMPACT)
            TIER_NAME="Strix Halo Compact"
            LLM_MODEL="qwen3-30b-a3b"
            GGUF_FILE="Qwen3-30B-A3B-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/unsloth/Qwen3-30B-A3B-GGUF/resolve/main/Qwen3-30B-A3B-Q4_K_M.gguf"
            GGUF_SHA256="9f1a24700a339b09c06009b729b5c809e0b64c213b8af5b711b3dbdfd0c5ba48"
            MAX_CONTEXT=131072
            LLM_MODEL_SIZE_MB=18600   # 18.6 GB per HF file listing
            ;;
        0)
            TIER_NAME="Lightweight"
            LLM_MODEL="qwen3.5-2b"
            GGUF_FILE="Qwen3.5-2B-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/Qwen3.5-2B-Q4_K_M.gguf"
            GGUF_SHA256=""
            MAX_CONTEXT=8192
            LLM_MODEL_SIZE_MB=1500    # Qwen3.5-2B-Q4_K_M (1.28 GB)
            ;;
        1)
            TIER_NAME="Entry Level"
            LLM_MODEL="qwen3.5-9b"
            GGUF_FILE="Qwen3.5-9B-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf"
            GGUF_SHA256="03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8"
            MAX_CONTEXT=16384
            LLM_MODEL_SIZE_MB=5760    # Qwen3.5-9B-Q4_K_M (5.68 GB)
            ;;
        2)
            TIER_NAME="Prosumer"
            LLM_MODEL="qwen3.5-9b"
            GGUF_FILE="Qwen3.5-9B-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf"
            GGUF_SHA256="03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8"
            MAX_CONTEXT=32768
            LLM_MODEL_SIZE_MB=5760    # Qwen3.5-9B-Q4_K_M (5.68 GB)
            ;;
        3)
            TIER_NAME="Pro"
            LLM_MODEL="qwen3-30b-a3b"
            GGUF_FILE="Qwen3-30B-A3B-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/unsloth/Qwen3-30B-A3B-GGUF/resolve/main/Qwen3-30B-A3B-Q4_K_M.gguf"
            GGUF_SHA256="9f1a24700a339b09c06009b729b5c809e0b64c213b8af5b711b3dbdfd0c5ba48"
            MAX_CONTEXT=32768
            LLM_MODEL_SIZE_MB=18600   # Qwen3-30B-A3B-Q4_K_M MoE (18.6 GB)
            ;;
        4)
            TIER_NAME="Enterprise"
            LLM_MODEL="qwen3-30b-a3b"
            GGUF_FILE="Qwen3-30B-A3B-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/unsloth/Qwen3-30B-A3B-GGUF/resolve/main/Qwen3-30B-A3B-Q4_K_M.gguf"
            GGUF_SHA256="9f1a24700a339b09c06009b729b5c809e0b64c213b8af5b711b3dbdfd0c5ba48"
            MAX_CONTEXT=131072
            LLM_MODEL_SIZE_MB=18600   # 18.6 GB per HF file listing
            ;;
        *)
            error "Invalid tier: $TIER. Valid tiers: 0, 1, 2, 3, 4, CLOUD, NV_ULTRA, SH_LARGE, SH_COMPACT, ARC, ARC_LITE"
            # NOTE for modders: add your tier above this line and update this message.
            ;;
    esac
}

set_gemma4_tier_config() {
    case $TIER in
        CLOUD)
            TIER_NAME="Cloud (API)"
            LLM_MODEL="anthropic/claude-sonnet-4-5-20250514"
            GGUF_FILE=""
            GGUF_URL=""
            GGUF_SHA256=""
            MAX_CONTEXT=200000
            LLM_MODEL_SIZE_MB=0
            ;;
        ARC)
            TIER_NAME="Intel Arc"
            LLM_MODEL="gemma-4-e4b-it"
            GGUF_FILE="gemma-4-E4B-it-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-Q4_K_M.gguf"
            GGUF_SHA256=""
            MAX_CONTEXT=32768
            LLM_MODEL_SIZE_MB=5340
            GPU_BACKEND="sycl"
            N_GPU_LAYERS=99
            ;;
        ARC_LITE)
            TIER_NAME="Intel Arc Lite"
            LLM_MODEL="gemma-4-e2b-it"
            GGUF_FILE="gemma-4-E2B-it-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_K_M.gguf"
            GGUF_SHA256=""
            MAX_CONTEXT=16384
            LLM_MODEL_SIZE_MB=2810
            GPU_BACKEND="sycl"
            N_GPU_LAYERS=99
            ;;
        NV_ULTRA)
            TIER_NAME="NVIDIA Ultra (90GB+)"
            LLM_MODEL="gemma-4-31b-it"
            GGUF_FILE="gemma-4-31B-it-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/ggml-org/gemma-4-31B-it-GGUF/resolve/main/gemma-4-31B-it-Q4_K_M.gguf"
            GGUF_SHA256=""
            MAX_CONTEXT=131072
            LLM_MODEL_SIZE_MB=19800
            ;;
        SH_LARGE)
            TIER_NAME="Strix Halo 90+"
            LLM_MODEL="gemma-4-31b-it"
            GGUF_FILE="gemma-4-31B-it-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/ggml-org/gemma-4-31B-it-GGUF/resolve/main/gemma-4-31B-it-Q4_K_M.gguf"
            GGUF_SHA256=""
            MAX_CONTEXT=131072
            LLM_MODEL_SIZE_MB=19800
            ;;
        SH_COMPACT)
            TIER_NAME="Strix Halo Compact"
            LLM_MODEL="gemma-4-26b-a4b-it"
            GGUF_FILE="gemma-4-26B-A4B-it-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/ggml-org/gemma-4-26B-A4B-it-GGUF/resolve/main/gemma-4-26B-A4B-it-Q4_K_M.gguf"
            GGUF_SHA256=""
            MAX_CONTEXT=65536
            LLM_MODEL_SIZE_MB=18000
            ;;
        0)
            # Keep the current tiny bootstrap-friendly Qwen path for the absolute minimum tier.
            TIER_NAME="Lightweight"
            LLM_MODEL="qwen3.5-2b"
            GGUF_FILE="Qwen3.5-2B-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/Qwen3.5-2B-Q4_K_M.gguf"
            GGUF_SHA256=""
            MAX_CONTEXT=8192
            LLM_MODEL_SIZE_MB=1500
            ;;
        1)
            TIER_NAME="Entry Level"
            LLM_MODEL="gemma-4-e2b-it"
            GGUF_FILE="gemma-4-E2B-it-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_K_M.gguf"
            GGUF_SHA256=""
            MAX_CONTEXT=16384
            LLM_MODEL_SIZE_MB=2810
            ;;
        2)
            TIER_NAME="Prosumer"
            LLM_MODEL="gemma-4-e4b-it"
            GGUF_FILE="gemma-4-E4B-it-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-Q4_K_M.gguf"
            GGUF_SHA256=""
            MAX_CONTEXT=32768
            LLM_MODEL_SIZE_MB=5340
            ;;
        3)
            TIER_NAME="Pro"
            LLM_MODEL="gemma-4-26b-a4b-it"
            GGUF_FILE="gemma-4-26B-A4B-it-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/ggml-org/gemma-4-26B-A4B-it-GGUF/resolve/main/gemma-4-26B-A4B-it-Q4_K_M.gguf"
            GGUF_SHA256=""
            MAX_CONTEXT=16384
            LLM_MODEL_SIZE_MB=18000
            ;;
        4)
            TIER_NAME="Enterprise"
            LLM_MODEL="gemma-4-31b-it"
            GGUF_FILE="gemma-4-31B-it-Q4_K_M.gguf"
            GGUF_URL="https://huggingface.co/ggml-org/gemma-4-31B-it-GGUF/resolve/main/gemma-4-31B-it-Q4_K_M.gguf"
            GGUF_SHA256=""
            MAX_CONTEXT=65536
            LLM_MODEL_SIZE_MB=19800
            ;;
        *)
            error "Invalid tier: $TIER. Valid tiers: 0, 1, 2, 3, 4, CLOUD, NV_ULTRA, SH_LARGE, SH_COMPACT, ARC, ARC_LITE"
            ;;
    esac
}

resolve_tier_config() {
    MODEL_PROFILE_REQUESTED="$(normalize_model_profile)"
    MODEL_PROFILE_EFFECTIVE="$(effective_model_profile "$MODEL_PROFILE_REQUESTED")"

    case "$MODEL_PROFILE_EFFECTIVE" in
        gemma4)
            if ! set_gemma4_tier_config; then
                return 1
            fi
            ;;
        *)
            if ! set_qwen_tier_config; then
                return 1
            fi
            ;;
    esac

    configure_llama_runtime_defaults
}

# Map a tier name to its LLM_MODEL value (used by dream model swap)
tier_to_model() {
    local t="$1"
    local requested effective
    local previous_tier="${TIER:-}"
    requested="$(normalize_model_profile "${2:-}")"
    TIER="$t"
    effective="$(effective_model_profile "$requested")"

    local model=""
    case "$effective" in
        gemma4)
            case "$t" in
                CLOUD)          model="anthropic/claude-sonnet-4-5-20250514" ;;
                NV_ULTRA)       model="gemma-4-31b-it" ;;
                SH_LARGE)       model="gemma-4-31b-it" ;;
                SH_COMPACT|SH)  model="gemma-4-26b-a4b-it" ;;
                ARC)            model="gemma-4-e4b-it" ;;
                ARC_LITE)       model="gemma-4-e2b-it" ;;
                0|T0)           model="qwen3.5-2b" ;;
                1|T1)           model="gemma-4-e2b-it" ;;
                2|T2)           model="gemma-4-e4b-it" ;;
                3|T3)           model="gemma-4-26b-a4b-it" ;;
                4|T4)           model="gemma-4-31b-it" ;;
                *)              model="" ;;
            esac
            ;;
        *)
            case "$t" in
                CLOUD)          model="anthropic/claude-sonnet-4-5-20250514" ;;
                NV_ULTRA)
                    if [[ "${HOST_ARCH:-}" == "arm64" ]]; then
                        model="qwen3.6-35b-a3b"
                    else
                        model="qwen3-coder-next"
                    fi
                    ;;
                SH_LARGE)       model="qwen3-coder-next" ;;
                SH_COMPACT|SH)  model="qwen3-30b-a3b" ;;
                ARC)            model="qwen3.5-9b" ;;
                ARC_LITE)       model="qwen3.5-4b" ;;
                0|T0)           model="qwen3.5-2b" ;;
                1|T1)           model="qwen3.5-9b" ;;
                2|T2)           model="qwen3.5-9b" ;;
                3|T3)           model="qwen3-30b-a3b" ;;
                4|T4)           model="qwen3-30b-a3b" ;;
                *)              model="" ;;
            esac
            ;;
    esac

    if [[ -n "${previous_tier}" ]]; then
        TIER="$previous_tier"
    else
        unset TIER
    fi

    echo "$model"
}
