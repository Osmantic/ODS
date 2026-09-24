#!/usr/bin/env bash
# Validate every enabled extension Compose fragment in the stack it joins.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXTENSIONS_DIR="$ROOT_DIR/extensions/services"
BASE_COMPOSE="$ROOT_DIR/docker-compose.base.yml"

if ! command -v docker >/dev/null 2>&1; then
    echo "FAIL: docker is required to validate Compose fragments" >&2
    exit 1
fi

if [[ ! -f "$BASE_COMPOSE" || ! -d "$EXTENSIONS_DIR" ]]; then
    echo "FAIL: expected ODS base Compose file and extension directory" >&2
    exit 1
fi

# Required substitutions need non-secret values before Compose can validate the
# merged model. New required variables intentionally fail this gate until their
# CI placeholder is declared here.
export WEBUI_SECRET="${WEBUI_SECRET:-compose-validation-placeholder}"
export SEARXNG_SECRET="${SEARXNG_SECRET:-compose-validation-placeholder}"
export N8N_USER="${N8N_USER:-compose-validation@example.invalid}"
export N8N_PASS="${N8N_PASS:-compose-validation-placeholder}"
export LITELLM_KEY="${LITELLM_KEY:-compose-validation-placeholder}"
export OPENCLAW_TOKEN="${OPENCLAW_TOKEN:-compose-validation-placeholder}"
export HERMES_DASHBOARD_SESSION_TOKEN="${HERMES_DASHBOARD_SESSION_TOKEN:-compose-validation-placeholder}"
export APE_API_KEY="${APE_API_KEY:-compose-validation-placeholder}"
export COMFYUI_GPU_UUID="${COMFYUI_GPU_UUID:-GPU-compose-validation}"
export EMBEDDINGS_GPU_UUID="${EMBEDDINGS_GPU_UUID:-GPU-compose-validation}"
export WHISPER_GPU_UUID="${WHISPER_GPU_UUID:-GPU-compose-validation}"

extension_bases=()
amd_overlays=()
nvidia_overlays=()
apple_overlays=()
local_overlays=()
multigpu_amd_overlays=()
multigpu_nvidia_overlays=()

while IFS= read -r -d '' fragment; do
    case "$(basename "$fragment")" in
        compose.yaml|compose.yml) extension_bases+=(-f "$fragment") ;;
        compose.amd.yaml|compose.amd.yml) amd_overlays+=(-f "$fragment") ;;
        compose.nvidia.yaml|compose.nvidia.yml) nvidia_overlays+=(-f "$fragment") ;;
        compose.apple.yaml|compose.apple.yml) apple_overlays+=(-f "$fragment") ;;
        compose.local.yaml|compose.local.yml) local_overlays+=(-f "$fragment") ;;
        compose.multigpu-amd.yaml|compose.multigpu-amd.yml) multigpu_amd_overlays+=(-f "$fragment") ;;
        compose.multigpu-nvidia.yaml|compose.multigpu-nvidia.yml) multigpu_nvidia_overlays+=(-f "$fragment") ;;
    esac
done < <(find "$EXTENSIONS_DIR" -mindepth 2 -maxdepth 2 -type f \
    \( -name 'compose*.yaml' -o -name 'compose*.yml' \) -print0)

if (( ${#extension_bases[@]} == 0 )); then
    echo "FAIL: no enabled extension Compose fragments found" >&2
    exit 1
fi

failed=0
validated=0

validate_profile() {
    local profile="$1"
    shift
    if docker compose -f "$BASE_COMPOSE" "$@" config --quiet; then
        echo "PASS: $profile extension stack"
    else
        echo "FAIL: $profile extension stack" >&2
        failed=1
    fi
    validated=$((validated + 1))
}

validate_profile base "${extension_bases[@]}"
validate_profile local "${extension_bases[@]}" "${local_overlays[@]}"
validate_profile amd \
    -f "$ROOT_DIR/docker-compose.amd.yml" \
    "${extension_bases[@]}" "${amd_overlays[@]}"
validate_profile nvidia \
    -f "$ROOT_DIR/docker-compose.nvidia.yml" \
    "${extension_bases[@]}" "${nvidia_overlays[@]}"
validate_profile apple \
    -f "$ROOT_DIR/docker-compose.apple.yml" \
    "${extension_bases[@]}" "${apple_overlays[@]}"
validate_profile multigpu-amd \
    -f "$ROOT_DIR/docker-compose.amd.yml" \
    -f "$ROOT_DIR/docker-compose.multigpu-amd.yml" \
    "${extension_bases[@]}" "${amd_overlays[@]}" "${multigpu_amd_overlays[@]}"
validate_profile multigpu-nvidia \
    -f "$ROOT_DIR/docker-compose.nvidia.yml" \
    -f "$ROOT_DIR/docker-compose.multigpu-nvidia.yml" \
    "${extension_bases[@]}" "${nvidia_overlays[@]}" "${multigpu_nvidia_overlays[@]}"

echo "Validated $validated extension stack profiles"
exit "$failed"
