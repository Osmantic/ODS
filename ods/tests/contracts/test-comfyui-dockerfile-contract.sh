#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DOCKERFILE="$ROOT_DIR/extensions/services/comfyui/Dockerfile"

[[ -f "$DOCKERFILE" ]] || { echo "[FAIL] missing ComfyUI Dockerfile" >&2; exit 1; }

# A custom node must never be advertised by a successful image build when its
# requirements failed to install. Keep dependency failures fatal and visible.
if grep -nE 'pip3 install[^\n]*requirements\.txt[^\n]*(2>/dev/null|\|\|[[:space:]]*true)' "$DOCKERFILE"; then
    echo "[FAIL] ComfyUI custom-node dependency installation is suppressed" >&2
    exit 1
fi

count="$(grep -c 'pip3 install --no-cache-dir -r requirements.txt' "$DOCKERFILE")"
[[ "$count" -eq 6 ]] || { echo "[FAIL] expected base plus five custom-node dependency installs, found $count" >&2; exit 1; }
echo "[PASS] ComfyUI dependency installs are fatal and visible"
