#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT_DIR/scripts/check-compatibility.sh"

output="$(bash "$SCRIPT" --json)"
COMPAT_JSON="$output" python3 - "$ROOT_DIR/manifest.json" <<'PY'
import json
import os
import pathlib
import sys

payload = json.loads(os.environ["COMPAT_JSON"])
manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert payload["ok"] is True
assert payload["manifest_version"] == str(manifest["manifestVersion"])
assert payload["release_version"] == manifest["release"]["version"]
assert len(payload["checks"]) >= 6
assert all(item["status"] in {"pass", "warn"} for item in payload["checks"])
assert payload["checks"][-1]["name"] == "compatibility check complete"
PY

human_rc=0
human="$(bash "$SCRIPT")" || human_rc=$?
[[ "$human_rc" -eq 0 ]]
grep -qF "compatibility check complete" <<< "$human"

echo "[PASS] compatibility validator emits a standalone JSON receipt"
