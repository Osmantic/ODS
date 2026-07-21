#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 -m py_compile "$ROOT_DIR/scripts/validate-generated-configs.py"
python3 "$ROOT_DIR/scripts/validate-generated-configs.py" "$ROOT_DIR/config/generated-config-contracts.json"
python3 "$ROOT_DIR/tests/test-fedora-strix-compat.py"
python3 "$ROOT_DIR/tests/test-embedding-model-contract.py"

echo "[PASS] generated config contract test"
