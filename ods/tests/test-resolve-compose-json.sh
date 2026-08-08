#!/usr/bin/env bash
# Public CLI coverage for structured compose resolution plans.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPORT="$(mktemp)"
trap 'rm -f "$REPORT"' EXIT

bash "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
    --script-dir "$ROOT_DIR" \
    --tier 1 \
    --gpu-backend nvidia \
    --gpu-count 1 \
    --ods-mode local \
    --json > "$REPORT"

python3 - "$REPORT" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    report = json.load(handle)

assert report["primary_file"] == "docker-compose.nvidia.yml"
assert report["files"][:2] == ["docker-compose.base.yml", "docker-compose.nvidia.yml"]
assert report["tier"] == "1"
assert report["gpu_backend"] == "nvidia"
assert report["gpu_count"] == 1
assert report["ods_mode"] == "local"
assert report["flags"] == " ".join(f"-f {path}" for path in report["files"])
PY

echo "[PASS] compose resolver emits a self-consistent JSON plan"
