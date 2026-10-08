#!/usr/bin/env bash
# A missing Linux GPU device must not replace the model the Windows Portal's
# host-native llama-server serves (NATIVE_LLM_BASE_URL). The route survives
# the CPU fallback, the in-stack llama-server stays off, and the image plan
# agrees with the resolver. Retargets the external-Lemonade fallback test.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
SCRIPT_DIR="$ROOT_DIR"
LOG_FILE=/dev/null
ai_warn() { :; }
ai() { :; }
log() { :; }
source installers/lib/detection.sh
source installers/lib/native-llm.sh
source installers/lib/compose-select.sh

fail() { echo "[FAIL] $*" >&2; exit 1; }

# The WSL VM sees no AMD GPU devices while Windows serves the model.
ODS_MODE=local
GPU_BACKEND=amd
TIER=1
GPU_COUNT=1
CAP_COMPOSE_OVERLAYS=docker-compose.base.yml,docker-compose.amd.yml
NATIVE_LLM_BASE_URL=http://localhost:8080
apply_cpu_gpu_fallback 'AMD devices unavailable in the Linux container.'
[[ "$GPU_BACKEND" == cpu && "$ODS_MODE" == local && "$NATIVE_LLM_BASE_URL" == http://localhost:8080 ]] \
    || fail "the host-native route did not survive the CPU fallback"
export ODS_MODE GPU_BACKEND TIER GPU_COUNT CAP_COMPOSE_OVERLAYS NATIVE_LLM_BASE_URL
unset AMD_INFERENCE_BACKEND
resolve_compose_config
[[ "$COMPOSE_FLAGS" == *docker-compose.host-native-llm.yml* \
   && "$COMPOSE_FLAGS" != *docker-compose.cpu.yml* \
   && "$COMPOSE_FLAGS" != *docker-compose.amd.yml* ]] \
    || fail "the CPU fallback selected in-stack inference: $COMPOSE_FLAGS"

# The late service-start fallback runs against the .env the installer wrote.
# A second fallback must leave the Windows endpoint and model choice intact.
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
INSTALL_DIR="$tmp_dir"
cat > "$INSTALL_DIR/.env" <<'ENV'
GPU_BACKEND=amd
ODS_MODE=local
LLM_API_URL=http://litellm:4000
NATIVE_LLM_BASE_URL=http://localhost:8080
GGUF_FILE=keep-existing.gguf
ENV
source <(sed -n '/^    _phase11_env_set()/,/^    _phase11_allow_container_host_firewall()/ {
    /^    _phase11_allow_container_host_firewall()/d
    p
}' installers/phases/11-services.sh)
show_amd_gpu_device_guidance() { :; }
ai_ok() { :; }
TIER_FORCED=true
GPU_BACKEND=amd
_phase11_apply_cpu_fallback /dev/dri
grep -qx 'GPU_BACKEND=cpu' "$INSTALL_DIR/.env" || fail "the late fallback must record GPU_BACKEND=cpu"
grep -qx 'LLM_API_URL=http://litellm:4000' "$INSTALL_DIR/.env" || fail "the late fallback rewrote LLM_API_URL"
grep -qx 'NATIVE_LLM_BASE_URL=http://localhost:8080' "$INSTALL_DIR/.env" || fail "the late fallback dropped the native route"
grep -qx 'GGUF_FILE=keep-existing.gguf' "$INSTALL_DIR/.env" || fail "the late fallback replaced the served model"

docker_compose_mock() { printf 'model-router\nllama-server\n'; }
DOCKER_COMPOSE_CMD=docker_compose_mock
if ods_host_native_assert_no_managed_llama >/dev/null 2>&1; then
    fail "the host-native route accepted an enabled in-stack llama-server"
fi
docker_compose_mock() { printf 'model-router\nlitellm\n'; }
ods_host_native_assert_no_managed_llama

# Exercise the real Phase 08 guard, stopping at its next phase banner before
# any image pull. A fresh Pixel install has no ingress GID yet.
phase08_guard_probe() (
    local mode="$1" original_gid="${2:-unset}"
    unset PIXEL_INGRESS_GID COMPOSE_PROFILES
    if [[ "$original_gid" != unset ]]; then export PIXEL_INGRESS_GID="$original_gid"; fi
    export DRY_RUN=false GPU_BACKEND=cpu ODS_MODE=local NATIVE_LLM_BASE_URL=http://localhost:8080
    export ODS_GATEWAY_ONLY=false ENABLE_OPEN_WEBUI=true ENABLE_COMFYUI=false
    COMPOSE_FLAGS='-f docker-compose.yml'
    DOCKER_COMPOSE_CMD=early_compose_mock
    early_compose_mock() {
        [[ "$*" == '-f docker-compose.yml config --services' ]] || return 21
        [[ "${PIXEL_INGRESS_GID:-}" == "${original_gid/unset/1}" ]] || return 22
        [[ "$mode" != broken ]] || return 23
        printf 'model-router\npixel-edge\n'
        [[ "$mode" != unsafe ]] || printf 'llama-server\n'
    }
    ods_progress() { :; }
    ai_bad() { printf '%s\n' "$*" >&2; }
    show_phase() {
        [[ "${PIXEL_INGRESS_GID-unset}" == "$original_gid" ]] || exit 24
        exit 0
    }
    source installers/phases/08-images.sh
    echo '[FAIL] Phase 08 probe did not reach the image phase banner' >&2
    exit 25
)
phase08_guard_probe safe
phase08_guard_probe safe 4242
for failure in unsafe broken; do
    if phase08_guard_probe "$failure" >/dev/null 2>&1; then
        fail "Phase 08 accepted $failure host-native Compose"
    fi
done

# The image planner must agree with the resolver: no in-stack llama image.
phase08_images="$(
    export DRY_RUN=true GPU_BACKEND=cpu ODS_MODE=local NATIVE_LLM_BASE_URL=http://localhost:8080
    export ENABLE_COMFYUI=false ENABLE_VOICE=false ENABLE_WORKFLOWS=false
    export ENABLE_RAG=false ENABLE_QDRANT=false ENABLE_EMBEDDINGS=false
    export ENABLE_HERMES=false ENABLE_OPEN_WEBUI=false
    COMPOSE_FLAGS=''
    ods_progress() { :; }
    show_phase() { :; }
    bootline() { :; }
    signal() { :; }
    source installers/phases/08-images.sh
    printf '%s\n' "${PULL_LIST[@]}"
)"
if grep -q 'LLAMA-SERVER' <<< "$phase08_images"; then
    fail "the host-native route planned an in-stack llama image"
fi

# Without the native route, an AMD host with no usable devices runs llama.cpp
# on the CPU in the stack.
NATIVE_LLM_BASE_URL=
ODS_MODE=local
GPU_BACKEND=amd
apply_cpu_gpu_fallback 'AMD has no usable GPU devices.'
[[ "$ODS_MODE" == local && "$GPU_BACKEND" == cpu ]] || fail "managed AMD did not fall back to local CPU inference"

echo '[PASS] the host-native llama-server route survives the CPU fallback'
