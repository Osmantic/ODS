#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE="$SCRIPT_DIR/../extensions/library/services/ollama/compose.yaml"

grep -q 'OLLAMA_MODEL=\${OLLAMA_MODEL:-llama3}' "$COMPOSE" \
  || { echo "OLLAMA_MODEL environment contract is missing" >&2; exit 1; }
grep -q 'ollama pull' "$COMPOSE" \
  || { echo "Ollama startup must pull the configured model" >&2; exit 1; }
grep -q '\$${OLLAMA_MODEL:-llama3}' "$COMPOSE" \
  || { echo "Ollama startup must use the configured model" >&2; exit 1; }

echo "PASS: Ollama startup pulls the configured default model"
