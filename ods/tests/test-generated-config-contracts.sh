#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 -m py_compile "$ROOT_DIR/scripts/validate-generated-configs.py"
python3 "$ROOT_DIR/scripts/validate-generated-configs.py" "$ROOT_DIR/config/generated-config-contracts.json"

missing_writer_contract="$(mktemp)"
missing_lemonade_writer_contract="$(mktemp)"
escaping_path_contract="$(mktemp)"
trap 'rm -f "$missing_writer_contract" "$missing_lemonade_writer_contract" "$escaping_path_contract"' EXIT
python3 - "$ROOT_DIR/config/generated-config-contracts.json" "$missing_writer_contract" <<'PY'
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
target = Path(sys.argv[2])
contract = json.loads(source.read_text(encoding="utf-8"))
surface = next(item for item in contract["surfaces"] if item["id"] == "litellm-local-native")
surface["writers"] = [
    writer
    for writer in surface["writers"]
    if writer["path"] != "installers/windows/install-windows.ps1"
]
target.write_text(json.dumps(contract), encoding="utf-8")
PY
if python3 "$ROOT_DIR/scripts/validate-generated-configs.py" "$missing_writer_contract" >/dev/null 2>&1; then
    echo "[FAIL] writer marker validation accepted an incomplete ownership inventory" >&2
    exit 1
fi

python3 - "$ROOT_DIR/config/generated-config-contracts.json" "$missing_lemonade_writer_contract" <<'PY'
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
target = Path(sys.argv[2])
contract = json.loads(source.read_text(encoding="utf-8"))
surface = next(item for item in contract["surfaces"] if item["id"] == "litellm-lemonade")
surface["writers"] = [
    writer
    for writer in surface["writers"]
    if writer["path"] != "config/litellm/lemonade.yaml"
]
target.write_text(json.dumps(contract), encoding="utf-8")
PY
if python3 "$ROOT_DIR/scripts/validate-generated-configs.py" "$missing_lemonade_writer_contract" >/dev/null 2>&1; then
    echo "[FAIL] Lemonade writer validation accepted an incomplete ownership inventory" >&2
    exit 1
fi

python3 - "$ROOT_DIR/config/generated-config-contracts.json" "$escaping_path_contract" <<'PY'
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
target = Path(sys.argv[2])
contract = json.loads(source.read_text(encoding="utf-8"))
contract["surfaces"][0]["invariants"][0]["path"] = "../README.md"
target.write_text(json.dumps(contract), encoding="utf-8")
PY
if python3 "$ROOT_DIR/scripts/validate-generated-configs.py" "$escaping_path_contract" >/dev/null 2>&1; then
    echo "[FAIL] generated config validation accepted a path outside the repository" >&2
    exit 1
fi

python3 "$ROOT_DIR/tests/test-fedora-strix-compat.py"
python3 "$ROOT_DIR/tests/test-embedding-model-contract.py"

echo "[PASS] generated config contract test"
