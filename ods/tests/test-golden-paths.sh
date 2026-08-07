#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 -m py_compile "$ROOT_DIR/scripts/validate-golden-paths.py"
python3 "$ROOT_DIR/scripts/validate-golden-paths.py" "$ROOT_DIR/config/golden-paths.json"

escaping_contract="$(mktemp)"
trap 'rm -f "$escaping_contract"' EXIT
python3 - "$ROOT_DIR/config/golden-paths.json" "$escaping_contract" <<'PY'
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
target = Path(sys.argv[2])
contract = json.loads(source.read_text(encoding="utf-8"))
contract["scenarios"][0]["installer"]["entrypoint"] = "../../install.sh"
target.write_text(json.dumps(contract), encoding="utf-8")
PY
if python3 "$ROOT_DIR/scripts/validate-golden-paths.py" "$escaping_contract" >/dev/null 2>&1; then
    echo "[FAIL] golden path validator accepted an entrypoint outside the repository" >&2
    exit 1
fi

echo "[PASS] golden path validator test"
