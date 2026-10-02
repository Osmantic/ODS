#!/usr/bin/env bash
# A missing Linux GPU device must not replace a Windows-owned Lemonade model.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
SCRIPT_DIR="$ROOT_DIR"
LOG_FILE=/dev/null
ai_warn() { :; }
ai() { :; }
log() { :; }
source installers/lib/detection.sh
source installers/lib/compose-select.sh

assert_external_route() {
    local selector="$1"
    ODS_MODE=lemonade
    GPU_BACKEND=amd
    TIER=1
    GPU_COUNT=1
    CAP_COMPOSE_OVERLAYS=docker-compose.base.yml,docker-compose.amd.yml
    LEMONADE_EXTERNAL=false
    AMD_INFERENCE_RUNTIME=
    AMD_INFERENCE_MANAGED=
    if [[ "$selector" == explicit ]]; then
        LEMONADE_EXTERNAL=true
    else
        AMD_INFERENCE_RUNTIME=lemonade
        AMD_INFERENCE_MANAGED=false
    fi

    apply_cpu_gpu_fallback 'AMD devices unavailable in the Linux container.'
    [[ "$GPU_BACKEND" == cpu && "$ODS_MODE" == lemonade ]] || {
        echo "[FAIL] $selector external Lemonade lost its route during CPU fallback" >&2
        return 1
    }
    export ODS_MODE GPU_BACKEND TIER GPU_COUNT CAP_COMPOSE_OVERLAYS
    export LEMONADE_EXTERNAL AMD_INFERENCE_RUNTIME AMD_INFERENCE_MANAGED
    resolve_compose_config
    [[ "$COMPOSE_FLAGS" == *docker-compose.lemonade-external.yml* \
       && "$COMPOSE_FLAGS" != *docker-compose.cpu.yml* ]] || {
        echo "[FAIL] $selector external Lemonade selected managed CPU inference: $COMPOSE_FLAGS" >&2
        return 1
    }
}

assert_external_route explicit
assert_external_route runtime

# Exercise the late service-start fallback against a real .env file. The
# installer writes that file before Phase 11, so a second fallback must leave
# the Windows model endpoint and model choice intact.
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
INSTALL_DIR="$tmp_dir"
cat > "$INSTALL_DIR/.env" <<'ENV'
GPU_BACKEND=amd
ODS_MODE=lemonade
LLM_API_URL=http://host.docker.internal:13305/api/v1
LEMONADE_MODEL=Qwen3.6-35B-A3B
GGUF_FILE=keep-existing.gguf
ENV
source <(sed -n '/^    _phase11_env_set()/,/^    _phase11_allow_container_host_firewall()/ {
    /^    _phase11_allow_container_host_firewall()/d
    p
}' installers/phases/11-services.sh)
show_amd_gpu_device_guidance() { :; }
ai_ok() { :; }
TIER_FORCED=true
ODS_MODE=lemonade
GPU_BACKEND=amd
LEMONADE_EXTERNAL=true
AMD_INFERENCE_RUNTIME=lemonade
AMD_INFERENCE_MANAGED=false
_phase11_apply_cpu_fallback kfd
grep -qx 'GPU_BACKEND=cpu' "$INSTALL_DIR/.env"
grep -qx 'ODS_MODE=lemonade' "$INSTALL_DIR/.env"
grep -qx 'LLM_API_URL=http://host.docker.internal:13305/api/v1' "$INSTALL_DIR/.env"
grep -qx 'LEMONADE_MODEL=Qwen3.6-35B-A3B' "$INSTALL_DIR/.env"
grep -qx 'GGUF_FILE=keep-existing.gguf' "$INSTALL_DIR/.env"

docker_compose_mock() { printf 'model-router\nllama-server\n'; }
DOCKER_COMPOSE_CMD=docker_compose_mock
if ods_external_lemonade_assert_no_managed_llama >/dev/null 2>&1; then
    echo '[FAIL] external Lemonade accepted an enabled managed llama-server' >&2
    exit 1
fi
docker_compose_mock() { printf 'model-router\nlitellm\n'; }
ods_external_lemonade_assert_no_managed_llama
LEMONADE_EXTERNAL=YES
AMD_INFERENCE_RUNTIME=
AMD_INFERENCE_MANAGED=
ods_external_lemonade_requested
LEMONADE_EXTERNAL=false
AMD_INFERENCE_RUNTIME=lemonade
AMD_INFERENCE_MANAGED=false
ods_external_lemonade_requested

# The image planner must agree with the resolver's case-insensitive runtime
# selector. Otherwise it can pull llama even when Compose disables the service.
phase08_images="$(
    export DRY_RUN=true GPU_BACKEND=cpu ODS_MODE=lemonade
    export LEMONADE_EXTERNAL=false AMD_INFERENCE_RUNTIME=LEMONADE AMD_INFERENCE_MANAGED=FALSE
    export ENABLE_COMFYUI=false ENABLE_VOICE=false ENABLE_WORKFLOWS=false
    export ENABLE_RAG=false ENABLE_QDRANT=false ENABLE_EMBEDDINGS=false
    export ENABLE_HERMES=false ENABLE_OPENCLAW=false ENABLE_OPEN_WEBUI=false
    COMPOSE_FLAGS=''
    ods_progress() { :; }
    show_phase() { :; }
    bootline() { :; }
    signal() { :; }
    source installers/phases/08-images.sh
    printf '%s\n' "${PULL_LIST[@]}"
)"
if grep -q 'LLAMA-SERVER' <<< "$phase08_images"; then
    echo '[FAIL] external Lemonade runtime selector planned a managed llama image' >&2
    exit 1
fi

ODS_MODE=lemonade
GPU_BACKEND=amd
LEMONADE_EXTERNAL=false
AMD_INFERENCE_RUNTIME=lemonade
AMD_INFERENCE_MANAGED=true
apply_cpu_gpu_fallback 'Managed Lemonade has no usable GPU devices.'
[[ "$ODS_MODE" == local && "$GPU_BACKEND" == cpu ]] || {
    echo '[FAIL] managed Lemonade did not fall back to local CPU inference' >&2
    exit 1
}

echo '[PASS] external Lemonade preserves its runtime through CPU fallback'
