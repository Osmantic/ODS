#!/usr/bin/env bash
# Multi-GPU NVIDIA overlays must pin ComfyUI and Whisper to their assigned GPU
# only. Compose appends deploy device-reservation lists across files, so a
# reservation in both compose.nvidia.yaml and the multigpu overlay would hand
# the container an extra GPU.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR/extensions/services"

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    echo "test-multigpu-nvidia-service-pinning: skipped (Docker Compose unavailable)"
    exit 0
fi

check_service() {
    local svc="$1" uuid_var="$2"
    env "$uuid_var=GPU-pinned" docker compose \
        -f "$svc/compose.yaml" -f "$svc/compose.nvidia.yaml" -f "$svc/compose.multigpu-nvidia.yaml" \
        config --format json \
        | python3 -c '
import json, sys
svc = sys.argv[1]
devices = json.load(sys.stdin)["services"][svc]["deploy"]["resources"]["reservations"]["devices"]
assert len(devices) == 1, f"{svc}: expected 1 device reservation, got {devices}"
assert devices[0].get("device_ids") == ["GPU-pinned"], f"{svc}: {devices}"
assert "count" not in devices[0], f"{svc}: {devices}"
' "$svc"
}

check_single_gpu() {
    local svc="$1"
    docker compose -f "$svc/compose.yaml" -f "$svc/compose.nvidia.yaml" config --format json         | python3 -c '
import json, sys
svc = sys.argv[1]
devices = json.load(sys.stdin)["services"][svc]["deploy"]["resources"]["reservations"]["devices"]
assert devices == [{"capabilities": ["gpu"], "driver": "nvidia", "device_ids": ["0"]}], f"{svc}: {devices}"
' "$svc"
}

check_missing_uuid_fails() {
    local svc="$1" uuid_var="$2" err
    if err=$(env -u "$uuid_var" docker compose -f "$svc/compose.yaml" -f "$svc/compose.nvidia.yaml"         -f "$svc/compose.multigpu-nvidia.yaml" config 2>&1); then
        echo "FAIL: $svc multi-GPU overlay accepted an unset $uuid_var" >&2
        exit 1
    fi
    grep -q "$uuid_var must be set" <<<"$err" || { echo "$err" >&2; exit 1; }
}

check_single_gpu comfyui
check_single_gpu whisper
check_missing_uuid_fails comfyui COMFYUI_GPU_UUID
check_missing_uuid_fails whisper WHISPER_GPU_UUID
check_service comfyui COMFYUI_GPU_UUID
check_service whisper WHISPER_GPU_UUID

echo "test-multigpu-nvidia-service-pinning: ok"
