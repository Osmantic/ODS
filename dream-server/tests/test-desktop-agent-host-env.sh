#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

grep -F 'DREAM_AGENT_HOST=$(Get-EnvOrNew "DREAM_AGENT_HOST" "host.docker.internal")' \
    "$ROOT_DIR/installers/windows/lib/env-generator.ps1" >/dev/null
grep -F 'DREAM_AGENT_HOST=${DREAM_AGENT_HOST:-host.docker.internal}' \
    "$ROOT_DIR/installers/macos/lib/env-generator.sh" >/dev/null
grep -F 'DREAM_AGENT_HOST=${DREAM_AGENT_HOST:-}' \
    "$ROOT_DIR/docker-compose.base.yml" >/dev/null
grep -F '"DREAM_AGENT_HOST"' "$ROOT_DIR/.env.schema.json" >/dev/null
grep -F '# DREAM_AGENT_HOST=host.docker.internal' "$ROOT_DIR/.env.example" >/dev/null

echo "[PASS] desktop installers pin dashboard-api agent host to host.docker.internal"
