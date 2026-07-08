#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 -m py_compile "$ROOT_DIR/scripts/validate-golden-paths.py"
python3 "$ROOT_DIR/scripts/validate-golden-paths.py" "$ROOT_DIR/config/golden-paths.json"

echo "[PASS] golden path validator test"
