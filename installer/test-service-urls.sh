#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPLETE="$ROOT_DIR/src/pages/Complete.tsx"
COMMANDS="$ROOT_DIR/src-tauri/src/commands.rs"

grep -q 'getServiceUrls' "$COMPLETE"
grep -q 'get_service_urls' "$ROOT_DIR/src/hooks/useTauri.ts"
grep -q 'WEBUI_PORT' "$COMMANDS"
grep -q 'DASHBOARD_PORT' "$COMMANDS"
grep -q 'OLLAMA_PORT' "$COMMANDS"
grep -q 'installed_service_urls().webui' "$COMMANDS"

echo "[PASS] installer completion uses persisted service ports"
