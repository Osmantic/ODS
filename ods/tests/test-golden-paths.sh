#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PYTHON_CMD="python3"
command -v python3 >/dev/null 2>&1 || PYTHON_CMD="python"
"$PYTHON_CMD" -m py_compile "$ROOT_DIR/scripts/validate-golden-paths.py"
"$PYTHON_CMD" "$ROOT_DIR/scripts/validate-golden-paths.py" "$ROOT_DIR/config/golden-paths.json"

echo "[PASS] golden path validator test"
