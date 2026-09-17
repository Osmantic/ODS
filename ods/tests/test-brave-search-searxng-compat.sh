#!/usr/bin/env bash
# Contract tests for the brave-search proxy's opt-in searxng compatibility
# mode. Spawns the real proxy against a loopback stub of the Brave API and
# asserts both the stable /v1/search contract and the searxng-shaped /search
# route. Logic lives in test-brave-search-searxng-compat.mjs.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

command -v node >/dev/null 2>&1 || { echo "[FAIL] node is required"; exit 1; }
node -e "process.exit(typeof fetch === 'function' ? 0 : 1)" \
    || { echo "[FAIL] node 18+ with global fetch is required"; exit 1; }

exec node "$ROOT_DIR/tests/test-brave-search-searxng-compat.mjs"
