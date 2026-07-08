#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

echo "[smoke] Linux NVIDIA installer paths"
grep -rq 'docker-compose.nvidia.yml' install-core.sh installers/
grep -rq 'GPU_BACKEND" != "amd"' install-core.sh installers/
grep -q 'Linux (Ubuntu/Debian family).*NVIDIA' docs/SUPPORT-MATRIX.md

echo "[smoke] Extension service directories exist"
test -d extensions/services/llama-server
test -d extensions/services/whisper
test -f extensions/services/whisper/compose.nvidia.yaml

echo "[smoke] PASS linux-nvidia"
