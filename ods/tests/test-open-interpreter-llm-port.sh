#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE="$ROOT_DIR/ods/extensions/library/services/open-interpreter/compose.yaml"

grep -q 'LLM_API_URL=\${LLM_API_URL:-http://llama-server:8080}' "$COMPOSE" \
    || { echo "FAIL: Open Interpreter default does not use the ODS internal LLM port" >&2; exit 1; }
if grep -q 'LLM_API_URL=\${LLM_API_URL:-http://llama-server:8000}' "$COMPOSE"; then
    echo "FAIL: stale llama-server:8000 default remains" >&2
    exit 1
fi
grep -q 'LLM_API_URL=' "$COMPOSE" \
    || { echo "FAIL: configurable LLM endpoint was removed" >&2; exit 1; }

echo "PASS: Open Interpreter defaults to llama-server:8080 and remains configurable"
