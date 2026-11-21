#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="$ROOT_DIR/extensions/library/services/open-interpreter/compose.yaml"

grep -q 'LLM_API_URL=\${LLM_API_URL:-http://llama-server:8080}' "$COMPOSE"
! grep -q 'LLM_API_URL=\${LLM_API_URL:-http://llama-server:8000}' "$COMPOSE"

echo "[PASS] Open Interpreter defaults to the ODS internal LLM port"
