#!/usr/bin/env bash
# The Linux installer's phases on the vLLM route (LLM_BACKEND=vllm), each run
# from the shipped phase code with no Docker daemon, GPU or network:
#
#   03  the LLM role is exactly VLLM_GPU_UUID; whisper, ComfyUI and
#       embeddings take the other GPUs; no split, no tensor split;
#   06  .env carries LLM_BACKEND=vllm and the VLLM_* keys, every URL that
#       named llama-server goes through LiteLLM or model-router as on the
#       host-native route, the switchboard is on, the model-router endpoint
#       is vllm-default rendered for LLM_MODEL, and every other route writes
#       no vLLM line;
#   08  the vLLM image is pulled instead of llama.cpp's; the stack check
#       refuses a stack without vllm-server or with llama-server;
#   11  the model download's progress and every downloader exit code, and a
#       rerun that moves to vLLM removes the old llama-server container;
#   12  the wait for vllm-server and the answer through model-router, with
#       calm progress and a cause, a next step and the Discord link on failure;
#   13  the summary names the engine and the model;
#   pixel  Pixel binds vLLM's served id and the catalog's thinking default.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASES="$ROOT_DIR/installers/phases"
PYTHON_CMD="${ODS_PYTHON_CMD:-python3}"
PASS=0
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
pass() { printf '[PASS] %s\n' "$*"; PASS=$((PASS + 1)); }
# ODS_TEST_SECTION=NN runs only that phase's section (a quick rerun while editing).
only() { [[ -z "${ODS_TEST_SECTION:-}" || "$ODS_TEST_SECTION" == "$1" ]]; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fakebin="$tmp/bin"
mkdir -p "$fakebin"
# No test here may reach a real Docker daemon.
printf '#!/usr/bin/env bash\necho "the test reached docker" >&2\nexit 97\n' > "$fakebin/docker"
chmod +x "$fakebin/docker"
export PATH="$fakebin:$PATH"

GB300=GPU-aaaaaaaa-gb300
RTX=GPU-bbbbbbbb-rtx6000
PINNED_IMAGE="$(jq -r '.entries[] | select(.id == "vllm.vllm-server") | .value' "$ROOT_DIR/config/dependency-lock.json")"

# ── 03: the vLLM engine owns exactly its GPU ──────────────────────────────
if only 03; then
phase03() {
    local name="$1" topology="$2" rc=0 dir="$tmp/p03-$1"
    shift 2
    mkdir -p "$dir/src/scripts" "$dir/home" "$dir/install"
    cp "$ROOT_DIR/scripts/assign_gpus.py" "$dir/src/scripts/"
    {
        printf '%s\n' 'set -euo pipefail'
        printf 'FEATURES_PHASE=%q\nSCRIPT_DIR=%q\nINSTALL_DIR=%q\nHOME=%q\n' \
            "$PHASES/03-features.sh" "$dir/src" "$dir/install" "$dir/home"
        printf 'GPU_TOPOLOGY_JSON=%q\n' "$topology"
        cat <<'EOF'
INTERACTIVE=false DRY_RUN=false TIER=NV_ULTRA ODS_MODE=local
ENABLE_VOICE=true ENABLE_WORKFLOWS=false ENABLE_RAG=true ENABLE_RECOMMENDED=false
ENABLE_HERMES=false ENABLE_PIXEL=false ENABLE_COMFYUI=true ENABLE_APE=false
ENABLE_PERPLEXICA=false ENABLE_PRIVACY_SHIELD=false ENABLE_LANGFUSE=false ENABLE_BRAVE_SEARCH=false
GPU_BACKEND=nvidia HOST_ARCH=arm64 HOST_PAGE_SIZE=4096 MAX_CONTEXT=131072 LLM_MODEL_SIZE_MB=132735
VERBOSE=false DEBUG=false AMB= BGRN= DIM= GRN= NC= RED= WHT=
GPU_COUNT="$(jq '.gpu_count' <<< "$GPU_TOPOLOGY_JSON")"
ods_progress() { :; }; show_phase() { :; }; show_install_menu() { :; }; ai() { :; }; ai_warn() { :; }
ai_bad() { :; }; log() { :; }; chapter() { :; }; bootline() { :; }; signal() { :; }
warn() { printf 'WARN %s\n' "$*"; }
success() { printf 'SUCCESS %s\n' "$*"; }
error() { printf 'ERROR %s\n' "$*" >&2; exit 1; }
nvidia-smi() { jq -r '.gpus[].uuid' <<< "$GPU_TOPOLOGY_JSON"; }
source "$(dirname "$FEATURES_PHASE")/../lib/installed-feature-state.sh"
EOF
        printf '%s\n' "$@"
        cat <<'EOF'
source "$FEATURES_PHASE"
printf 'LLAMA=%s\nWHISPER=%s\nCOMFYUI=%s\nEMBEDDINGS=%s\nSPLIT=%s\nTENSOR=%s\n' "${LLAMA_SERVER_GPU_UUIDS:-}" \
    "${WHISPER_GPU_UUID:-}" "${COMFYUI_GPU_UUID:-}" "${EMBEDDINGS_GPU_UUID:-}" "${LLAMA_ARG_SPLIT_MODE:-}" "${LLAMA_ARG_TENSOR_SPLIT:-}"
printf 'ASSIGNED=%s\n' "$(jq -c '.gpu_assignment.services.llama_server.gpus' <<< "$GPU_ASSIGNMENT_JSON")"
printf 'MAX_CONTEXT=%s\nBELOW_FLOOR=%s\n' "$MAX_CONTEXT" "${HERMES_CONTEXT_BELOW_FLOOR:-}"
EOF
    } > "$dir/run.bash"
    bash "$dir/run.bash" >"$dir/out" 2>"$dir/err" || rc=$?
    printf '%s\n' "$rc" > "$dir/rc"
}
p03() { sed -n "s/^$2=//p" "$tmp/p03-$1/out"; }
dgx_topology="$(cat <<EOF
{"vendor": "nvidia", "gpu_count": 2, "gpus": [
  {"index": 0, "uuid": "$GB300", "name": "NVIDIA GB300", "memory_gb": 250.7, "memory_free_gb": 250.0},
  {"index": 1, "uuid": "$RTX", "name": "NVIDIA RTX PRO 6000 Blackwell", "memory_gb": 95.6, "memory_free_gb": 95.0}],
 "links": [{"gpu_a": 0, "gpu_b": 1, "rank": 10, "link_type": "SYS", "link_label": "SYS"}]}
EOF
)"
phase03 dgx "$dgx_topology" 'LLM_BACKEND=vllm' "VLLM_GPU_UUID=$GB300"
[[ "$(cat "$tmp/p03-dgx/rc")" == 0 ]] || fail "phase 03 failed on the DGX Station: $(cat "$tmp/p03-dgx/err")"
[[ "$(p03 dgx LLAMA)" == "$GB300" && "$(p03 dgx ASSIGNED)" == "[\"$GB300\"]" ]] \
    || fail "the LLM role must be exactly the vLLM GPU: $(cat "$tmp/p03-dgx/out")"
[[ "$(p03 dgx WHISPER)" == "$RTX" && "$(p03 dgx COMFYUI)" == "$RTX" && "$(p03 dgx EMBEDDINGS)" == "$RTX" ]] \
    || fail "whisper, ComfyUI and embeddings must take the other GPU: $(cat "$tmp/p03-dgx/out")"
[[ "$(p03 dgx SPLIT)" == none && "$(p03 dgx TENSOR)" == 1 ]] || fail "the vLLM route must not split the model"
# The same host and model without the pin: llama.cpp would split it across both.
phase03 dgx-llama "$dgx_topology" 'LLM_BACKEND=llama-server'
[[ "$(p03 dgx-llama ASSIGNED)" == "[\"$GB300\",\"$RTX\"]" ]] \
    || fail "fixture check: the unpinned assignment was expected to span both GPUs: $(cat "$tmp/p03-dgx-llama/out" "$tmp/p03-dgx-llama/err")"
pass "phase 03 gives vLLM exactly its GPU and the other services the rest, where llama.cpp would split"

five="$(jq -c '.' <<'EOF'
{"vendor":"nvidia","gpu_count":5,"gpus":[
 {"index":0,"uuid":"GPU-0","name":"B200","memory_gb":180,"memory_free_gb":180},
 {"index":1,"uuid":"GPU-1","name":"B200","memory_gb":180,"memory_free_gb":180},
 {"index":2,"uuid":"GPU-2","name":"B200","memory_gb":180,"memory_free_gb":180},
 {"index":3,"uuid":"GPU-3","name":"B200","memory_gb":180,"memory_free_gb":180},
 {"index":4,"uuid":"GPU-4","name":"B200","memory_gb":180,"memory_free_gb":180}],
 "links":[{"gpu_a":0,"gpu_b":1,"rank":100,"link_type":"NV18","link_label":"NV18"},{"gpu_a":0,"gpu_b":2,"rank":100,"link_type":"NV18","link_label":"NV18"},
  {"gpu_a":0,"gpu_b":3,"rank":100,"link_type":"NV18","link_label":"NV18"},{"gpu_a":0,"gpu_b":4,"rank":100,"link_type":"NV18","link_label":"NV18"},
  {"gpu_a":1,"gpu_b":2,"rank":100,"link_type":"NV18","link_label":"NV18"},{"gpu_a":1,"gpu_b":3,"rank":100,"link_type":"NV18","link_label":"NV18"},
  {"gpu_a":1,"gpu_b":4,"rank":100,"link_type":"NV18","link_label":"NV18"},{"gpu_a":2,"gpu_b":3,"rank":100,"link_type":"NV18","link_label":"NV18"},
  {"gpu_a":2,"gpu_b":4,"rank":100,"link_type":"NV18","link_label":"NV18"},{"gpu_a":3,"gpu_b":4,"rank":100,"link_type":"NV18","link_label":"NV18"}]}
EOF
)"
phase03 five "$five" 'LLM_BACKEND=vllm' 'VLLM_GPU_UUID=GPU-2'
[[ "$(p03 five LLAMA)" == GPU-2 && "$(p03 five SPLIT)" == none ]] \
    || fail "spare GPUs must not join a pinned vLLM GPU: $(cat "$tmp/p03-five/out" "$tmp/p03-five/err")"
[[ "$(p03 five WHISPER)" != GPU-2 && "$(p03 five COMFYUI)" != GPU-2 && "$(p03 five EMBEDDINGS)" != GPU-2 ]] \
    || fail "with spare GPUs the other services must not share vLLM's GPU: $(cat "$tmp/p03-five/out")"
pass "on an NVLink host vLLM keeps one GPU and no spare GPU joins it"

mkdir -p "$tmp/p03-reuse/install"
saved_split="$(jq -nc --arg a "$GB300" --arg b "$RTX" '{gpu_assignment:{version:"1.0",strategy:"colocated",services:{
    llama_server:{gpus:[$a,$b],parallelism:{mode:"pipeline",tensor_parallel_size:1,pipeline_parallel_size:2,gpu_memory_utilization:0.95}},
    whisper:{gpus:[$b]},comfyui:{gpus:[$b]},embeddings:{gpus:[$b]}}}}')"
printf 'GPU_ASSIGNMENT_JSON_B64=%s\n' "$(printf '%s' "$saved_split" | base64 | tr -d '\n')" > "$tmp/p03-reuse/install/.env"
phase03 reuse "$dgx_topology" 'LLM_BACKEND=vllm' "VLLM_GPU_UUID=$GB300"
[[ "$(p03 reuse ASSIGNED)" == "[\"$GB300\"]" ]] && ! grep -q 'Reusing existing GPU assignment' "$tmp/p03-reuse/out" \
    || fail "a saved assignment that splits the LLM must not be reused for vLLM: $(cat "$tmp/p03-reuse/out")"
saved_pinned="$(jq -c --arg a "$GB300" '.gpu_assignment.services.llama_server = {gpus:[$a],parallelism:{mode:"none",tensor_parallel_size:1,pipeline_parallel_size:1,gpu_memory_utilization:0.95}}' <<< "$saved_split")"
mkdir -p "$tmp/p03-reuse2/install"
printf 'GPU_ASSIGNMENT_JSON_B64=%s\n' "$(printf '%s' "$saved_pinned" | base64 | tr -d '\n')" > "$tmp/p03-reuse2/install/.env"
phase03 reuse2 "$dgx_topology" 'LLM_BACKEND=vllm' "VLLM_GPU_UUID=$GB300"
grep -q 'SUCCESS Reusing existing GPU assignment from .env' "$tmp/p03-reuse2/out" && [[ "$(p03 reuse2 LLAMA)" == "$GB300" ]] \
    || fail "a saved assignment that already pins vLLM's GPU must be kept: $(cat "$tmp/p03-reuse2/out" "$tmp/p03-reuse2/err")"
pass "a saved GPU assignment is reused for vLLM only when it already gives the LLM exactly vLLM's GPU"

single="$(jq -nc --arg a "$GB300" '{vendor:"nvidia",gpu_count:1,gpus:[{index:0,uuid:$a,name:"NVIDIA GB300",memory_gb:250.7}],links:[]}')"
phase03 single "$single" 'LLM_BACKEND=vllm' "VLLM_GPU_UUID=$GB300"
[[ "$(cat "$tmp/p03-single/rc")" == 0 ]] && grep -q "ASSIGNED=\[\"$GB300\"\]" "$tmp/p03-single/out" \
    || fail "a single vLLM GPU must get the trivial assignment: $(cat "$tmp/p03-single/out" "$tmp/p03-single/err")"
phase03 single-mismatch "$single" 'LLM_BACKEND=vllm' 'VLLM_GPU_UUID=GPU-other'
[[ "$(cat "$tmp/p03-single-mismatch/rc")" != 0 ]] && grep -q 'The vLLM engine was planned for GPU GPU-other' "$tmp/p03-single-mismatch/err" \
    || fail "a single GPU that is not vLLM's must stop the install"
pass "a single-GPU vLLM host gets its one GPU, and a GPU mismatch stops the install"

# vLLM serves the context its --max-model-len fixes: the Hermes floor never
# raises it (llama.cpp's unverified fresh pick would be raised to 64K).
phase03 hermes-vllm "$single" 'LLM_BACKEND=vllm' "VLLM_GPU_UUID=$GB300" 'ENABLE_HERMES=true' 'MAX_CONTEXT=32768'
[[ "$(p03 hermes-vllm MAX_CONTEXT)" == 32768 && "$(p03 hermes-vllm BELOW_FLOOR)" == true ]] \
    || fail "the Hermes floor must not resize a vLLM model: $(cat "$tmp/p03-hermes-vllm/out" "$tmp/p03-hermes-vllm/err")"
phase03 hermes-llama "$single" 'LLM_BACKEND=llama-server' 'ENABLE_HERMES=true' 'MAX_CONTEXT=32768'
[[ "$(p03 hermes-llama MAX_CONTEXT)" == 65536 ]] || fail "fixture check: llama.cpp's fresh pick is raised to 64K"
pass "the Hermes 64K floor keeps a vLLM model's context instead of raising it"

fi

# ── 06: the .env the vLLM route writes ────────────────────────────────────
if only 06; then
# shellcheck source=lib/phase06-env-harness.sh
source "$ROOT_DIR/tests/lib/phase06-env-harness.sh"
PHASE06="$PHASES/06-directories.sh"
cat > "$tmp/vllm.fixture" <<EOF
TIER=NV_ULTRA
GPU_BACKEND=nvidia
GPU_COUNT=2
GPU_COMPUTE_CAP=10.3
RAM_GB=512
HOST_ARCH=arm64
ODS_MODE=local
LLM_BACKEND=vllm
export LLM_BACKEND
LLM_MODEL=qwen3.8-flash-next-nvfp4
GGUF_FILE=""
GGUF_URL=""
GGUF_SHA256=""
MAX_CONTEXT=131072
LLM_MODEL_SIZE_MB=132735
VLLM_IMAGE='$PINNED_IMAGE'
VLLM_MODEL_DIR='nvidia--Qwen3.8-Flash-Next-NVFP4@fc694b54'
VLLM_SERVED_MODEL=Qwen3.8-Flash-Next-NVFP4
VLLM_ARGS='--quantization modelopt --max-model-len 131072 --speculative-config.method=mtp'
VLLM_GPU_UUID=$GB300
LLAMA_SERVER_GPU_UUIDS=$GB300
LLAMA_ARG_SPLIT_MODE=none
LLAMA_ARG_TENSOR_SPLIT=1
EOF
render() {
    local name="$1" fixture="$2"
    mkdir -p "$tmp/p06-$name"
    ods_phase06_render_env "$PHASE06" "$ROOT_DIR" "$tmp/p06-$name" "$fixture" \
        || fail "phase 06 did not render the $name .env: $(cat "$tmp/p06-$name/render.err")"
}
envv() {
    python3 - "$tmp/p06-$1/.env" "$2" "$ROOT_DIR/extensions/services/dashboard-api" <<'PY'
import sys
sys.path.insert(0, sys.argv[3])
from env_values import parse_env_value
value = None
for line in open(sys.argv[1], encoding="utf-8").read().splitlines():
    key, separator, raw = line.partition("=")
    if separator and key == sys.argv[2]:
        value = parse_env_value(raw)
print("<absent>" if value is None else value)
PY
}
render vllm "$tmp/vllm.fixture"
for pair in "LLM_BACKEND=vllm" "VLLM_IMAGE=$PINNED_IMAGE" "VLLM_MODEL_DIR=nvidia--Qwen3.8-Flash-Next-NVFP4@fc694b54" \
    "VLLM_SERVED_MODEL=Qwen3.8-Flash-Next-NVFP4" "VLLM_GPU_UUID=$GB300" \
    "VLLM_ARGS=--quantization modelopt --max-model-len 131072 --speculative-config.method=mtp" \
    "LLM_API_URL=http://litellm:4000" "OPEN_WEBUI_LLM_BASE_URL=http://litellm:4000" \
    "HERMES_LLM_BASE_URL=http://model-router:9099/v1" "ODS_MODEL_SWITCHBOARD=enabled" \
    "ODS_TALK_VISION_URL=http://litellm:4000/v1" "GGUF_FILE=" "GGUF_URL=" "MAX_CONTEXT=131072" "CTX_SIZE=131072" \
    "LLAMA_SERVER_GPU_UUIDS=$GB300" "LLAMA_ARG_SPLIT_MODE=none" "SKIP_MODEL_DOWNLOAD=false" \
    "VLLM_GPU_MEMORY_UTILIZATION=<absent>"; do
    [[ "$(envv vllm "${pair%%=*}")" == "${pair#*=}" ]] || fail "the vLLM .env must hold ${pair}, got '$(envv vllm "${pair%%=*}")'"
done
[[ "$(envv vllm ODS_TALK_VISION_KEY)" == "$(envv vllm LITELLM_KEY)" \
    && "$(envv vllm OPEN_WEBUI_LLM_API_KEY)" == "$(envv vllm LITELLM_KEY)" ]] \
    || fail "Open WebUI and ODS Talk images must reach LiteLLM with its key"
if grep -E '^[A-Z_]+=.*llama-server:8080' "$tmp/p06-vllm/.env"; then
    fail "the vLLM .env must not point a service at the in-stack llama-server"
fi
[[ "$(grep -c '^VLLM_' "$tmp/p06-vllm/.env")" == 5 && "$(grep -c '^LLM_BACKEND=' "$tmp/p06-vllm/.env")" == 1 ]] \
    || fail "each vLLM key must appear once"
if grep -q 'WARN' "$tmp/p06-vllm/render.err"; then
    fail "a fresh vLLM install must not warn: $(cat "$tmp/p06-vllm/render.err")"
fi
bash "$ROOT_DIR/scripts/validate-env.sh" "$tmp/p06-vllm/.env" "$ROOT_DIR/.env.schema.json" >"$tmp/validate.out" 2>&1 \
    || fail "the vLLM .env must pass .env.schema.json: $(tail -20 "$tmp/validate.out")"
pass "phase 06 writes LLM_BACKEND=vllm, the VLLM_* keys and gateway URLs that pass the schema"

# A rerun over an installation that ran llama.cpp with the switchboard off.
mkdir -p "$tmp/p06-rerun"
cat > "$tmp/p06-rerun/.env" <<'EOF'
LLM_BACKEND=llama-server
ODS_MODEL_SWITCHBOARD=legacy
LLM_API_URL=http://llama-server:8080
OPEN_WEBUI_LLM_BASE_URL=
HERMES_LLM_BASE_URL=http://llama-server:8080/v1
HERMES_LLM_API_KEY=sk-ods-hermes-local
ODS_TALK_VISION_URL=http://llama-server:8080/v1
VLLM_GPU_MEMORY_UTILIZATION=0.85
LITELLM_KEY=sk-ods-kept-key
EOF
render rerun "$tmp/vllm.fixture"
for pair in "LLM_API_URL=http://litellm:4000" "OPEN_WEBUI_LLM_BASE_URL=http://litellm:4000" \
    "OPEN_WEBUI_LLM_API_KEY=sk-ods-kept-key" "HERMES_LLM_BASE_URL=http://model-router:9099/v1" \
    "HERMES_LLM_API_KEY=no-key" "ODS_MODEL_SWITCHBOARD=enabled" "ODS_TALK_VISION_URL=http://litellm:4000/v1" \
    "ODS_TALK_VISION_KEY=sk-ods-kept-key" "VLLM_GPU_MEMORY_UTILIZATION=0.85" "LLM_BACKEND=vllm"; do
    [[ "$(envv rerun "${pair%%=*}")" == "${pair#*=}" ]] || fail "the vLLM rerun must hold ${pair}, got '$(envv rerun "${pair%%=*}")'"
done
grep -q 'setting ODS_MODEL_SWITCHBOARD=enabled' "$tmp/p06-rerun/render.err" \
    || fail "turning the switchboard on must be said"
mkdir -p "$tmp/p06-owner"
printf '%s\n' 'ODS_TALK_VISION_URL=http://10.0.0.9:8000/v1' 'ODS_TALK_VISION_KEY=owner-key' > "$tmp/p06-owner/.env"
render owner "$tmp/vllm.fixture"
[[ "$(envv owner ODS_TALK_VISION_URL)" == http://10.0.0.9:8000/v1 && "$(envv owner ODS_TALK_VISION_KEY)" == owner-key ]] \
    || fail "an owner's own vision server must be kept"
pass "a rerun moves llama-server URLs to LiteLLM and model-router, turns the switchboard on and keeps owner settings"

# A newly picked model writes its own memory share (the selector always sends
# one), never the share an earlier model left in .env.
mkdir -p "$tmp/p06-pickshare"
printf '%s\n' 'LLM_BACKEND=vllm' 'VLLM_GPU_MEMORY_UTILIZATION=0.85' > "$tmp/p06-pickshare/.env"
{ cat "$tmp/vllm.fixture"; printf '%s\n' 'VLLM_GPU_MEMORY_UTILIZATION=0.5'; } > "$tmp/pickshare.fixture"
render pickshare "$tmp/pickshare.fixture"
[[ "$(envv pickshare VLLM_GPU_MEMORY_UTILIZATION)" == 0.5 \
    && "$(grep -c '^VLLM_GPU_MEMORY_UTILIZATION=' "$tmp/p06-pickshare/.env")" == 1 ]] \
    || fail "the pick's own share must replace an earlier model's, once: $(grep '^VLLM_GPU' "$tmp/p06-pickshare/.env")"
pass "a newly picked vLLM model writes its own memory share over an earlier model's"

mkdir -p "$tmp/p06-noimage"
grep -v '^VLLM_IMAGE=' "$tmp/vllm.fixture" > "$tmp/noimage.fixture"
! ods_phase06_render_env "$PHASE06" "$ROOT_DIR" "$tmp/p06-noimage" "$tmp/noimage.fixture" \
    && grep -q 'The vLLM engine needs VLLM_IMAGE' "$tmp/p06-noimage/render.err" \
    || fail "phase 06 must refuse to write a vLLM .env without VLLM_IMAGE"
pass "phase 06 refuses a vLLM .env with a missing VLLM_* value"

# Other routes: no vLLM line, and an exported llama-server writes the same bytes as none.
for route in \
    "llama|TIER=NV_ULTRA GPU_BACKEND=nvidia GPU_COUNT=1 RAM_GB=512 ODS_MODE=local LLM_MODEL=qwen3.6-35b-a3b GGUF_FILE=Q.gguf MAX_CONTEXT=65536" \
    "amd|TIER=SH_LARGE GPU_BACKEND=amd GPU_COUNT=1 RAM_GB=128 ODS_MODE=local LLM_MODEL=qwen3.6-35b-a3b GGUF_FILE=Q.gguf MAX_CONTEXT=65536" \
    "cloud|TIER=CLOUD GPU_BACKEND=cpu GPU_COUNT=0 RAM_GB=8 ODS_MODE=cloud LLM_MODEL=anthropic/claude-sonnet-4-6 GGUF_FILE= MAX_CONTEXT=200000" \
    "external|TIER=3 GPU_BACKEND=nvidia GPU_COUNT=1 RAM_GB=32 ODS_MODE=local LLM_MODEL=m GGUF_FILE=Q.gguf MAX_CONTEXT=65536 EXTERNAL_LLM_URL=http://127.0.0.1:11434 EXTERNAL_LLM_CONTAINER_URL=http://host.docker.internal:11434 EXTERNAL_LLM_PROVIDER=ollama EXTERNAL_LLM_MODEL=qwen3:32b"; do
    IFS='|' read -r name assignments <<< "$route"
    read -r -a items <<< "$assignments"
    printf '%s\n' "${items[@]}" > "$tmp/$name.fixture"
    { cat "$tmp/$name.fixture"; printf 'LLM_BACKEND=llama-server\nexport LLM_BACKEND\n'; } > "$tmp/$name-exported.fixture"
    render "$name" "$tmp/$name.fixture"
    render "$name-exported" "$tmp/$name-exported.fixture"
    cmp -s "$tmp/p06-$name/.env" "$tmp/p06-$name-exported/.env" \
        || fail "$name: an exported LLM_BACKEND=llama-server changed .env: $(diff "$tmp/p06-$name/.env" "$tmp/p06-$name-exported/.env")"
    if grep -E '^(VLLM_[A-Z_]+|ODS_TALK_VISION_[A-Z]+)=|vLLM engine' "$tmp/p06-$name/.env"; then
        fail "$name: a route without vLLM must write no vLLM line"
    fi
done
[[ "$(envv llama LLM_BACKEND)" == llama-server && "$(envv external LLM_BACKEND)" == external \
    && "$(envv llama LLM_API_URL)" == http://llama-server:8080 ]] || fail "the llama.cpp and API routes keep their values"
pass "every other route writes no vLLM line and the same bytes whether or not LLM_BACKEND is exported"

# Compose reads the exported LLM_BACKEND ahead of .env, so the two must agree.
[[ "$(cat "$tmp/p06-vllm/exported-llm-backend")" == vllm \
    && "$(cat "$tmp/p06-external-exported/exported-llm-backend")" == external \
    && "$(cat "$tmp/p06-llama-exported/exported-llm-backend")" == llama-server ]] \
    || fail "phase 06 must export the LLM_BACKEND it writes to .env"
cache_block="$(awk '/^    if \[\[ "\$\{LLM_BACKEND:-\}" == "vllm" \]\]; then$/ { emit = 1 } emit { print } emit && /^    fi$/ { exit }' "$PHASE06")"
[[ "$cache_block" == *'mkdir -p "$INSTALL_DIR"/data/vllm/cache'* ]] || fail "phase 06 must create data/vllm/cache"
( INSTALL_DIR="$tmp/cache-vllm" LLM_BACKEND=vllm; eval "$cache_block" )
( INSTALL_DIR="$tmp/cache-llama" LLM_BACKEND=llama-server; eval "$cache_block" )
[[ -d "$tmp/cache-vllm/data/vllm/cache" && ! -e "$tmp/cache-llama" ]] \
    || fail "data/vllm/cache must be created for the vLLM engine only"
pass "phase 06 exports the LLM_BACKEND it writes and creates data/vllm/cache only for vLLM"

# The runtime-config renders phase 06 runs: vllm-default only for vLLM.
router_args="$(awk '/^    _router_llm_backend=llama-server$/ { emit = 1 } /^    _router_surfaces=\(model-router-endpoints\)$/ { exit } emit { print }' "$PHASE06")"
[[ "$router_args" == *'--llm-backend "$_router_llm_backend"'* ]] || fail "phase 06 must pass --llm-backend to the router renders"
# router_arg ASSIGNMENTS FLAG: the value phase 06 passes the router renders for FLAG.
router_arg() (
    ODS_MODEL_SWITCHBOARD_VALUE=enabled _router_ods_mode=local GPU_BACKEND=nvidia GGUF_FILE=""
    _router_llm_base_url=http://llama-server:8080/v1 INSTALL_DIR="$tmp" NATIVE_LLM_ACTIVE=false
    LLM_MODEL=qwen3.8-flash-next-nvfp4
    eval "$1"
    eval "$router_args"
    printf '%s\n' "${_router_common_args[@]}" | sed -n "/^$2\$/{n;p;}"
)
[[ "$(router_arg 'LLM_BACKEND=vllm' --llm-backend)" == vllm && "$(router_arg 'LLM_BACKEND=llama-server' --llm-backend)" == llama-server \
    && "$(router_arg 'unset LLM_BACKEND' --llm-backend)" == llama-server ]] \
    || fail "phase 06 must render the router for vllm only on the vLLM route"
# The vllm-default endpoint carries the model's thinking map, read from the
# catalog entry --model names; every other route renders without --model.
[[ "$(router_arg 'LLM_BACKEND=vllm' --model)" == qwen3.8-flash-next-nvfp4 \
    && -z "$(router_arg 'LLM_BACKEND=llama-server' --model)" && -z "$(router_arg 'unset LLM_BACKEND' --model)" ]] \
    || fail "phase 06 must name LLM_MODEL with --model on the vLLM route only"
endpoints() {
    rm -rf "$tmp/render" && mkdir -p "$tmp/render"
    ODS_RENDER_LITELLM_KEY=sk-ods-test env -u LLM_BACKEND "$PYTHON_CMD" "$ROOT_DIR/scripts/render-runtime-configs.py" \
        --surface model-router-endpoints --switchboard-mode enabled --ods-mode local --gpu-backend nvidia \
        --gguf-file Q.gguf --llm-base-url http://llama-server:8080 --output-root "$tmp/render" --write "$@" >/dev/null
    cat "$tmp/render/config/model-router/endpoints.json"
}
[[ "$(endpoints)" == "$(endpoints --llm-backend llama-server)" ]] || fail "--llm-backend llama-server must render the endpoints as before"
vllm_endpoints="$(endpoints --llm-backend vllm --model qwen3.8-flash-next-nvfp4)"
grep -q '"id": "vllm-default"' <<< "$vllm_endpoints" && grep -q 'http://vllm-server:8000' <<< "$vllm_endpoints" \
    || fail "--llm-backend vllm must route model-router to vllm-server: $vllm_endpoints"
pass "model-router's endpoint is vllm-default, rendered for LLM_MODEL, on vLLM and unchanged elsewhere"

fi

# ── 08: images ─────────────────────────────────────────────────────────────
if only 08; then
pull_plan() (
    SCRIPT_DIR="$ROOT_DIR" INSTALL_DIR="$tmp/p08" LOG_FILE="$tmp/p08.log" DRY_RUN=true
    GPU_BACKEND=nvidia ODS_MODE=local EXTERNAL_LLM_URL="" COMPOSE_FLAGS="" ENABLE_VOICE=false
    ENABLE_WORKFLOWS=false ENABLE_RAG=false ENABLE_HERMES=false ENABLE_OPEN_WEBUI=true
    HOST_ARCH=arm64
    eval "$1"
    ods_progress() { :; }; show_phase() { :; }; ai() { :; }; ai_ok() { :; }; ai_warn() { :; }
    bootline() { :; }; signal() { :; }
    source "$ROOT_DIR/installers/lib/native-llm.sh"
    source "$PHASES/08-images.sh"
    printf '%s\n' "${PULL_LIST[@]}"
)
plan="$(pull_plan "LLM_BACKEND=vllm VLLM_IMAGE='$PINNED_IMAGE'")"
grep -qxF "$PINNED_IMAGE|VLLM — downloading the vLLM engine (NVIDIA)" <<< "$plan" \
    || fail "phase 08 must pull the pinned vLLM image: $plan"
! grep -q 'LLAMA-SERVER' <<< "$plan" || fail "phase 08 must not pull a llama.cpp image for vLLM: $plan"
grep -q 'LLAMA-SERVER' <<< "$(pull_plan 'LLM_BACKEND=llama-server')" || fail "the llama.cpp route still pulls llama-server"
pass "phase 08 pulls the vLLM image and no llama.cpp image on the vLLM route"

(
    source "$ROOT_DIR/installers/lib/compose-select.sh"
    INSTALL_DIR="$tmp"
    fake_compose() { printf '%s\n' $FAKE_SERVICES; }
    DOCKER_COMPOSE_CMD=fake_compose
    FAKE_SERVICES="dashboard model-router vllm-server litellm"
    ods_vllm_assert_engine_stack -f docker-compose.base.yml || exit 11
    FAKE_SERVICES="dashboard model-router vllm-server llama-server"
    ! ods_vllm_assert_engine_stack 2>"$tmp/assert.err" || exit 12
    grep -q 'also starts the in-stack llama-server' "$tmp/assert.err" || exit 13
    FAKE_SERVICES="dashboard llama-server"
    ! ods_vllm_assert_engine_stack 2>"$tmp/assert.err" || exit 14
    grep -q 'no vllm-server service' "$tmp/assert.err" || exit 15
) || fail "the vLLM stack check must refuse a stack with llama-server or without vllm-server (exit $?)"
for phase in 08-images.sh 11-services.sh; do
    grep -q 'ods_vllm_assert_engine_stack' "$PHASES/$phase" || fail "$phase must check the vLLM stack before using it"
done
pass "phases 08 and 11 check that the vLLM stack runs vllm-server and not llama-server"

fi

# ── 11: the vLLM model download ───────────────────────────────────────────
if only 11; then
download_fns="$(awk '
    /^    _phase11_vllm_download_status\(\) \{$/ { emit = 1 }
    emit { print }
    emit && /^    _phase11_download_vllm_model\(\) \{$/ { last = 1 }
    last && /^    }$/ { exit }
' "$PHASES/11-services.sh")"
[[ "$download_fns" == *'_phase11_download_vllm_model() {'* ]] || fail "phase 11's vLLM download was not found"
download() {
    local name="$1" exits="$2" rc=0 dir="$tmp/p11-$1"
    mkdir -p "$dir/install/scripts"
    cat > "$dir/install/scripts/download-hf-manifest.py" <<'PY'
import os
import sys
from pathlib import Path

state = Path(os.environ["FAKE_STATE"])
calls = int(state.read_text()) if state.exists() else 0
state.write_text(str(calls + 1))
codes = os.environ["FAKE_EXITS"].split(",")
code = int(codes[min(calls, len(codes) - 1)])
with open(os.environ["FAKE_ARGS"], "a", encoding="utf-8") as handle:
    handle.write(" ".join(sys.argv[1:]) + "\n")
print("plan: nvidia/Qwen3.8-Flash-Next-NVFP4@fc694b54, 25 files, 132.7 GB, into x", flush=True)
print("progress: 40% 53.1 of 132.7 GB", flush=True)
messages = {1: "the download stopped: timed out. Its bytes are kept: run this again to resume.",
            3: "model.safetensors does not match the catalog (sha256 1, expected 2), so its download was deleted.",
            4: "/models has 12.0 GB free, but this model needs 120.6 GB more plus a 2.1 GB safety margin."}
if code:
    print(f"error: {messages.get(code, 'unexpected')}\nNeed help? Ask on the ODS Discord: x", file=sys.stderr)
else:
    print("done: 25 files verified in x", flush=True)
sys.exit(code)
PY
    {
        printf '%s\n' 'set -euo pipefail'
        printf 'INSTALL_DIR=%q\nODS_PYTHON_CMD=%q\n' "$dir/install" "$PYTHON_CMD"
        printf 'export FAKE_STATE=%q FAKE_ARGS=%q FAKE_EXITS=%q\n' "$dir/state" "$dir/args" "$exits"
        cat <<'EOF'
LLM_MODEL=qwen3.8-flash-next-nvfp4 VLLM_SERVED_MODEL=Qwen3.8-Flash-Next-NVFP4
VLLM_MODEL_DIR='nvidia--Qwen3.8-Flash-Next-NVFP4@fc694b54' LLM_MODEL_SIZE_MB=132735
ODS_VLLM_DOWNLOAD_RETRY_DELAY_SECONDS=0
ai() { printf 'AI %s\n' "$*"; }
ai_bad() { printf 'BAD %s\n' "$*"; }
ui_status_line() { printf 'STATUS %s %s\n' "$1" "$2"; }
spin_task() { local rc=0; wait "$1" || rc=$?; printf 'SPIN %s | %s\n' "$2" "$("$5")"; return "$rc"; }
EOF
        printf '%s\n' "$download_fns"
        printf '%s\n' '_phase11_download_vllm_model'
    } > "$dir/run.bash"
    bash "$dir/run.bash" >"$dir/out" 2>"$dir/err" || rc=$?
    printf '%s\n' "$rc" > "$dir/rc"
}
download ok 0
[[ "$(cat "$tmp/p11-ok/rc")" == 0 ]] && grep -q '^STATUS ok Model downloaded and verified: Qwen3.8-Flash-Next-NVFP4$' "$tmp/p11-ok/out" \
    || fail "a verified download must be reported: $(cat "$tmp/p11-ok/out" "$tmp/p11-ok/err")"
# (Windows Python sees the same paths spelled C:/...; compare their ends.)
grep -Eqx -- '--catalog .*/p11-ok/install/config/model-library\.json --model-id qwen3\.8-flash-next-nvfp4 --models-dir .*/p11-ok/install/data/models' "$tmp/p11-ok/args" \
    || fail "the downloader must get the catalog, the model and data/models: $(cat "$tmp/p11-ok/args")"
grep -q '^SPIN Downloading Qwen3.8-Flash-Next-NVFP4 | 40% 53.1 of 132.7 GB$' "$tmp/p11-ok/out" \
    || fail "the download must show the downloader's progress: $(cat "$tmp/p11-ok/out")"
grep -q 'about 133 GB into data/models/nvidia--Qwen3.8-Flash-Next-NVFP4@fc694b54' "$tmp/p11-ok/out" \
    || fail "the download must say how big it is and where it goes"
if grep -qi 'fail\|error' "$tmp/p11-ok/out"; then
    fail "a successful download must not show error-looking text: $(cat "$tmp/p11-ok/out")"
fi
download resumed 1,0
[[ "$(cat "$tmp/p11-resumed/rc")" == 0 && "$(wc -l < "$tmp/p11-resumed/args" | tr -d ' ')" == 2 ]] \
    && grep -q 'resuming from the files kept so far (attempt 2 of 3)' "$tmp/p11-resumed/out" \
    || fail "a stopped download must resume: $(cat "$tmp/p11-resumed/out")"
if grep -qi 'fail\|error' "$tmp/p11-resumed/out"; then
    fail "a resumed download must stay calm: $(cat "$tmp/p11-resumed/out")"
fi
download stopped 1
[[ "$(cat "$tmp/p11-stopped/rc")" != 0 && "$(wc -l < "$tmp/p11-stopped/args" | tr -d ' ')" == 3 ]] \
    && grep -q '^BAD The download of Qwen3.8-Flash-Next-NVFP4 stopped before it finished: the download stopped: timed out' "$tmp/p11-stopped/out" \
    && grep -q 'Run the same install command again: it keeps the verified files and resumes' "$tmp/p11-stopped/out" \
    || fail "three stops must end with the cause and the resume step: $(cat "$tmp/p11-stopped/out")"
download mismatch 3
[[ "$(cat "$tmp/p11-mismatch/rc")" != 0 && "$(wc -l < "$tmp/p11-mismatch/args" | tr -d ' ')" == 1 ]] \
    && grep -q '^BAD A downloaded file of Qwen3.8-Flash-Next-NVFP4 did not match the catalog checksum' "$tmp/p11-mismatch/out" \
    || fail "a checksum mismatch must stop at once with its cause: $(cat "$tmp/p11-mismatch/out")"
download disk 4
[[ "$(cat "$tmp/p11-disk/rc")" != 0 ]] \
    && grep -q '^BAD There is not enough free disk space for Qwen3.8-Flash-Next-NVFP4: /models has 12.0 GB free' "$tmp/p11-disk/out" \
    && grep -q 'Free up space on that disk' "$tmp/p11-disk/out" \
    || fail "a full disk must say how much is free and what to do: $(cat "$tmp/p11-disk/out")"
for name in stopped mismatch disk; do
    grep -q '^AI Need help? Ask on the ODS Discord: https://discord.gg/4ntNp9MAwC$' "$tmp/p11-$name/out" \
        || fail "$name: a download failure must name the Discord help link"
done
grep -q 'vllm-model-download.log' "$tmp/p11-stopped/out" || fail "a download failure must name its log"
grep -q '_phase11_download_vllm_model || exit 1' "$PHASES/11-services.sh" || fail "phase 11 must stop on a failed vLLM download"
pass "phase 11 shows the vLLM download's progress, resumes a stop and explains exits 1, 3 and 4"

# A rerun that moves a llama.cpp installation to vLLM: the old llama-server
# container, which the vLLM overlay keeps behind a profile, goes first.
p11_helpers="$(sed -n '/^    _phase11_old_llama_containers() {$/,/^    }$/p; /^    _phase11_old_llama_stuck() {$/,/^    }$/p' "$PHASES/11-services.sh")"
p11_removal="$(awk '
    /^        # A rerun that moves a llama.cpp installation to the vLLM engine leaves$/ { print previous; emit = 1 }
    emit { print }
    emit && /^    fi$/ { exit }
    { previous = $0 }
' "$PHASES/11-services.sh")"
[[ "$p11_helpers" == *'_phase11_old_llama_stuck() {'* && "$p11_removal" == '    if _phase11_vllm_engine; then'* ]] \
    || fail "phase 11's removal of the old llama-server was not found"
removal_line="$(grep -n '^        # A rerun that moves a llama.cpp installation to the vLLM engine leaves$' "$PHASES/11-services.sh" | cut -d: -f1)"
launch_line="$(grep -n 'up -d --remove-orphans --no-build --pull never >> "$LOG_FILE" 2>&1 &$' "$PHASES/11-services.sh" | head -1 | cut -d: -f1)"
(( removal_line < launch_line )) || fail "the old llama-server must be removed before the containers launch"
remove_old() {
    local name="$1" rc=0 dir="$tmp/p11c-$1"
    shift
    mkdir -p "$dir"
    : > "$dir/calls"
    {
        printf '%s\n' 'set -euo pipefail'
        printf 'LOG_FILE=%q\nSTATE=%q\n' "$dir/install.log" "$dir"
        cat <<'EOF'
ai() { printf 'AI %s\n' "$*"; }
ai_ok() { printf 'OK %s\n' "$*"; }
ai_bad() { printf 'BAD %s\n' "$*"; }
fake_compose() {
    printf '%s\n' "$*" >> "$STATE/calls"
    case " $* " in
        *" ps -a -q llama-server "*) [[ ! -f "$STATE/container" ]] || cat "$STATE/container" ;;
        *" rm --stop --force llama-server "*)
            [[ "${FAKE_RM:-ok}" != fail ]] || return 1
            [[ "${FAKE_RM:-ok}" == keeps ]] || rm -f "$STATE/container"
            ;;
        *) return 97 ;;
    esac
}
DOCKER_COMPOSE_CMD=fake_compose
COMPOSE_FLAGS_ARR=(-f docker-compose.base.yml -f docker-compose.vllm.yml)
_phase11_vllm_engine() { [[ "$LLM_BACKEND" == vllm ]]; }
EOF
        printf '%s\n' "$@" "$p11_helpers" "$p11_removal" 'printf "LAUNCH\n"'
    } > "$dir/run.bash"
    bash "$dir/run.bash" > "$dir/out" 2>&1 || rc=$?
    printf '%s\n' "$rc" > "$dir/rc"
}
old_container='printf "%s\n" 0123abcd4567 > "$STATE/container"'
remove_old present 'LLM_BACKEND=vllm' "$old_container"
[[ "$(cat "$tmp/p11c-present/rc")" == 0 ]] && grep -qx 'LAUNCH' "$tmp/p11c-present/out" \
    && grep -qx -- '--profile local-inference -f docker-compose.base.yml -f docker-compose.vllm.yml rm --stop --force llama-server' \
        "$tmp/p11c-present/calls" \
    && grep -qx 'OK Removed the previous llama-server container; its models stay in data/models' "$tmp/p11c-present/out" \
    || fail "a rerun moving to vLLM must stop and remove the old llama-server: $(cat "$tmp/p11c-present/out" "$tmp/p11c-present/calls")"
remove_old absent 'LLM_BACKEND=vllm'
[[ "$(cat "$tmp/p11c-absent/rc")" == 0 ]] && grep -qx 'LAUNCH' "$tmp/p11c-absent/out" \
    && ! grep -q ' rm ' "$tmp/p11c-absent/calls" && ! grep -q '^OK' "$tmp/p11c-absent/out" \
    || fail "a fresh vLLM install has no llama-server to remove and says nothing: $(cat "$tmp/p11c-absent/out")"
remove_old rm-fails 'LLM_BACKEND=vllm' "$old_container" 'export FAKE_RM=fail'
remove_old rm-keeps 'LLM_BACKEND=vllm' "$old_container" 'export FAKE_RM=keeps'
for name in rm-fails rm-keeps; do
    [[ "$(cat "$tmp/p11c-$name/rc")" != 0 ]] && ! grep -qx 'LAUNCH' "$tmp/p11c-$name/out" \
        && grep -q '^BAD The previous llama-server container could not be removed' "$tmp/p11c-$name/out" \
        && grep -q 'docker rm -f ods-llama-server, then run the same install command again' "$tmp/p11c-$name/out" \
        && grep -q 'discord.gg' "$tmp/p11c-$name/out" \
        || fail "$name: a llama-server that stays must stop the install with what to do: $(cat "$tmp/p11c-$name/out")"
done
remove_old llama 'LLM_BACKEND=llama-server' "$old_container"
[[ "$(cat "$tmp/p11c-llama/rc")" == 0 && ! -s "$tmp/p11c-llama/calls" ]] \
    || fail "the llama.cpp route must leave its llama-server alone: $(cat "$tmp/p11c-llama/calls")"
pass "a rerun that moves to vLLM stops and removes the old llama-server before the launch, and stops when it stays"

fi

# ── 12: the wait for vllm-server and the model route ──────────────────────
if only 12; then
phase12_fns="$(awk '
    /^_phase12_env_get\(\) \{$/ || /^_phase12_curl_bearer\(\) \{$/ { emit = 1 }
    /^# The vLLM engine \(LLM_BACKEND=vllm\) runs in ods-vllm-server/ { emit = 1 }
    /^# Core service health checks/ { exit }
    emit { print }
    emit && /^}$/ && !vllm { emit = 0 }
    /^# The vLLM engine \(LLM_BACKEND=vllm\) runs in ods-vllm-server/ { vllm = 1 }
' "$PHASES/12-health.sh")"
[[ "$phase12_fns" == *'_phase12_verify_vllm_route() {'* && "$phase12_fns" == *'_phase12_curl_bearer() {'* ]] \
    || fail "phase 12's vLLM functions were not found"
cat > "$fakebin/fake-docker" <<'EOF'
#!/usr/bin/env bash
case "$1" in
    inspect)
        count=$(( $(cat "$FAKE_DOCKER_COUNT") + 1 ))
        printf '%s' "$count" > "$FAKE_DOCKER_COUNT"
        state="$(sed -n "${count}p" "$FAKE_DOCKER_STATES")"
        [[ -n "$state" ]] || state="$(tail -n 1 "$FAKE_DOCKER_STATES")"
        [[ "$state" == missing ]] && { echo "Error: No such object: ods-vllm-server" >&2; exit 1; }
        printf '%s\n' "$state"
        ;;
    logs) printf 'vLLM: torch.OutOfMemoryError: CUDA out of memory\n' ;;
    *) exit 97 ;;
esac
EOF
cat > "$fakebin/curl" <<'EOF'
#!/usr/bin/env bash
out="" header=""
while (( $# > 0 )); do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        -H) [[ "$2" == @* ]] && header="$(cat "${2#@}")"; shift 2 ;;
        -w|-X|-d|--max-time) shift 2 ;;
        *) shift ;;
    esac
done
printf '%s\n' "$header" >> "$FAKE_CURL_HEADERS"
count=$(( $(cat "$FAKE_CURL_COUNT") + 1 ))
printf '%s' "$count" > "$FAKE_CURL_COUNT"
reply="$(sed -n "${count}p" "$FAKE_CURL_REPLIES")"
[[ -n "$reply" ]] || reply="$(tail -n 1 "$FAKE_CURL_REPLIES")"
printf '%s' "${reply#*|}" > "$out"
printf '%s' "${reply%%|*}"
EOF
chmod +x "$fakebin/fake-docker" "$fakebin/curl"
health() {
    local name="$1" call="$2" rc=0 dir="$tmp/p12-$1"
    mkdir -p "$dir"
    cat > "$dir/states"
    {
        printf '%s\n' 'set -euo pipefail'
        printf 'INSTALL_DIR=%q\nLOG_FILE=%q\nDOCKER_CMD=%q\n' "$dir" "$dir/install.log" "$fakebin/fake-docker"
        printf 'export FAKE_DOCKER_STATES=%q FAKE_DOCKER_COUNT=%q\n' "$dir/states" "$dir/docker-count"
        printf 'export FAKE_CURL_REPLIES=%q FAKE_CURL_COUNT=%q FAKE_CURL_HEADERS=%q\n' "$dir/replies" "$dir/curl-count" "$dir/headers"
        printf 'ODS_PYTHON_CMD=%q\n' "$PYTHON_CMD"
        cat <<'EOF'
declare -A SERVICE_PORTS=([litellm]=4000)
LLM_BACKEND=vllm VLLM_SERVED_MODEL=Qwen3.8-Flash-Next-NVFP4 LITELLM_KEY=sk-ods-route-key
ODS_VLLM_POLL_SECONDS=0 ODS_VLLM_READY_TIMEOUT_SECONDS=3 ODS_VLLM_ROUTE_TIMEOUT_SECONDS=3
RED='' BGRN='' NC=''
ai() { printf 'AI %s\n' "$*"; }
ai_bad() { printf 'BAD %s\n' "$*"; }
ui_status_line() { printf 'STATUS %s %s\n' "$1" "$2"; }
spin_task() { local rc=0; wait "$1" || rc=$?; printf 'SPIN %s | %s\n' "$2" "$("$5")"; return "$rc"; }
EOF
        printf '%s\n' "$phase12_fns" "$call" 'printf "RETURNED\n"'
    } > "$dir/run.bash"
    : > "$dir/replies"
    printf '0' > "$dir/docker-count"
    printf '0' > "$dir/curl-count"
    [[ -z "${REPLIES:-}" ]] || printf '%b' "$REPLIES" > "$dir/replies"
    bash "$dir/run.bash" >"$dir/out" 2>"$dir/err" || rc=$?
    printf '%s\n' "$rc" > "$dir/rc"
}
p12_ok() { [[ "$(cat "$tmp/p12-$1/rc")" == 0 ]] && grep -q '^RETURNED$' "$tmp/p12-$1/out"; }
p12_stopped() {
    local name="$1" cause="$2"
    [[ "$(cat "$tmp/p12-$name/rc")" != 0 ]] && ! grep -q '^RETURNED$' "$tmp/p12-$name/out" \
        && grep -qF "BAD $cause" "$tmp/p12-$name/out" \
        && grep -q '^AI Need help? Ask on the ODS Discord: https://discord.gg/4ntNp9MAwC$' "$tmp/p12-$name/out" \
        || fail "$name: expected a stop with '$cause' and the Discord link: $(cat "$tmp/p12-$name/out" "$tmp/p12-$name/err")"
}

printf '%s\n' 'running 0 starting' 'running 0 starting' 'running 0 healthy' | health healthy _phase12_wait_for_vllm
p12_ok healthy && grep -q '^STATUS ok vLLM engine ready (Qwen3.8-Flash-Next-NVFP4)$' "$tmp/p12-healthy/out" \
    || fail "a healthy vllm-server must be reported ready: $(cat "$tmp/p12-healthy/out" "$tmp/p12-healthy/err")"
grep -q '^SPIN Loading Qwen3.8-Flash-Next-NVFP4 into GPU memory | a first start reads the weights and compiles kernels$' "$tmp/p12-healthy/out" \
    && grep -q 'compiles kernels before it answers; later starts are faster' "$tmp/p12-healthy/out" \
    || fail "the wait must explain the first start calmly: $(cat "$tmp/p12-healthy/out")"
if grep -qi 'fail\|error' "$tmp/p12-healthy/out"; then
    fail "waiting must not show error-looking text: $(cat "$tmp/p12-healthy/out")"
fi
printf '%s\n' 'running 3 starting' 'running 3 healthy' | health old-restarts _phase12_wait_for_vllm
p12_ok old-restarts || fail "restarts from before this wait must not count: $(cat "$tmp/p12-old-restarts/out")"
pass "phase 12 waits calmly for vllm-server's health check"

printf '%s\n' 'running 0 starting' 'exited 0 unhealthy' | health exited _phase12_wait_for_vllm
p12_stopped exited 'The vLLM container stopped before the model was ready (container exited).'
grep -q 'docker logs --tail 100 ods-vllm-server' "$tmp/p12-exited/out" \
    && grep -q 'CUDA out of memory' "$tmp/p12-exited/install.log" \
    || fail "a stopped engine must say how to read its log and save the log to the install log"
printf '%s\n' 'running 0 starting' 'restarting 1 starting' | health restarted _phase12_wait_for_vllm
p12_stopped restarted 'vLLM stopped while loading the model, and Docker restarted it.'
printf '%s\n' 'running 0 unhealthy' | health unhealthy _phase12_wait_for_vllm
p12_stopped unhealthy "vLLM's health check failed."
printf '%s\n' 'missing' | health missing _phase12_wait_for_vllm
p12_stopped missing 'The vLLM container stopped before the model was ready (container missing).'
printf '%s\n' 'running 0 starting' | health slow _phase12_wait_for_vllm
p12_stopped slow 'vLLM did not finish loading Qwen3.8-Flash-Next-NVFP4 within'
pass "phase 12 stops with the cause, the next step and the Discord link when vLLM exits, restarts, fails or is slow"

REPLIES='503|{"error":"no verified route"}\n503|{"error":"no verified route"}\n200|{"choices":[{"message":{"role":"assistant","content":"OK"}}]}\n'
printf 'running 0 healthy\n' | health route _phase12_verify_vllm_route
p12_ok route && grep -q '^STATUS ok Model route verified: ods/current answers through model-router (vLLM)$' "$tmp/p12-route/out" \
    || fail "an answer through model-router must verify the route: $(cat "$tmp/p12-route/out" "$tmp/p12-route/err")"
[[ "$(cat "$tmp/p12-route/curl-count")" == 3 ]] && grep -qx 'Authorization: Bearer sk-ods-route-key' "$tmp/p12-route/headers" \
    || fail "the route check must retry until an answer, with LiteLLM's key"
if grep -qi 'fail\|error' "$tmp/p12-route/out"; then
    fail "the route check must stay calm while it waits: $(cat "$tmp/p12-route/out")"
fi
REPLIES='200|{"choices":[{"message":{"role":"assistant","content":null,"reasoning_content":"The user wants OK."},"finish_reason":"length"}]}\n'
printf 'running 0 healthy\n' | health reasoning _phase12_verify_vllm_route
p12_ok reasoning || fail "a reasoning-only first answer still proves the route: $(cat "$tmp/p12-reasoning/out")"
REPLIES='200|{"choices":[{"message":{"role":"assistant","content":""}}]}\n'
printf 'running 0 healthy\n' | health empty _phase12_verify_vllm_route
p12_stopped empty 'vLLM is running, but Qwen3.8-Flash-Next-NVFP4 did not answer through LiteLLM and model-router within 0 minutes (last HTTP status 200).'
grep -q 'vLLM model route check, last reply (HTTP 200)' "$tmp/p12-empty/install.log" || fail "the last reply must be saved to the install log"
REPLIES='503|{"error":"no verified route"}\n'
printf 'running 0 healthy\n' | health no-route _phase12_verify_vllm_route
p12_stopped no-route 'vLLM is running, but Qwen3.8-Flash-Next-NVFP4 did not answer through LiteLLM and model-router'
grep -qF '(last HTTP status 503).' "$tmp/p12-no-route/out" || fail "a route that never answers must name its last HTTP status"
unset REPLIES
pass "phase 12 confirms the model answers through model-router, and explains a route that never answers"

# Phase 12's model-server dispatch and llama-server pre-warm, run with stubs.
dispatch_block="$(awk '/^if _phase12_external_llm; then$/ { emit = 1 } emit { print } emit && /^fi$/ { exit }' "$PHASES/12-health.sh")"
prewarm_block="$(awk '/^if \[\[ "\$\{ODS_MODE:-local\}" == "cloud" \]\] \|\| _phase12_host_native_llm \|\| _phase12_external_llm; then$/ { emit = 1 } emit { print } emit && /^fi$/ { exit }' "$PHASES/12-health.sh")"
[[ -n "$dispatch_block" && -n "$prewarm_block" ]] || fail "phase 12's model-server checks were not found"
dispatch() (
    exec 3>&1  # the pre-warm sends curl's output to /dev/null; the stub reports on fd 3
    ods_progress() { :; }; ai() { :; }; ai_ok() { :; }; ai_warn() { :; }; log() { :; }
    sr_container() { printf 'ods-x\n'; }
    _phase12_external_llm() { return 1; }
    _phase12_host_native_llm() { return 1; }
    _phase12_vllm_engine() { [[ "$route" == vllm ]]; }
    _phase12_wait_for_vllm() { printf 'WAIT_VLLM\n'; }
    _phase12_verify_vllm_route() { printf 'ROUTE_VLLM\n'; }
    _check_health() { printf 'CHECK %s\n' "$1"; }
    curl() { printf 'PREWARM\n' >&3; }
    declare -A SERVICE_PORTS=() SERVICE_HEALTH=()
    ODS_MODE=local INSTALL_DIR="$tmp/p12-dispatch" GGUF_FILE=Q.gguf LLM_MODEL=q route="$1"
    eval "$dispatch_block"
    eval "$prewarm_block"
)
[[ "$(dispatch vllm)" == "$(printf '%s\n' WAIT_VLLM ROUTE_VLLM)" ]] \
    || fail "the vLLM route must wait on vllm-server and its model route, not llama-server: $(dispatch vllm)"
[[ "$(dispatch llama)" == "$(printf '%s\n' 'CHECK llama-server' PREWARM)" ]] \
    || fail "the llama.cpp route must keep its llama-server wait and pre-warm: $(dispatch llama)"
pass "phase 12 waits on vllm-server and its route instead of llama-server on the vLLM route"

fi

# ── 13: the summary names the engine and the model ────────────────────────
if only 13; then
summary_api="$(awk '/^if \[\[ -n "\$\{EXTERNAL_LLM_URL:-\}" \|\| "\$\{ODS_MODE:-local\}" == "cloud"/ { emit = 1 } emit { print } emit && /^fi$/ { exit }' "$PHASES/13-summary.sh")"
summary_model="$(awk '/^if \[\[ "\$\{ODS_GATEWAY_ONLY:-false\}" == true \]\]; then$/ { emit = 1 } emit { print } emit && /^fi$/ { exit }' "$PHASES/13-summary.sh")"
summary_json="$(awk '/^if \[\[ -n "\$SUMMARY_JSON_FILE" \]\]; then$/ { emit = 1 } emit { print }' "$PHASES/13-summary.sh")"
[[ -n "$summary_api" && -n "$summary_model" && -n "$summary_json" ]] || fail "phase 13's summary blocks were not found"
shown="$(
    declare -A SERVICE_PORTS=([litellm]=4000 [llama-server]=11434)
    LLM_BACKEND=vllm VLLM_GPU_UUID="$GB300" VLLM_SERVED_MODEL=Qwen3.8-Flash-Next-NVFP4
    LLM_MODEL=qwen3.8-flash-next-nvfp4 MAX_CONTEXT=131072
    eval "$summary_api"
    eval "$summary_model"
)"
grep -qF '  • LLM API:       http://localhost:4000/v1  (LiteLLM gateway to the vLLM engine)' <<< "$shown" \
    && grep -qF "  • Engine: vLLM on GPU $GB300" <<< "$shown" \
    && grep -qF '  • Model: Qwen3.8-Flash-Next-NVFP4 (qwen3.8-flash-next-nvfp4, 131072 tokens of context)' <<< "$shown" \
    || fail "the summary must name the vLLM engine and its model: $shown"
shown="$(
    declare -A SERVICE_PORTS=([litellm]=4000 [llama-server]=11434)
    LLM_BACKEND=llama-server LLM_MODEL=qwen3.6-27b
    eval "$summary_api"
    eval "$summary_model"
)"
[[ "$shown" == "$(printf '%s\n' '  • LLM API:       http://localhost:11434/v1  (llama-server)' '  • Model: qwen3.6-27b')" ]] \
    || fail "the llama.cpp summary must stay as it was: $shown"
(
    SCRIPT_DIR="$ROOT_DIR" SUMMARY_JSON_FILE="$tmp/summary.json" VERSION=3.0.0 INSTALL_DIR="$tmp" TIER=NV_ULTRA
    TIER_NAME=Ultra GPU_BACKEND=nvidia LLM_MODEL=qwen3.8-flash-next-nvfp4 COMPOSE_FLAGS="" DRY_RUN=false
    PREFLIGHT_REPORT_FILE="" LLM_BACKEND=vllm
    eval "$summary_json" >/dev/null
)
[[ "$(jq -r '.runtime.backend_service' "$tmp/summary.json")" == vllm-server ]] \
    || fail "the summary JSON must name vllm-server as the model server"
pass "phase 13 names the vLLM engine, its GPU and its model, and the llama.cpp summary is unchanged"
fi

# ── pixel: the model the Portal binds and its thinking default ────────────
if only pixel; then
# pixel_case NAME [ASSIGNMENT...]: write Pixel's onboarding contract with the
# renderer replaced by a recorder; NAME/render-args holds its arguments.
pixel_case() {
    local name="$1" rc=0 dir="$tmp/pixel-$1"
    shift
    mkdir -p "$dir/home"
    {
        printf '%s\n' 'set -euo pipefail'
        printf 'ROOT_DIR=%q\nSTATE=%q\n' "$ROOT_DIR" "$dir"
        cat <<'EOF'
source "$ROOT_DIR/installers/lib/pixel-host-install.sh"
SCRIPT_DIR="$ROOT_DIR"
ai_bad() { printf 'BAD %s\n' "$*"; }
ods_pixel_run_as_owner() {
    shift 2
    case "$1" in
        python3) printf '%s\n' "$@" > "$STATE/render-args" ;;
        mktemp) printf '%s\n' "$STATE/relay-key" ;;
        tee) cat > "$STATE/relay-key" ;;
    esac
}
PIXEL_MODEL_RELAY_KEY=sk-ods-relay MAX_CONTEXT=131072 ODS_MODEL_SWITCHBOARD=enabled EXTERNAL_LLM_URL=""
EOF
        printf '%s\n' "$@"
        printf '%s\n' '_ods_pixel_write_onboarding owner "$STATE/home" "$STATE/home/onboarding.json" /usr/bin/openclaw /opt/ods/pixel-plugin digest'
    } > "$dir/run.bash"
    bash "$dir/run.bash" > "$dir/out" 2>&1 || rc=$?
    printf '%s\n' "$rc" > "$dir/rc"
}
# The renderer's arguments after python3: 2 renderer ... 6 model, 9 reasoning.
rendered() {
    [[ "$(cat "$tmp/pixel-$1/rc")" == 0 ]] || fail "$1: Pixel onboarding failed: $(cat "$tmp/pixel-$1/out")"
    sed -n "$2p" "$tmp/pixel-$1/render-args"
}
qwen_vllm='LLM_BACKEND=vllm LLM_MODEL=qwen3.8-flash-next-nvfp4 VLLM_SERVED_MODEL=Qwen3.8-Flash-Next-NVFP4 GGUF_FILE=""'
pixel_case vllm "$qwen_vllm"
[[ "$(rendered vllm 6)" == Qwen3.8-Flash-Next-NVFP4 && "$(rendered vllm 9)" == bootstrap ]] \
    || fail "on vLLM Pixel must bind the served id and start thinking on (medium): $(cat "$tmp/pixel-vllm/render-args")"
pixel_case vllm-llama-off "$qwen_vllm" 'export LLAMA_REASONING=off'
[[ "$(rendered vllm-llama-off 9)" == bootstrap ]] || fail "llama-server's LLAMA_REASONING must not decide vLLM's thinking"
pixel_case vllm-glm 'LLM_BACKEND=vllm LLM_MODEL=glm-5.3-flash-nvfp4 VLLM_SERVED_MODEL=GLM-5.3-Flash-NVFP4 GGUF_FILE=""'
[[ "$(rendered vllm-glm 6)" == GLM-5.3-Flash-NVFP4 && "$(rendered vllm-glm 9)" == bootstrap ]] \
    || fail "GLM-5.3 on vLLM must bind its served id with thinking on"
mkdir -p "$tmp/pixel-off-root/config"
python3 - "$ROOT_DIR/config/model-library.json" "$tmp/pixel-off-root/config/model-library.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
for model in data["models"]:
    if model.get("id") == "qwen3.8-flash-next-nvfp4-vllm":
        model["reasoning"] = "off"
json.dump(data, open(sys.argv[2], "w", encoding="utf-8"))
PY
pixel_case vllm-off "$qwen_vllm" "SCRIPT_DIR='$tmp/pixel-off-root'"
[[ "$(rendered vllm-off 9)" == default ]] || fail "a vLLM model whose thinking default is off must start with thinking off"
pixel_case vllm-unknown 'LLM_BACKEND=vllm LLM_MODEL=not-a-catalog-model VLLM_SERVED_MODEL=X GGUF_FILE=""'
[[ "$(cat "$tmp/pixel-vllm-unknown/rc")" != 0 && ! -e "$tmp/pixel-vllm-unknown/render-args" ]] \
    && grep -q '^BAD Pixel could not read the thinking default of not-a-catalog-model' "$tmp/pixel-vllm-unknown/out" \
    && grep -q 'discord.gg' "$tmp/pixel-vllm-unknown/out" \
    || fail "a vLLM model outside the catalog must stop Pixel's setup with what to do: $(cat "$tmp/pixel-vllm-unknown/out")"
pixel_case vllm-unnamed 'LLM_BACKEND=vllm LLM_MODEL=qwen3.8-flash-next-nvfp4 VLLM_SERVED_MODEL="" GGUF_FILE=""'
[[ "$(cat "$tmp/pixel-vllm-unnamed/rc")" != 0 && ! -e "$tmp/pixel-vllm-unnamed/render-args" ]] \
    || fail "Pixel must not bind a vLLM route without its served id"
pixel_case llama 'LLM_BACKEND=llama-server LLM_MODEL=qwen3.6-35b GGUF_FILE=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf'
[[ "$(rendered llama 6)" == Qwen3.6-35B-A3B-UD-Q4_K_M.gguf && "$(rendered llama 9)" == default ]] \
    || fail "llama.cpp must keep binding the GGUF name with the no-think default"
pixel_case llama-on 'LLM_BACKEND=llama-server LLM_MODEL=qwen3.6-35b GGUF_FILE=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf' \
    'export LLAMA_REASONING=on'
[[ "$(rendered llama-on 9)" == true ]] || fail "LLAMA_REASONING must still decide llama.cpp's thinking"
pass "Pixel binds vLLM's served id and starts from the catalog's thinking default; llama.cpp is unchanged"
fi

printf '\nResult: %d passed, 0 failed\n' "$PASS"
