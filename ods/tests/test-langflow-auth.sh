#!/usr/bin/env bash
# Langflow runs arbitrary Python; it must never start with AUTO_LOGIN or
# without generated secrets.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_DIR="$ROOT_DIR/extensions/library/services/langflow"
COMPOSE="$SERVICE_DIR/compose.yaml"
MANIFEST="$SERVICE_DIR/manifest.yaml"

require() {
    local pattern="$1" file="$2" message="$3"
    if ! grep -Eq -- "$pattern" "$file"; then
        printf 'FAIL: %s\n' "$message" >&2
        exit 1
    fi
}

require '^ +- LANGFLOW_AUTO_LOGIN=false$' "$COMPOSE" \
    "Langflow must disable AUTO_LOGIN"
for key in LANGFLOW_SUPERUSER_PASSWORD LANGFLOW_SECRET_KEY; do
    require "^ +- ${key}=\\$\{${key}:\?" "$COMPOSE" \
        "Langflow compose must refuse to start without ${key}"
    require "^ +- key: ${key}$" "$MANIFEST" \
        "Langflow manifest must declare ${key}"
done
[[ "$(grep -Ec '^ +generate: ' "$MANIFEST")" -eq 2 ]] || {
    printf 'FAIL: Langflow manifest must generate both secrets\n' >&2
    exit 1
}

printf 'PASS: Langflow requires authentication\n'
