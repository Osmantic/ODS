#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 -m py_compile "$ROOT_DIR/scripts/validate-golden-paths.py"
python3 "$ROOT_DIR/scripts/validate-golden-paths.py" "$ROOT_DIR/config/golden-paths.json"

json_output="$(python3 "$ROOT_DIR/scripts/validate-golden-paths.py" --json "$ROOT_DIR/config/golden-paths.json")"
GOLDEN_JSON="$json_output" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["GOLDEN_JSON"])
assert payload["ok"] is True
assert payload["scenario_count"] == 4
assert payload["issues"] == []
PY

missing_output=""
missing_rc=0
missing_output=$(python3 "$ROOT_DIR/scripts/validate-golden-paths.py" --json "$ROOT_DIR/config/missing-golden-paths.json") \
    || missing_rc=$?
[[ "$missing_rc" -eq 1 ]]
GOLDEN_JSON="$missing_output" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["GOLDEN_JSON"])
assert payload["ok"] is False
assert payload["scenario_count"] == 0
assert payload["issues"] == [{"path": "$file", "message": "golden path file not found"}]
PY

echo "[PASS] golden path validator test"
