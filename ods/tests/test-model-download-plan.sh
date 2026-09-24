#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PLANNER="$ROOT_DIR/scripts/pre-download.sh"

json_out="$(bash "$PLANNER" --plan --tier pro --with-voice --json)"
JSON_REPORT="$json_out" python3 - <<'PY'
import json
import os

report = json.loads(os.environ["JSON_REPORT"])
assert report["schema_version"] == "1"
assert report["kind"] == "model-download-plan"
assert report["tier"] == "pro"
assert report["selection"] == "requested"
assert report["include_voice"] is True
assert report["estimated_download_gb"] == 21.2
assert report["components"] == [
    {"kind": "llm", "model": "Qwen/Qwen2.5-32B-Instruct-AWQ", "size_gb": 18},
    {"kind": "stt", "model": "Systran/faster-whisper-large-v3", "size_gb": 3},
    {"kind": "tts", "model": "hexgrad/Kokoro-82M", "size_gb": 0.2},
]
assert set(report["hardware"]) == {"ram_gb", "vram_gb"}
PY

human_out="$(bash "$PLANNER" --plan --tier nano)"
[[ "$human_out" == *"Model pre-download plan"* ]]
[[ "$human_out" == *"Qwen/Qwen2.5-1.5B-Instruct"* ]]

set +e
error_out="$(bash "$PLANNER" --json --tier nano 2>&1)"
status=$?
set -e
[[ $status -eq 2 ]]
[[ "$error_out" == *"--json requires --plan"* ]]

echo "Model download plan tests passed"
