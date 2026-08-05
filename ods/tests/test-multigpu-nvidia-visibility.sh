#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    echo "test-multigpu-nvidia-visibility: skipped (Docker Compose unavailable)"
    exit 0
fi

tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT
compose=(
    docker compose
    -f docker-compose.base.yml
    -f docker-compose.multigpu-nvidia.yml
    config --format json
)

if WEBUI_SECRET=test "${compose[@]}" >"$tmp_dir/missing.json" 2>"$tmp_dir/missing.err"; then
    echo "FAIL: multi-GPU compose accepted an empty LLAMA_SERVER_GPU_UUIDS" >&2
    exit 1
fi
grep -q 'LLAMA_SERVER_GPU_UUIDS must be set' "$tmp_dir/missing.err" \
    || { cat "$tmp_dir/missing.err" >&2; exit 1; }

WEBUI_SECRET=test LLAMA_SERVER_GPU_UUIDS=GPU-a,GPU-b "${compose[@]}" \
    | python3 -c 'import json, sys; config=json.load(sys.stdin); visible=config["services"]["llama-server"]["environment"]["NVIDIA_VISIBLE_DEVICES"]; assert visible == "GPU-a,GPU-b", visible'

echo "test-multigpu-nvidia-visibility: ok"
