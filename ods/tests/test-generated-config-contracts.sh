#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 -m py_compile "$ROOT_DIR/scripts/validate-generated-configs.py"
python3 "$ROOT_DIR/tests/test-generated-config-backups.py"
python3 "$ROOT_DIR/scripts/validate-generated-configs.py" "$ROOT_DIR/config/generated-config-contracts.json"

json_output="$(python3 "$ROOT_DIR/scripts/validate-generated-configs.py" --json "$ROOT_DIR/config/generated-config-contracts.json")"
GENERATED_JSON="$json_output" python3 - "$ROOT_DIR/config/generated-config-contracts.json" <<'PY'
import json
import os
import pathlib
import sys

payload = json.loads(os.environ["GENERATED_JSON"])
contract = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert payload["ok"] is True
assert payload["surface_count"] == len(contract["surfaces"])
assert payload["issues"] == []
PY

missing_writer_contract="$(mktemp)"
missing_lemonade_writer_contract="$(mktemp)"
trap 'rm -f "$missing_writer_contract" "$missing_lemonade_writer_contract"' EXIT
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

missing_output=""
missing_rc=0
missing_output=$(python3 "$ROOT_DIR/scripts/validate-generated-configs.py" --json "$ROOT_DIR/config/missing-generated-contract.json") \
    || missing_rc=$?
[[ "$missing_rc" -eq 1 ]]
GENERATED_JSON="$missing_output" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["GENERATED_JSON"])
assert payload["ok"] is False
assert payload["surface_count"] == 0
assert payload["issues"] == [
    {"path": "$file", "message": "generated config contract file not found"}
]
PY

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

python3 "$ROOT_DIR/tests/test-fedora-strix-compat.py"
python3 "$ROOT_DIR/tests/test-embedding-model-contract.py"

echo "[PASS] generated config contract test"
