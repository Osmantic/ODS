#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

require_literal() {
    local path="$1"
    local pattern="$2"
    local contract="$3"

    grep -Fq -- "$pattern" "$path" \
        || fail "$contract (${path#"$ROOT_DIR/"})"
}

require_literal \
    "$ROOT_DIR/installers/windows/lib/env-generator.ps1" \
    'ODS_AGENT_HOST=$(Get-EnvOrNew "ODS_AGENT_HOST" "host.docker.internal")' \
    "Windows env generator is missing the host-agent default"
require_literal \
    "$ROOT_DIR/installers/macos/lib/env-generator.sh" \
    'local agent_host="host.docker.internal"' \
    "macOS env generator is missing the Docker Desktop fallback"
require_literal \
    "$ROOT_DIR/installers/macos/lib/env-generator.sh" \
    'agent_host="$macos_host_gateway"' \
    "macOS env generator is missing the direct-bind gateway override"
require_literal \
    "$ROOT_DIR/installers/macos/lib/env-generator.sh" \
    'ODS_AGENT_HOST=${ODS_AGENT_HOST:-${agent_host}}' \
    "macOS env generator is missing the ODS_AGENT_HOST export"
require_literal \
    "$ROOT_DIR/docker-compose.base.yml" \
    'ODS_AGENT_HOST=${ODS_AGENT_HOST:-}' \
    "Dashboard API Compose wiring is missing ODS_AGENT_HOST"
require_literal \
    "$ROOT_DIR/.env.schema.json" \
    '"ODS_AGENT_HOST"' \
    "Environment schema is missing ODS_AGENT_HOST"
require_literal \
    "$ROOT_DIR/.env.example" \
    '# ODS_AGENT_HOST=host.docker.internal' \
    "Example environment is missing the desktop host-agent override"

echo "[PASS] desktop installers route dashboard-api to the platform-safe host-agent endpoint"
