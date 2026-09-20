#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT_DIR/ods-cli"

bash -n "$CLI"
grep -q 'sysctl -n hw.logicalcpu' "$CLI"

echo "[PASS] ods-cli probes macOS logical CPU count"
