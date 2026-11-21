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

# 4. run_automatic itself (extracted from phase 03, with the assigner replaced
#    by a recorder) passes the need for llama.cpp and never for the vLLM
#    engine, whose GPU is pinned.
auto="$(sed -n '/^run_automatic() {$/,/^}$/p' "$PHASE03")"
[[ -n "$auto" ]] || fail "phase 03 must define run_automatic"
echo '{"gpu_assignment":{"strategy":"colocated","services":{"llama_server":{"gpus":["GPU-a"],"parallelism":{"mode":"none","tensor_parallel_size":1,"pipeline_parallel_size":1,"gpu_memory_utilization":0.95}}}}}' > "$tmp/canned.json"
run_auto() {  # LLM_BACKEND : prints the arguments the assigner received
    env -i PATH="$PATH" HOME="${HOME:-/tmp}" TMP_DIR="$tmp" LLM_BACKEND="$1" bash -c '
        set -euo pipefail
        chapter() { :; }; success() { :; }; log() { :; }; error() { echo "ERROR $*" >&2; exit 1; }; _show_json() { :; }
        _phase03_llm_fit_need_mb() { echo 23122; }
        python3() { printf "%s\n" "$@" > "$TMP_DIR/assign-args"; cat "$TMP_DIR/canned.json"; }
        ASSIGN_GPUS_SCRIPT=assign_gpus.py TOPOLOGY_FILE=topo.json LLM_MODEL_SIZE_MB=22000 MAX_CONTEXT=65536 VLLM_GPU_UUID=GPU-v
        GRN= NC= WHT= BGRN= AMB= RED= BLD= CYN= DIM=
        declare -a GPU_INDICES=(0); declare -A GPU_UUIDS=([0]=GPU-a)
        '"$auto"'
        run_automatic >/dev/null
        cat "$TMP_DIR/assign-args"'
}
llama_args="$(run_auto llama-server | tr '\n' ' ')"
[[ "$llama_args" == *"--fit-need-mb 23122"* && "$llama_args" != *"--pin-llm-gpu"* ]] \
    || fail "run_automatic must pass the need for llama.cpp: $llama_args"
vllm_args="$(run_auto vllm | tr '\n' ' ')"
[[ "$vllm_args" == *"--pin-llm-gpu GPU-v"* && "$vllm_args" != *"--fit-need-mb"* ]] \
    || fail "run_automatic must pin the vLLM GPU and pass no need: $vllm_args"
pass "run_automatic passes the need for llama.cpp and pins the vLLM GPU without one"

# 5. A saved assignment is reused only while its LLM GPUs still hold the
#    model's current need (a --reselect-model rerun, a raised context).
load_fn="$(sed -n '/^_decode_base64_portable() {$/,/^}$/p; /^_load_existing_gpu_assignment_json() {$/,/^}$/p' "$PHASE03")"
cat > "$tmp/topo.json" <<'EOF'
{"vendor": "nvidia", "gpu_count": 2,
 "gpus": [{"index": 0, "uuid": "GPU-a", "name": "a", "memory_gb": 24},
          {"index": 1, "uuid": "GPU-b", "name": "b", "memory_gb": 24}], "links": []}
EOF
mkdir -p "$tmp/install"
saved='{"gpu_assignment":{"strategy":"colocated","services":{"llama_server":{"gpus":["GPU-a"]},"whisper":{"gpus":["GPU-b"]}}}}'
printf 'GPU_ASSIGNMENT_JSON_B64=%s\n' "$(printf '%s' "$saved" | base64 | tr -d '\n')" > "$tmp/install/.env"
reuse() {  # NEED_MB : exit status of _load_existing_gpu_assignment_json
    env -i PATH="$PATH" HOME="${HOME:-/tmp}" NEED="$1" INSTALL_DIR="$tmp/install" TOPOLOGY_FILE="$tmp/topo.json" bash -c '
        set -euo pipefail
        log() { :; }
        _phase03_llm_fit_need_mb() { echo "$NEED"; }
        '"$load_fn"'
        _load_existing_gpu_assignment_json >/dev/null'
}
reuse 20000 || fail "a saved one-GPU assignment that still holds the model must be reused"
! reuse 30000 || fail "a saved one-GPU assignment below the model's need must be assigned again"
pass "a saved assignment is reused only while its LLM GPUs hold the model's need"

# 6. ods gpu reassign --auto asks the catalog the same way and passes the need.
cli_block="$(sed -n '/^    local fit_need_mb="" selector_python=""$/,/--enabled-services "\$enabled_services" \\$/p' "$ROOT_DIR/ods-cli")"
[[ "$cli_block" == *'ods_llm_fit_need_mb "$selector_python"'* && "$cli_block" == *'"${LLM_BACKEND:-}" != "vllm"'* ]] \
    && grep -qF '${need_args[@]+"${need_args[@]}"}) || error "GPU assignment failed"' "$ROOT_DIR/ods-cli" \
    || fail "ods gpu reassign must pass the catalog's need to the assigner"
# Sourcing model-selector.sh fires the function's RETURN trap, which deletes
# the temporary topology: the need must be computed before that trap is set.
need_line=$(grep -n 'ods_llm_fit_need_mb "\$selector_python"' "$ROOT_DIR/ods-cli" | head -1 | cut -d: -f1)
trap_line=$(awk -v n="$need_line" 'NR > n && /trap "rm -rf .\$tmpdir." RETURN/ {print NR; exit}' "$ROOT_DIR/ods-cli")
[[ -n "$need_line" && -n "$trap_line" ]] \
    || fail "ods gpu reassign must ask the catalog before it sets its RETURN trap (need line ${need_line:-none}, trap after it: ${trap_line:-none})"
pass "ods gpu reassign --auto passes the same catalog need, computed before its RETURN trap"

echo ""
echo "Result: $PASS passed, 0 failed"
