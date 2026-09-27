#!/usr/bin/env bash
# A model activated from the Dashboard may run on one of the llama GPUs
# (host agent: LLAMA_ARG_SPLIT_MODE=none + LLAMA_ARG_MAIN_GPU). An installer
# rerun that preserves that model (phase 02) and reuses the persisted GPU
# assignment (phase 03) must keep the one-GPU placement instead of resetting
# it to the assignment's layer split: on llama.cpp before b10247 a Gemma 4
# E2B/E4B layer split aborts at load. Everything else plans from the
# assignment as before.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FEATURES_PHASE="$ROOT_DIR/installers/phases/03-features.sh"
DIRECTORIES_PHASE="$ROOT_DIR/installers/phases/06-directories.sh"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

command -v jq >/dev/null 2>&1 || { echo "[SKIP] jq unavailable"; exit 0; }
command -v python3 >/dev/null 2>&1 || { echo "[SKIP] python3 unavailable"; exit 0; }

# Phase 06 writes the kept placement into .env (only when there is one).
grep -Fq 'then echo "LLAMA_ARG_MAIN_GPU=${LLAMA_ARG_MAIN_GPU}"; fi)' "$DIRECTORIES_PHASE" \
    || fail "phase 06 must write LLAMA_ARG_MAIN_GPU when phase 03 keeps a one-GPU placement"
pass "phase 06 writes a kept LLAMA_ARG_MAIN_GPU"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
mkdir -p "$tmp_dir/scripts"
cp "$ROOT_DIR/scripts/assign_gpus.py" "$tmp_dir/scripts/assign_gpus.py"

# tower2: two RTX PRO 6000; llama-server on both, auxiliary services on GPU 1.
assignment_b64="$(printf '%s' '{"gpu_assignment":{"version":"1.0","strategy":"colocated","services":{"whisper":{"gpus":["GPU-bbbb"],"gpu_indices":[1]},"comfyui":{"gpus":["GPU-bbbb"],"gpu_indices":[1]},"embeddings":{"gpus":["GPU-bbbb"],"gpu_indices":[1]},"llama_server":{"gpus":["GPU-aaaa","GPU-bbbb"],"gpu_indices":[0,1],"parallelism":{"mode":"pipeline","tensor_parallel_size":1,"pipeline_parallel_size":2,"gpu_memory_utilization":0.95}}}}}' | base64 | tr -d '\n')"

run_phase() {
    # $1 = existing .env assignment (b64 or empty), $2 = preserved split mode,
    # $3 = preserved main GPU (empty: unset).
    local install="$tmp_dir/install-$RANDOM"
    mkdir -p "$install"
    if [[ -n "$1" ]]; then
        printf 'GPU_ASSIGNMENT_JSON_B64=%s\n' "$1" >"$install/.env"
    fi
    HARNESS_TMP="$tmp_dir" HARNESS_INSTALL="$install" \
        HARNESS_SPLIT="$2" HARNESS_MAIN_GPU="$3" \
        bash -c '
set -euo pipefail
INTERACTIVE=false
DRY_RUN=false
INSTALL_CHOICE=1
TIER=NV_ULTRA
ODS_MODE=local
ENABLE_VOICE=false
ENABLE_WORKFLOWS=false
ENABLE_RAG=false
ENABLE_RECOMMENDED=false
ENABLE_HERMES=false
ENABLE_OPENCLAW=false
ENABLE_COMFYUI=false
ENABLE_APE=false
ENABLE_PERPLEXICA=false
ENABLE_PRIVACY_SHIELD=false
ENABLE_LANGFUSE=false
ENABLE_BRAVE_SEARCH=false
GPU_COUNT=2
GPU_BACKEND=nvidia
HOST_ARCH=amd64
HOST_PAGE_SIZE=4096
INSTALL_DIR="$HARNESS_INSTALL"
SCRIPT_DIR="$HARNESS_TMP"
LLM_MODEL_SIZE_MB=2963
MAX_CONTEXT=65536
VERBOSE=false
DEBUG=false
AMB=
BGRN=
DIM=
GRN=
NC=
RED=
WHT=
GPU_TOPOLOGY_JSON='"'"'{
  "vendor": "nvidia",
  "gpu_count": 2,
  "gpus": [
    {"index": 0, "uuid": "GPU-aaaa", "name": "RTX PRO 6000", "memory_gb": 95.6, "memory_free_gb": 94.6},
    {"index": 1, "uuid": "GPU-bbbb", "name": "RTX PRO 6000", "memory_gb": 95.6, "memory_free_gb": 95}
  ],
  "links": [
    {"gpu_a": 0, "gpu_b": 1, "rank": 20, "link_type": "NODE", "link_label": "SameNUMA-NoBridge"}
  ]
}'"'"'
# Phase 02 output for a preserved Dashboard model.
LLAMA_ARG_SPLIT_MODE="$HARNESS_SPLIT"
if [[ -n "$HARNESS_MAIN_GPU" ]]; then LLAMA_ARG_MAIN_GPU="$HARNESS_MAIN_GPU"; else unset LLAMA_ARG_MAIN_GPU; fi

ods_progress() { :; }
show_phase() { :; }
show_install_menu() { :; }
ai_warn() { :; }
log() { printf "LOG: %s\n" "$*"; }
warn() { printf "WARN: %s\n" "$*"; }
success() { printf "SUCCESS: %s\n" "$*"; }
chapter() { :; }
bootline() { :; }
signal() { :; }
get_rank() { printf "20\n"; }
error() { printf "ERROR: %s\n" "$*" >&2; return 1; }

# shellcheck source=/dev/null
source "$1"
printf "SPLIT=%s\n" "$LLAMA_ARG_SPLIT_MODE"
printf "TENSOR=%s\n" "$LLAMA_ARG_TENSOR_SPLIT"
printf "MAIN=%s\n" "${LLAMA_ARG_MAIN_GPU-<unset>}"
echo PHASE03_COMPLETED
' _ "$FEATURES_PHASE"
}

expect() {
    local name="$1" out="$2" split="$3" main="$4"
    grep -q 'PHASE03_COMPLETED' <<<"$out" || { echo "$out" >&2; fail "$name: phase 03 did not complete"; }
    grep -qx "SPLIT=$split" <<<"$out" || { echo "$out" >&2; fail "$name: expected LLAMA_ARG_SPLIT_MODE=$split"; }
    grep -qx "TENSOR=1,1" <<<"$out" || { echo "$out" >&2; fail "$name: the assignment's tensor split must stay 1,1"; }
    grep -qx "MAIN=$main" <<<"$out" || { echo "$out" >&2; fail "$name: expected LLAMA_ARG_MAIN_GPU=$main"; }
}

out="$(run_phase "$assignment_b64" none 0)" || { echo "$out" >&2; fail "kept placement run failed"; }
expect "reused assignment + preserved one-GPU placement" "$out" none 0
grep -q 'Reusing existing GPU assignment from .env' <<<"$out" || fail "harness did not reuse the assignment"
pass "a rerun that reuses the assignment keeps the preserved one-GPU placement"

out="$(run_phase "$assignment_b64" none 1)" || { echo "$out" >&2; fail "main GPU 1 run failed"; }
expect "preserved main GPU 1" "$out" none 1
pass "the preserved main GPU index is kept as-is"

out="$(run_phase "$assignment_b64" layer "")" || { echo "$out" >&2; fail "layer run failed"; }
expect "preserved layer split" "$out" layer "<unset>"
pass "a preserved layer split (Qwen3-Coder-Next on tower2) stays a layer split"

out="$(run_phase "$assignment_b64" none 2)" || { echo "$out" >&2; fail "out-of-range run failed"; }
expect "main GPU outside the llama set" "$out" layer "<unset>"
out="$(run_phase "$assignment_b64" none 01)" || { echo "$out" >&2; fail "malformed run failed"; }
expect "malformed main GPU" "$out" layer "<unset>"
out="$(run_phase "$assignment_b64" none "")" || { echo "$out" >&2; fail "missing main GPU run failed"; }
expect "split none without a main GPU" "$out" layer "<unset>"
pass "an invalid or missing preserved main GPU falls back to the assignment's split"

out="$(run_phase "" none 0)" || { echo "$out" >&2; fail "fresh assignment run failed"; }
expect "fresh assignment" "$out" layer "<unset>"
pass "a freshly planned assignment ignores the preserved placement"
