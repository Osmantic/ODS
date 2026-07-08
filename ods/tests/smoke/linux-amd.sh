#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

echo "[smoke] Linux AMD compose contract"
test -f docker-compose.base.yml
test -f docker-compose.amd.yml
grep -rq "docker-compose.base.yml" install-core.sh installers/
grep -rq "docker-compose.amd.yml" install-core.sh installers/

echo "[smoke] Extension service directories exist"
test -d extensions/services/llama-server
test -d extensions/services/open-webui
test -f extensions/services/llama-server/manifest.yaml

echo "[smoke] Service registry library exists"
test -f lib/service-registry.sh

echo "[smoke] Linux AMD workflow path contract"
# dashboard-api resolves canonical config/n8n with legacy workflows/ fallback
grep -q '"config" / "n8n"' extensions/services/dashboard-api/config.py

echo "[smoke] PASS linux-amd"
