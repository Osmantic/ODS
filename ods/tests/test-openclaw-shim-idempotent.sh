#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT_DIR/config/openclaw/inject-token.js"

command -v node >/dev/null 2>&1 || { echo "SKIP: node unavailable"; exit 0; }
node --check "$SCRIPT"
grep -q "openSync(PID_FILE, 'wx')" "$SCRIPT" \
  || { echo "FAIL: shim does not claim an exclusive pid file" >&2; exit 1; }
grep -q "already running; refusing duplicate process" "$SCRIPT" \
  || { echo "FAIL: duplicate shim processes are not rejected" >&2; exit 1; }
grep -q "owner === String(process.pid)" "$SCRIPT" \
  || { echo "FAIL: shim does not clean up only its own pid file" >&2; exit 1; }
echo "PASS: OpenClaw shim startup is idempotent and cleans ownership state"
