#!/usr/bin/env bash
# Image pre-pulls must match the selected model route and optional services.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

image_plan() (
    export SCRIPT_DIR="$ROOT_DIR" LOG_FILE=/dev/null DRY_RUN=true COMPOSE_FLAGS=''
    export GPU_BACKEND="$1" ODS_MODE="$2" EXTERNAL_LLM_URL="$3"
    export LEMONADE_EXTERNAL="$4" ENABLE_PERPLEXICA="$5" ENABLE_COMFYUI="$6"
    export ENABLE_OPEN_WEBUI="${7:-true}"
    export ENABLE_VOICE=false ENABLE_WORKFLOWS=false ENABLE_RAG=false
    export ENABLE_QDRANT=false ENABLE_EMBEDDINGS=false ENABLE_HERMES=false
    export ENABLE_OPENCLAW=false
    ods_progress() { :; }; show_phase() { :; }; ai() { :; }
    ai_ok() { :; }; ai_warn() { :; }; bootline() { :; }; signal() { :; }
    source "$ROOT_DIR/installers/phases/08-images.sh"
    printf '%s\n' "${PULL_LIST[@]}"
)

assert_image() {
    local plan="$1" label="$2" expected="$3" description="$4"
    if [[ "$expected" == present ]]; then
        [[ "$plan" == *"$label"* ]] || { echo "FAIL: $description is missing $label" >&2; exit 1; }
    else
        [[ "$plan" != *"$label"* ]] || { echo "FAIL: $description unexpectedly contains $label" >&2; exit 1; }
    fi
}

plan="$(image_plan nvidia local '' false false false)"
assert_image "$plan" 'LLAMA-SERVER' present 'local NVIDIA'
assert_image "$plan" 'PERPLEXICA' absent 'disabled Perplexica'
assert_image "$plan" 'OPEN WEBUI' present 'current core UI'

plan="$(image_plan nvidia local '' false true false)"
assert_image "$plan" 'PERPLEXICA' present 'selected Perplexica'

for mode in cloud external; do
    url=''
    [[ "$mode" == external ]] && url='http://host.docker.internal:11434'
    plan="$(image_plan nvidia "$mode" "$url" false false false)"
    assert_image "$plan" 'LLAMA-SERVER' absent "$mode NVIDIA"
    assert_image "$plan" 'LEMONADE — downloading the brain' absent "$mode NVIDIA"
done

plan="$(image_plan cpu local 'http://host.docker.internal:11434' false false false)"
assert_image "$plan" 'LLAMA-SERVER' absent 'external CPU'

plan="$(image_plan nvidia local 'http://127.0.0.1:18080' false false false false)"
assert_image "$plan" 'LLAMA-SERVER' absent 'gateway-only external route'
assert_image "$plan" 'OPEN WEBUI' absent 'gateway-only without UI'

plan="$(image_plan amd local '' false false false)"
assert_image "$plan" 'LEMONADE — downloading the brain' present 'managed AMD'

plan="$(image_plan intel local '' false false false)"
assert_image "$plan" 'LLAMA-SERVER — downloading the brain (Intel)' present 'managed Intel'
assert_image "$plan" 'server-cuda' absent 'managed Intel'

plan="$(image_plan sycl local '' false false false)"
assert_image "$plan" 'LLAMA-SERVER' absent 'local-build Arc'

plan="$(image_plan amd local '' true false true)"
assert_image "$plan" 'LEMONADE — downloading the brain' absent 'external Lemonade'
assert_image "$plan" 'COMFYUI' present 'separately selected AMD ComfyUI'

echo 'PASS: Phase 08 image plans follow the selected route and services'
