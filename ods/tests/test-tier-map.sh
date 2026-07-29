#!/bin/bash
# ============================================================================
# Test: resolve_tier_config() — tier-map.sh
# ============================================================================
# Sources the actual tier-map.sh and verifies each tier resolves to the
# correct LLM_MODEL, GGUF_FILE, and MAX_CONTEXT.
#
# Run: bash tests/test-tier-map.sh
# ============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0
FAIL=0

# Minimal stubs for dependencies
error() { echo "ERROR: $*" >&2; return 1; }

# Source the module under test
source "$SCRIPT_DIR/installers/lib/tier-map.sh"
source "$SCRIPT_DIR/lib/safe-env.sh"

load_selector_env() {
    load_model_selector_env_from_output <<< "$1"
}

assert_eq() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        echo "  PASS: $label"
        ((PASS++))
    else
        echo "  FAIL: $label (expected '$expected', got '$actual')"
        ((FAIL++))
    fi
}

run_tier() {
    local tier_val="$1"
    TIER="$tier_val"
    # Reset globals
    TIER_NAME="" LLM_MODEL="" GGUF_FILE="" GGUF_URL="" MAX_CONTEXT=""
    GPU_BACKEND="" N_GPU_LAYERS=""
    resolve_tier_config
}

echo "=== Testing resolve_tier_config() ==="
echo ""

# --- Tier 0: Lightweight ---
echo "Tier 0 (Lightweight):"
run_tier 0
assert_eq "TIER_NAME"   "Lightweight"                          "$TIER_NAME"
assert_eq "LLM_MODEL"   "qwen3.5-2b"                          "$LLM_MODEL"
assert_eq "GGUF_FILE"   "Qwen3.5-2B-Q4_K_M.gguf"             "$GGUF_FILE"
assert_eq "MAX_CONTEXT"  "8192"                                 "$MAX_CONTEXT"
echo ""
# --- Tier 1: Entry Level ---
echo "Tier 1 (Entry Level):"
run_tier 1
assert_eq "TIER_NAME"   "Entry Level"                          "$TIER_NAME"
assert_eq "MODEL_PROFILE_EFFECTIVE" "qwen"                   "$MODEL_PROFILE_EFFECTIVE"
assert_eq "LLM_MODEL"   "qwen3.5-9b"                         "$LLM_MODEL"
assert_eq "GGUF_FILE"   "Qwen3.5-9B-Q4_K_M.gguf"             "$GGUF_FILE"
assert_eq "MAX_CONTEXT"  "16384"                                "$MAX_CONTEXT"
echo ""

# --- Tier 2: Prosumer ---
echo "Tier 2 (Prosumer):"
run_tier 2
assert_eq "TIER_NAME"   "Prosumer"                             "$TIER_NAME"
assert_eq "MODEL_PROFILE_EFFECTIVE" "qwen"                   "$MODEL_PROFILE_EFFECTIVE"
assert_eq "LLM_MODEL"   "qwen3.5-9b"                         "$LLM_MODEL"
assert_eq "GGUF_FILE"   "Qwen3.5-9B-Q4_K_M.gguf"             "$GGUF_FILE"
assert_eq "MAX_CONTEXT"  "32768"                                "$MAX_CONTEXT"
echo ""

# --- Tier 3: Pro ---
echo "Tier 3 (Pro):"
run_tier 3
assert_eq "TIER_NAME"   "Pro"                                  "$TIER_NAME"
assert_eq "MODEL_PROFILE_EFFECTIVE" "qwen"                   "$MODEL_PROFILE_EFFECTIVE"
assert_eq "LLM_MODEL"   "qwen3-30b-a3b"                       "$LLM_MODEL"
assert_eq "GGUF_FILE"   "Qwen3-30B-A3B-Q4_K_M.gguf"           "$GGUF_FILE"
assert_eq "MAX_CONTEXT"  "32768"                                "$MAX_CONTEXT"
echo ""

# --- Tier 4: Enterprise ---
echo "Tier 4 (Enterprise):"
run_tier 4
assert_eq "TIER_NAME"   "Enterprise"                           "$TIER_NAME"
assert_eq "MODEL_PROFILE_EFFECTIVE" "qwen"                   "$MODEL_PROFILE_EFFECTIVE"
assert_eq "LLM_MODEL"   "qwen3-30b-a3b"                       "$LLM_MODEL"
assert_eq "GGUF_FILE"   "Qwen3-30B-A3B-Q4_K_M.gguf"           "$GGUF_FILE"
assert_eq "MAX_CONTEXT"  "131072"                               "$MAX_CONTEXT"
echo ""

# --- NV_ULTRA ---
echo "NV_ULTRA (NVIDIA Ultra 90GB+):"
unset HOST_ARCH
run_tier NV_ULTRA
assert_eq "TIER_NAME"   "NVIDIA Ultra (90GB+)"                 "$TIER_NAME"
assert_eq "MODEL_PROFILE_EFFECTIVE" "qwen"                   "$MODEL_PROFILE_EFFECTIVE"
assert_eq "LLM_MODEL"   "qwen3-coder-next"                   "$LLM_MODEL"
assert_eq "GGUF_FILE"   "qwen3-coder-next-Q4_K_M.gguf"       "$GGUF_FILE"
assert_eq "MAX_CONTEXT"  "131072"                               "$MAX_CONTEXT"
echo ""

echo "NV_ULTRA (aarch64 A3B substitution):"
HOST_ARCH=arm64
run_tier NV_ULTRA
assert_eq "TIER_NAME"   "NVIDIA Ultra (90GB+, aarch64 — A3B substitution)" "$TIER_NAME"
assert_eq "MODEL_PROFILE_EFFECTIVE" "qwen"                   "$MODEL_PROFILE_EFFECTIVE"
assert_eq "LLM_MODEL"   "qwen3.6-35b-a3b"                    "$LLM_MODEL"
assert_eq "GGUF_FILE"   "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"     "$GGUF_FILE"
assert_eq "MAX_CONTEXT"  "131072"                               "$MAX_CONTEXT"
unset HOST_ARCH
echo ""

# --- SH_LARGE ---
# SH_LARGE substitutes to 35B-A3B for the same unified-memory reason as
# NV_ULTRA aarch64 — see tier-map.sh SH_LARGE block.
echo "SH_LARGE (Strix Halo 90+):"
run_tier SH_LARGE
assert_eq "TIER_NAME"   "Strix Halo 90+"                      "$TIER_NAME"
assert_eq "MODEL_PROFILE_EFFECTIVE" "qwen"                   "$MODEL_PROFILE_EFFECTIVE"
assert_eq "LLM_MODEL"   "qwen3.6-35b-a3b"                    "$LLM_MODEL"
assert_eq "GGUF_FILE"   "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"     "$GGUF_FILE"
assert_eq "MAX_CONTEXT"  "131072"                               "$MAX_CONTEXT"
echo ""

# --- SH_COMPACT ---
echo "SH_COMPACT (Strix Halo Compact):"
run_tier SH_COMPACT
assert_eq "TIER_NAME"   "Strix Halo Compact"                  "$TIER_NAME"
assert_eq "MODEL_PROFILE_EFFECTIVE" "qwen"                   "$MODEL_PROFILE_EFFECTIVE"
assert_eq "LLM_MODEL"   "qwen3-30b-a3b"                       "$LLM_MODEL"
assert_eq "GGUF_FILE"   "Qwen3-30B-A3B-Q4_K_M.gguf"           "$GGUF_FILE"
assert_eq "MAX_CONTEXT"  "131072"                               "$MAX_CONTEXT"
echo ""

# --- ARC ---
echo "ARC (Intel Arc ≥12 GB, e.g. A770 16 GB):"
run_tier ARC
assert_eq "TIER_NAME"    "Intel Arc"                           "$TIER_NAME"
assert_eq "MODEL_PROFILE_EFFECTIVE" "qwen"                   "$MODEL_PROFILE_EFFECTIVE"
assert_eq "LLM_MODEL"    "qwen3.5-9b"                        "$LLM_MODEL"
assert_eq "GGUF_FILE"    "Qwen3.5-9B-Q4_K_M.gguf"            "$GGUF_FILE"
assert_eq "MAX_CONTEXT"  "32768"                               "$MAX_CONTEXT"
assert_eq "GPU_BACKEND"  "sycl"                                "$GPU_BACKEND"
assert_eq "N_GPU_LAYERS" "99"                                  "$N_GPU_LAYERS"
echo ""

# --- ARC_LITE ---
echo "ARC_LITE (Intel Arc <12 GB, e.g. A750 8 GB / A380 6 GB):"
run_tier ARC_LITE
assert_eq "TIER_NAME"    "Intel Arc Lite"                      "$TIER_NAME"
assert_eq "MODEL_PROFILE_EFFECTIVE" "qwen"                   "$MODEL_PROFILE_EFFECTIVE"
assert_eq "LLM_MODEL"    "qwen3.5-4b"                        "$LLM_MODEL"
assert_eq "GGUF_FILE"    "Qwen3.5-4B-Q4_K_M.gguf"            "$GGUF_FILE"
assert_eq "MAX_CONTEXT"  "16384"                               "$MAX_CONTEXT"
assert_eq "GPU_BACKEND"  "sycl"                                "$GPU_BACKEND"
assert_eq "N_GPU_LAYERS" "99"                                  "$N_GPU_LAYERS"
echo ""

# --- Invalid tier should fail ---
echo "Invalid tier (should fail):"
if TIER="INVALID" resolve_tier_config 2>/dev/null; then
    echo "  FAIL: Invalid tier did not return error"
    ((FAIL++))
else
    echo "  PASS: Invalid tier returned error"
    ((PASS++))
fi
echo ""

# --- GGUF_URL should be set for all tiers ---
echo "GGUF_URL populated for all tiers:"
for t in 0 1 2 3 4 NV_ULTRA SH_LARGE SH_COMPACT ARC ARC_LITE; do
    run_tier "$t"
    if [[ -n "$GGUF_URL" && "$GGUF_URL" == https://* ]]; then
        echo "  PASS: Tier $t has valid GGUF_URL"
        ((PASS++))
    else
        echo "  FAIL: Tier $t missing or invalid GGUF_URL"
        ((FAIL++))
    fi
done
echo ""

# --- Gemma 4 profile opt-in ---
echo "Gemma 4 profile (tier 2):"
MODEL_PROFILE=gemma4 run_tier 2
assert_eq "MODEL_PROFILE_EFFECTIVE" "gemma4"                   "$MODEL_PROFILE_EFFECTIVE"
assert_eq "LLM_MODEL"               "gemma-4-e4b-it"           "$LLM_MODEL"
assert_eq "GGUF_FILE"               "gemma-4-E4B-it-Q4_K_M.gguf" "$GGUF_FILE"
assert_eq "LLAMA_SERVER_IMAGE" "ghcr.io/ggml-org/llama.cpp:server-cuda-b9014" "$LLAMA_SERVER_IMAGE"
assert_eq "LLAMA_CPP_RELEASE_TAG_OVERRIDE" "b9014"             "$LLAMA_CPP_RELEASE_TAG_OVERRIDE"
unset MODEL_PROFILE
echo ""

echo "Auto profile (tier 0 fallback):"
MODEL_PROFILE=auto run_tier 0
assert_eq "MODEL_PROFILE_EFFECTIVE" "qwen"                     "$MODEL_PROFILE_EFFECTIVE"
assert_eq "LLM_MODEL"               "qwen3.5-2b"               "$LLM_MODEL"
unset MODEL_PROFILE
echo ""

echo "Auto profile (tier 3 prefers Gemma with safer context):"
MODEL_PROFILE=auto run_tier 3
assert_eq "MODEL_PROFILE_EFFECTIVE" "gemma4"                   "$MODEL_PROFILE_EFFECTIVE"
assert_eq "LLM_MODEL"               "gemma-4-26b-a4b-it"       "$LLM_MODEL"
assert_eq "MAX_CONTEXT"             "16384"                    "$MAX_CONTEXT"
unset MODEL_PROFILE
echo ""

echo "Auto profile (tier 4 prefers Gemma with safer context):"
MODEL_PROFILE=auto run_tier 4
assert_eq "MODEL_PROFILE_EFFECTIVE" "gemma4"                   "$MODEL_PROFILE_EFFECTIVE"
assert_eq "LLM_MODEL"               "gemma-4-31b-it"           "$LLM_MODEL"
assert_eq "MAX_CONTEXT"             "65536"                    "$MAX_CONTEXT"
unset MODEL_PROFILE
echo ""

echo "Auto profile (SH_COMPACT prefers Gemma with safer context):"
MODEL_PROFILE=auto run_tier SH_COMPACT
assert_eq "MODEL_PROFILE_EFFECTIVE" "gemma4"                   "$MODEL_PROFILE_EFFECTIVE"
assert_eq "LLM_MODEL"               "gemma-4-26b-a4b-it"       "$LLM_MODEL"
assert_eq "MAX_CONTEXT"             "65536"                    "$MAX_CONTEXT"
unset MODEL_PROFILE
echo ""

echo "Explicit qwen profile (tier 2):"
MODEL_PROFILE=qwen run_tier 2
assert_eq "MODEL_PROFILE_EFFECTIVE" "qwen"                     "$MODEL_PROFILE_EFFECTIVE"
assert_eq "LLM_MODEL"               "qwen3.5-9b"               "$LLM_MODEL"
assert_eq "GGUF_FILE"               "Qwen3.5-9B-Q4_K_M.gguf"   "$GGUF_FILE"
unset MODEL_PROFILE
echo ""

echo "Catalog selector (amd64 NV_ULTRA keeps coder-next):"
_selector_env="$(python3 "$SCRIPT_DIR/scripts/select-model.py" \
    --catalog "$SCRIPT_DIR/config/model-library.json" \
    --backend nvidia \
    --memory-type discrete \
    --vram-mb 98304 \
    --ram-gb 128 \
    --profile qwen \
    --tier NV_ULTRA \
    --host-arch amd64 \
    --installable-only \
    --env)"
LLM_MODEL="" GGUF_FILE="" MODEL_RECOMMENDATION_POLICY=""
load_selector_env "$_selector_env"
assert_eq "SELECTOR_LLM_MODEL" "qwen3-coder-next" "$LLM_MODEL"
assert_eq "SELECTOR_GGUF_FILE" "qwen3-coder-next-Q4_K_M.gguf" "$GGUF_FILE"
assert_eq "SELECTOR_POLICY" "context-aware-largest-capable-general-v1" "$MODEL_RECOMMENDATION_POLICY"
echo ""

echo "Catalog selector (arm64 NV_ULTRA preserves A3B substitution):"
_selector_env="$(python3 "$SCRIPT_DIR/scripts/select-model.py" \
    --catalog "$SCRIPT_DIR/config/model-library.json" \
    --backend nvidia \
    --memory-type unified \
    --vram-mb 0 \
    --ram-gb 128 \
    --profile qwen \
    --tier NV_ULTRA \
    --host-arch arm64 \
    --installable-only \
    --env)"
LLM_MODEL="" GGUF_FILE="" MODEL_RECOMMENDATION_POLICY=""
load_selector_env "$_selector_env"
assert_eq "SELECTOR_LLM_MODEL" "qwen3.6-35b-a3b" "$LLM_MODEL"
assert_eq "SELECTOR_GGUF_FILE" "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf" "$GGUF_FILE"
assert_eq "SELECTOR_POLICY" "context-aware-largest-capable-general-v1+spark-aarch64-nv-ultra-a3b-v1" "$MODEL_RECOMMENDATION_POLICY"
echo ""

# Strix Halo (SH_LARGE, AMD Ryzen AI MAX+ 395, 124 GB unified) hit the same
# coder-next pathology as Spark aarch64. The selector should substitute to
# 35B-A3B via the unified-memory policy (distinct tag from the Spark one).
echo "Catalog selector (amd unified SH_LARGE substitutes coder-next → A3B):"
_selector_env="$(python3 "$SCRIPT_DIR/scripts/select-model.py" \
    --catalog "$SCRIPT_DIR/config/model-library.json" \
    --backend amd \
    --memory-type unified \
    --vram-mb 0 \
    --ram-gb 124 \
    --profile qwen \
    --tier SH_LARGE \
    --host-arch amd64 \
    --installable-only \
    --env)"
LLM_MODEL="" GGUF_FILE="" MODEL_RECOMMENDATION_POLICY=""
load_selector_env "$_selector_env"
assert_eq "SELECTOR_LLM_MODEL" "qwen3.6-35b-a3b" "$LLM_MODEL"
assert_eq "SELECTOR_GGUF_FILE" "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf" "$GGUF_FILE"
assert_eq "SELECTOR_POLICY" "context-aware-largest-capable-general-v1+unified-memory-coder-next-a3b-v1" "$MODEL_RECOMMENDATION_POLICY"
echo ""

echo "Catalog selector (16GB Apple unified qwen keeps ranked fit):"
_selector_env="$(python3 "$SCRIPT_DIR/scripts/select-model.py" \
    --catalog "$SCRIPT_DIR/config/model-library.json" \
    --backend metal \
    --memory-type unified \
    --vram-mb 0 \
    --ram-gb 16 \
    --profile qwen \
    --tier 1 \
    --host-arch arm64 \
    --installable-only \
    --env)"
LLM_MODEL="" GGUF_FILE="" MODEL_RECOMMENDATION_SOURCE="" MODEL_RECOMMENDATION_POLICY=""
load_selector_env "$_selector_env"
assert_eq "SELECTOR_LLM_MODEL" "qwen3.5-9b" "$LLM_MODEL"
assert_eq "SELECTOR_GGUF_FILE" "Qwen3.5-9B-Q4_K_M.gguf" "$GGUF_FILE"
assert_eq "SELECTOR_SOURCE" "catalog_fit_pre_download" "$MODEL_RECOMMENDATION_SOURCE"
assert_eq "SELECTOR_POLICY" "context-aware-largest-capable-general-v1" "$MODEL_RECOMMENDATION_POLICY"
echo ""

echo "Catalog selector (32GB Apple unified qwen keeps ranked fit):"
_selector_env="$(python3 "$SCRIPT_DIR/scripts/select-model.py" \
    --catalog "$SCRIPT_DIR/config/model-library.json" \
    --backend metal \
    --memory-type unified \
    --vram-mb 0 \
    --ram-gb 32 \
    --profile qwen \
    --tier 2 \
    --host-arch arm64 \
    --installable-only \
    --env)"
LLM_MODEL="" GGUF_FILE="" MODEL_RECOMMENDATION_SOURCE="" MODEL_RECOMMENDATION_POLICY=""
load_selector_env "$_selector_env"
assert_eq "SELECTOR_LLM_MODEL" "phi-4" "$LLM_MODEL"
assert_eq "SELECTOR_GGUF_FILE" "phi-4-Q4_K_M.gguf" "$GGUF_FILE"
assert_eq "SELECTOR_SOURCE" "catalog_fit_pre_download" "$MODEL_RECOMMENDATION_SOURCE"
assert_eq "SELECTOR_POLICY" "context-aware-largest-capable-general-v1" "$MODEL_RECOMMENDATION_POLICY"
echo ""

echo "Catalog selector (--max-size-mb bounds tier 2 on 48GB Apple unified — issue #1881):"
_selector_env="$(python3 "$SCRIPT_DIR/scripts/select-model.py" \
    --catalog "$SCRIPT_DIR/config/model-library.json" \
    --backend apple \
    --memory-type unified \
    --vram-mb 0 \
    --ram-gb 48 \
    --profile qwen \
    --tier 2 \
    --max-size-mb 5760 \
    --host-arch arm64 \
    --installable-only \
    --env)"
LLM_MODEL="" GGUF_FILE="" MODEL_RECOMMENDATION_SOURCE="" MODEL_RECOMMENDATION_REASON=""
load_selector_env "$_selector_env"
assert_eq "SELECTOR_LLM_MODEL" "qwen3.5-9b" "$LLM_MODEL"
assert_eq "SELECTOR_GGUF_FILE" "Qwen3.5-9B-Q4_K_M.gguf" "$GGUF_FILE"
case "$MODEL_RECOMMENDATION_REASON" in
    *"Bounded by --tier 2's model size ceiling"*) echo "  PASS: SELECTOR_REASON mentions ceiling"; ((PASS++)) ;;
    *) echo "  FAIL: SELECTOR_REASON missing ceiling note (got '$MODEL_RECOMMENDATION_REASON')"; ((FAIL++)) ;;
esac
echo ""

echo "Catalog selector (--max-size-mb absent still auto-upgrades — back-compat):"
_selector_env="$(python3 "$SCRIPT_DIR/scripts/select-model.py" \
    --catalog "$SCRIPT_DIR/config/model-library.json" \
    --backend apple \
    --memory-type unified \
    --vram-mb 0 \
    --ram-gb 48 \
    --profile qwen \
    --tier 2 \
    --host-arch arm64 \
    --installable-only \
    --env)"
LLM_MODEL="" GGUF_FILE=""
load_selector_env "$_selector_env"
assert_eq "SELECTOR_LLM_MODEL" "qwen3.5-27b" "$LLM_MODEL"
echo ""

echo "Catalog selector (tiny --max-size-mb falls back to smallest fitting model, not smallest overall):"
_selector_env="$(python3 "$SCRIPT_DIR/scripts/select-model.py" \
    --catalog "$SCRIPT_DIR/config/model-library.json" \
    --backend apple \
    --memory-type unified \
    --vram-mb 0 \
    --ram-gb 48 \
    --profile qwen \
    --tier 0 \
    --max-size-mb 1500 \
    --host-arch arm64 \
    --installable-only \
    --env)"
LLM_MODEL="" GGUF_FILE=""
load_selector_env "$_selector_env"
assert_eq "SELECTOR_LLM_MODEL" "qwen3.5-2b" "$LLM_MODEL"
echo ""

echo "Catalog selector (amd unified SH_COMPACT uses ranked A3B without override):"
_selector_env="$(python3 "$SCRIPT_DIR/scripts/select-model.py" \
    --catalog "$SCRIPT_DIR/config/model-library.json" \
    --backend amd \
    --memory-type unified \
    --vram-mb 0 \
    --ram-gb 64 \
    --profile qwen \
    --tier SH_COMPACT \
    --host-arch amd64 \
    --installable-only \
    --env)"
LLM_MODEL="" GGUF_FILE="" MODEL_RECOMMENDATION_SOURCE="" MODEL_RECOMMENDATION_POLICY=""
load_selector_env "$_selector_env"
assert_eq "SELECTOR_LLM_MODEL" "qwen3.6-35b-a3b" "$LLM_MODEL"
assert_eq "SELECTOR_GGUF_FILE" "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf" "$GGUF_FILE"
assert_eq "SELECTOR_SOURCE" "catalog_fit_pre_download" "$MODEL_RECOMMENDATION_SOURCE"
assert_eq "SELECTOR_POLICY" "context-aware-largest-capable-general-v1" "$MODEL_RECOMMENDATION_POLICY"
echo ""

echo "Catalog selector (8GB NVIDIA qwen uses 64K catalog fit):"
_selector_env="$(python3 "$SCRIPT_DIR/scripts/select-model.py" \
    --catalog "$SCRIPT_DIR/config/model-library.json" \
    --backend nvidia \
    --memory-type discrete \
    --vram-mb 8188 \
    --ram-gb 31 \
    --profile qwen \
    --tier 1 \
    --host-arch amd64 \
    --installable-only \
    --env)"
LLM_MODEL="" GGUF_FILE="" MAX_CONTEXT="" MODEL_RUNTIME_PROFILE="" LLAMA_ARG_N_CPU_MOE="" LLAMA_ARG_CACHE_TYPE_V="" LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS=""
load_selector_env "$_selector_env"
assert_eq "SELECTOR_LLM_MODEL" "qwen3.5-9b" "$LLM_MODEL"
assert_eq "SELECTOR_GGUF_FILE" "Qwen3.5-9B-Q4_K_M.gguf" "$GGUF_FILE"
assert_eq "SELECTOR_CONTEXT" "65536" "$MAX_CONTEXT"
assert_eq "SELECTOR_RUNTIME_PROFILE" "" "$MODEL_RUNTIME_PROFILE"
assert_eq "SELECTOR_N_CPU_MOE" "" "$LLAMA_ARG_N_CPU_MOE"
assert_eq "SELECTOR_CACHE_V" "" "$LLAMA_ARG_CACHE_TYPE_V"
assert_eq "SELECTOR_CHECKPOINTS" "" "$LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS"
echo ""

echo "Catalog selector (8GB NVIDIA gemma uses upstream catalog fit):"
_selector_env="$(python3 "$SCRIPT_DIR/scripts/select-model.py" \
    --catalog "$SCRIPT_DIR/config/model-library.json" \
    --backend nvidia \
    --memory-type discrete \
    --vram-mb 8188 \
    --ram-gb 31 \
    --profile gemma4 \
    --tier 1 \
    --host-arch amd64 \
    --installable-only \
    --env)"
LLM_MODEL="" GGUF_FILE="" MAX_CONTEXT="" MODEL_RUNTIME_PROFILE="" LLAMA_ARG_N_CPU_MOE="" LLAMA_ARG_CACHE_TYPE_V=""
load_selector_env "$_selector_env"
assert_eq "SELECTOR_LLM_MODEL" "gemma-4-e4b-it" "$LLM_MODEL"
assert_eq "SELECTOR_GGUF_FILE" "gemma-4-E4B-it-Q4_K_M.gguf" "$GGUF_FILE"
assert_eq "SELECTOR_CONTEXT" "32768" "$MAX_CONTEXT"
assert_eq "SELECTOR_RUNTIME_PROFILE" "" "$MODEL_RUNTIME_PROFILE"
assert_eq "SELECTOR_N_CPU_MOE" "" "$LLAMA_ARG_N_CPU_MOE"
assert_eq "SELECTOR_CACHE_V" "" "$LLAMA_ARG_CACHE_TYPE_V"
echo ""

echo "Catalog selector excludes Hugging Face imports from a mixed catalog:"
_selector_catalog="$(mktemp)"
trap 'rm -f "$_selector_catalog"' EXIT
python3 - "$_selector_catalog" <<'PY'
import json
import sys

models = [
    {
        "id": "curated-model",
        "llm_model_name": "curated-model",
        "family": "qwen",
        "gguf_file": "curated.gguf",
        "gguf_url": "https://huggingface.co/ods/curated/resolve/main/curated.gguf",
        "size_mb": 1000,
        "vram_required_gb": 2,
        "context_length": 65536,
    },
    {
        "id": "imported-model",
        "source": "huggingface",
        "llm_model_name": "imported-model",
        "family": "qwen",
        "gguf_file": "imported.gguf",
        "gguf_url": "https://huggingface.co/community/imported/resolve/main/imported.gguf",
        "size_mb": 7000,
        "vram_required_gb": 7,
        "context_length": 131072,
    },
]
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump({"models": models}, handle)
PY
_selector_env="$(python3 "$SCRIPT_DIR/scripts/select-model.py" \
    --catalog "$_selector_catalog" \
    --backend nvidia \
    --memory-type discrete \
    --vram-mb 8192 \
    --ram-gb 32 \
    --profile qwen \
    --tier 1 \
    --host-arch amd64 \
    --installable-only \
    --env)"
rm -f "$_selector_catalog"
trap - EXIT
LLM_MODEL="" GGUF_FILE=""
load_selector_env "$_selector_env"
assert_eq "SELECTOR_CURATED_SOURCE" "curated-model" "$LLM_MODEL"
echo ""

# --- Summary ---
echo "==============================="
echo "Results: $PASS passed, $FAIL failed"
echo "==============================="

[[ $FAIL -eq 0 ]] && exit 0 || exit 1
