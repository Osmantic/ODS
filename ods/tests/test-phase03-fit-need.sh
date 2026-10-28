#!/usr/bin/env bash
# Phase 03 hands assign_gpus.py the llama.cpp model's whole need at its
# context (the catalog's estimate: weights, KV cache, buffers), so a model that
# fits the largest GPU runs there alone instead of being split (owner rule,
# 2026-10-27). Without a catalog estimate (an import, the catalog turned off)
# phase 03 passes nothing and the assigner splits by the weights as before.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE03="$ROOT_DIR/installers/phases/03-features.sh"
ASSIGN="$ROOT_DIR/scripts/assign_gpus.py"
PASS=0
fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; PASS=$((PASS + 1)); }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fn="$(sed -n '/^_phase03_llm_fit_need_mb() {$/,/^}$/p' "$PHASE03")"
[[ -n "$fn" ]] || fail "phase 03 must define _phase03_llm_fit_need_mb"
python="$(SCRIPT_DIR="$ROOT_DIR"; . "$ROOT_DIR/installers/lib/model-selector.sh"; ods_model_selector_python)"
[[ -n "$python" ]] || fail "the model catalog needs Python"

need() {  # VAR=value... : run the helper in a clean shell with these inputs
    env -i PATH="$PATH" HOME="${HOME:-/tmp}" SCRIPT_DIR="$ROOT_DIR" LOG_FILE="$tmp/log" \
        GPU_BACKEND=nvidia GPU_MEMORY_TYPE=discrete GPU_VRAM=196608 RAM_GB=256 TIER=NV_ULTRA "$@" \
        bash -c 'set -euo pipefail; '"$fn"'; _phase03_llm_fit_need_mb'
}

# 1. A catalog model: the helper prints the catalog estimate in MiB.
model=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
got="$(need GGUF_FILE="$model" MAX_CONTEXT=65536)"
want="$("$python" "$ROOT_DIR/scripts/select-model.py" --catalog "$ROOT_DIR/config/model-library.json" \
    --backend nvidia --memory-type discrete --vram-mb 196608 --ram-gb 256 --tier NV_ULTRA \
    --check-fit --model-id "$model" --context 65536 \
    | "$python" -c 'import json, math, sys; print(math.ceil(json.load(sys.stdin)["required_gb"] * 1024))')" || true
want="${want%$'\r'}"   # a Windows Python ends the line with CRLF
[[ "$got" =~ ^[0-9]+$ && "$got" -gt 20000 && "$got" == "$want" ]] \
    || fail "the need must be the catalog's estimate in MiB (got '$got', want '$want')"
pass "a catalog model's need is its catalog estimate at the install's context ($got MiB)"

# 2. Nothing for a model the catalog does not list, or with the catalog off.
[[ -z "$(need GGUF_FILE=imported-model.gguf MAX_CONTEXT=65536)" ]] || fail "an unlisted model must have no need"
[[ -z "$(need GGUF_FILE="$model" MAX_CONTEXT=65536 ODS_DISABLE_CATALOG_MODEL_SELECTOR=true)" ]] \
    || fail "the catalog turned off must give no need"
pass "an import or a disabled catalog passes nothing, so the assigner splits as before"

# 3. The real assigner with that need: one 96 GB card holds the model; two
#    20 GB cards cannot, so they still share it.
topology() {  # MEMORY_GB
    cat > "$tmp/topo.json" <<EOF
{"vendor": "nvidia", "gpu_count": 2,
 "gpus": [{"index": 0, "uuid": "GPU-a", "name": "a", "memory_gb": $1},
          {"index": 1, "uuid": "GPU-b", "name": "b", "memory_gb": $1}],
 "links": [{"gpu_a": 0, "gpu_b": 1, "link_type": "PHB", "link_label": "PHB", "rank": 30}]}
EOF
}
llama_gpus() { "$python" "$ASSIGN" --topology "$tmp/topo.json" --model-size 22000 --fit-need-mb "$got" \
    | "$python" -c 'import json, sys; print(",".join(json.load(sys.stdin)["gpu_assignment"]["services"]["llama_server"]["gpus"]))'; }
topology 96
[[ "$(llama_gpus | tr -d '\r')" == GPU-a ]] || fail "a model that fits one 96 GB card must run there alone"
topology 20
[[ "$(llama_gpus | tr -d '\r')" == GPU-a,GPU-b ]] || fail "a model that does not fit one 20 GB card must still be split"
pass "with that need the assigner keeps the model on one card when it fits and splits it when it does not"

# 4. run_automatic passes the need to the assigner for llama.cpp only; the
#    vLLM engine's GPU is pinned and needs none.
auto="$(sed -n '/^run_automatic() {$/,/^}$/p' "$PHASE03")"
[[ "$auto" == *'[[ "${LLM_BACKEND:-}" == "vllm" ]] || fit_need_mb="$(_phase03_llm_fit_need_mb)"'* \
    && "$auto" == *'need_args=(--fit-need-mb "$fit_need_mb")'* \
    && "$auto" == *'${need_args[@]+"${need_args[@]}"}'* ]] \
    || fail "run_automatic must pass --fit-need-mb from the catalog for llama.cpp only"
pass "run_automatic passes the need for llama.cpp and never for the pinned vLLM GPU"

echo ""
echo "Result: $PASS passed, 0 failed"
