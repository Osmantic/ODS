#!/usr/bin/env bash
# Multi-GPU install: the llama-server GPU subset chosen in phase 03 must hold
# the selected model at the configured MAX_CONTEXT (weights + KV cache), not
# just the GGUF file. The selector sizes MAX_CONTEXT against the summed VRAM.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FEATURES_PHASE="$ROOT_DIR/installers/phases/03-features.sh"
SCRIPT_DIR="$ROOT_DIR"

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

command -v jq >/dev/null 2>&1 || { echo "[SKIP] jq is unavailable"; exit 0; }
command -v python3 >/dev/null 2>&1 || { echo "[SKIP] python3 is unavailable"; exit 0; }

grep -q -- '--model-size "$llama_need_mb"' "$FEATURES_PHASE" \
    || fail "phase 03 plans llama-server GPUs on the GGUF size only"
pass "phase 03 plans llama-server GPUs on its full memory requirement"

# shellcheck source=/dev/null
. "$ROOT_DIR/installers/lib/model-selector.sh"
# shellcheck disable=SC1090
eval "$(sed -n '/^llama_required_mb() {$/,/^}$/p' "$FEATURES_PHASE")"
declare -F llama_required_mb >/dev/null || fail "llama_required_mb is missing from phase 03"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
TOPOLOGY_FILE="$tmp_dir/topology.json"
LOG_FILE="$tmp_dir/log"

write_topology() {
    local count="$1"
    jq -n --argjson n "$count" '{
        vendor: "nvidia", gpu_count: $n,
        gpus: [range($n) | {index: ., name: "NVIDIA GeForce RTX 3090",
                            memory_gb: 24, uuid: "GPU-\(.)"}],
        links: [range($n) as $a | range($a + 1; $n) as $b
                | {gpu_a: $a, gpu_b: $b, rank: 10, link_type: "PHB", link_label: "PHB"}]
    }' > "$TOPOLOGY_FILE"
}

# select_for COUNT: load the catalog pick for COUNT x 24 GB into the env.
select_for() {
    GPU_BACKEND=nvidia GPU_MEMORY_TYPE=discrete RAM_GB=128 TIER=4 HOST_ARCH=amd64
    GPU_VRAM=$(( $1 * 24576 ))
    unset MODEL_RUNTIME_PROFILE
    local line
    while IFS= read -r line; do
        case "$line" in
            GGUF_FILE=*|LLM_MODEL=*|MAX_CONTEXT=*|LLM_MODEL_SIZE_MB=*|MODEL_RUNTIME_PROFILE=*)
                eval "$line" ;;
        esac
    done < <(ods_run_catalog_selector python3 0)
}

llama_vram_mb() {
    python3 "$ROOT_DIR/scripts/assign_gpus.py" --topology "$TOPOLOGY_FILE" --model-size "$1" \
        | jq '.gpu_assignment.services.llama_server.gpus | length * 24 * 1024'
}

for count in 1 2 3; do
    write_topology "$count"
    select_for "$count"
    need_mb="$(llama_required_mb)"
    required_mb=$(ods_catalog_fit_check python3 "$GGUF_FILE" "$MAX_CONTEXT" "${MODEL_RUNTIME_PROFILE:-}" \
        | jq '.required_gb * 1024 | ceil') || true
    (( need_mb >= LLM_MODEL_SIZE_MB )) || fail "${count}x24GB: planner size $need_mb below GGUF size"
    assigned_mb="$(llama_vram_mb "$need_mb")"
    total_mb=$(( count * 24576 ))
    if (( required_mb <= total_mb )); then
        (( assigned_mb >= required_mb )) \
            || fail "${count}x24GB: ${LLM_MODEL} at ${MAX_CONTEXT} needs ${required_mb}MB, llama-server got ${assigned_mb}MB"
    fi
    pass "${count}x24GB: ${LLM_MODEL} at ${MAX_CONTEXT} -> llama-server ${assigned_mb}MB (needs ${required_mb}MB)"
done

# Non-catalog (imported) model: keep the GGUF size contract.
write_topology 3
GGUF_FILE=not-in-catalog.gguf LLM_MODEL=imported LLM_MODEL_SIZE_MB=30000 MAX_CONTEXT=8192
[[ "$(llama_required_mb)" == "30000" ]] || fail "imported model must plan on LLM_MODEL_SIZE_MB"
pass "non-catalog model plans on LLM_MODEL_SIZE_MB"

# Occupied GPUs: the requirement must not shrink to the free VRAM, and the
# planner must reject a model that no longer fits.
write_occupied_topology() {
    jq -n '{vendor: "nvidia", gpu_count: 2,
        gpus: [range(2) | {index: ., name: "NVIDIA GeForce RTX 3090",
                           memory_gb: 24, memory_free_gb: 10, uuid: "GPU-\(.)"}],
        links: [{gpu_a: 0, gpu_b: 1, rank: 10, link_type: "PHB", link_label: "PHB"}]}' > "$TOPOLOGY_FILE"
}
write_occupied_topology
GGUF_FILE=not-in-catalog.gguf LLM_MODEL=imported LLM_MODEL_SIZE_MB=30000 MAX_CONTEXT=8192
[[ "$(llama_required_mb)" == "30000" ]]     || fail "occupied GPUs: imported model requirement was clamped to free VRAM"
if python3 "$ROOT_DIR/scripts/assign_gpus.py" --topology "$TOPOLOGY_FILE"         --model-size "$(llama_required_mb)" >/dev/null 2>&1; then
    fail "occupied GPUs: planner accepted 30000MB on 2x10GB free"
fi
pass "occupied GPUs: imported model keeps 30000MB and the planner rejects it"

select_for 2
write_occupied_topology
need_mb="$(llama_required_mb)"
(( need_mb >= LLM_MODEL_SIZE_MB )) || fail "occupied GPUs: catalog requirement $need_mb below GGUF size"
(( need_mb > 20480 )) || fail "occupied GPUs: catalog requirement $need_mb capped at free VRAM"
pass "occupied GPUs: catalog ${LLM_MODEL} keeps ${need_mb}MB requirement"

echo "[PASS] multi-GPU context fit"
