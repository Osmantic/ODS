#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT_DIR/scripts/check-release-claims.sh"

output="$(bash "$SCRIPT" --json)"
RELEASE_CLAIMS_JSON="$output" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["RELEASE_CLAIMS_JSON"])
assert payload["ok"] is True
assert payload["support"] == {
    "linux": True,
    "windows_wsl2": True,
    "macos": True,
    "windows_native": False,
}
assert len(payload["checks"]) == 11
assert all(check["status"] == "pass" for check in payload["checks"])
assert payload["checks"][-1]["name"] == "release claim gates"
PY

human_rc=0
human="$(bash "$SCRIPT")" || human_rc=$?
[[ "$human_rc" -eq 0 ]]
grep -qF "[PASS] release claim gates" <<< "$human"

echo "[PASS] release claim gate emits a standalone JSON receipt"
