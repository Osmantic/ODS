#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

grep -F 'ODS_AGENT_HOST=$(Get-EnvOrNew "ODS_AGENT_HOST" "host.docker.internal")' \
    "$ROOT_DIR/installers/windows/lib/env-generator.ps1" >/dev/null
grep -F 'local agent_host="host.docker.internal"' \
    "$ROOT_DIR/installers/macos/lib/env-generator.sh" >/dev/null
grep -F 'agent_host="$macos_host_gateway"' \
    "$ROOT_DIR/installers/macos/lib/env-generator.sh" >/dev/null
grep -F 'ODS_AGENT_HOST=${ODS_AGENT_HOST:-${agent_host}}' \
    "$ROOT_DIR/installers/macos/lib/env-generator.sh" >/dev/null
grep -F 'ODS_AGENT_HOST=${ODS_AGENT_HOST:-}' \
    "$ROOT_DIR/docker-compose.base.yml" >/dev/null
grep -F '"ODS_AGENT_HOST"' "$ROOT_DIR/.env.schema.json" >/dev/null
grep -F '# ODS_AGENT_HOST=host.docker.internal' "$ROOT_DIR/.env.example" >/dev/null

echo "[PASS] desktop installers route dashboard-api to the platform-safe host-agent endpoint"
