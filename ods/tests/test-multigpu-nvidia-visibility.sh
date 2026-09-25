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

# One-GPU placement of a small model: split mode none plus the main GPU index.
# Without a placement llama.cpp gets its own default (0), never an empty value
# (it parses --main-gpu as an integer).
env_of() {
    python3 -c 'import json, sys; env=json.load(sys.stdin)["services"]["llama-server"]["environment"]; print(env["LLAMA_ARG_SPLIT_MODE"], env["LLAMA_ARG_MAIN_GPU"], env["LLAMA_ARG_TENSOR_SPLIT"])'
}
actual=$(WEBUI_SECRET=test LLAMA_SERVER_GPU_UUIDS=GPU-a,GPU-b LLAMA_ARG_SPLIT_MODE=layer \
    LLAMA_ARG_TENSOR_SPLIT=1,1 "${compose[@]}" | env_of)
[[ "$actual" == "layer 0 1,1" ]] || { echo "FAIL: layer split env: $actual" >&2; exit 1; }
actual=$(WEBUI_SECRET=test LLAMA_SERVER_GPU_UUIDS=GPU-a,GPU-b LLAMA_ARG_SPLIT_MODE=none \
    LLAMA_ARG_MAIN_GPU=1 LLAMA_ARG_TENSOR_SPLIT=1,1 "${compose[@]}" | env_of)
[[ "$actual" == "none 1 1,1" ]] || { echo "FAIL: one-GPU placement env: $actual" >&2; exit 1; }
actual=$(WEBUI_SECRET=test LLAMA_SERVER_GPU_UUIDS=GPU-a,GPU-b LLAMA_ARG_MAIN_GPU= "${compose[@]}" | env_of)
[[ "$actual" == "none 0 " ]] || { echo "FAIL: empty LLAMA_ARG_MAIN_GPU must fall back to 0: $actual" >&2; exit 1; }

echo "test-multigpu-nvidia-visibility: ok"
