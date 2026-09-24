#!/usr/bin/env bash
# Public CLI contract: template discovery and preview expose parseable JSON.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT_DIR/ods-cli"

list_output=$(ODS_HOME="$ROOT_DIR" NO_COLOR=1 "$CLI" template list --json)
python3 - "$list_output" <<'PYEOF'
import json
import sys

templates = json.loads(sys.argv[1])
assert isinstance(templates, list)
creative = next(item for item in templates if item["id"] == "creative-studio")
assert creative["name"] == "Creative Studio"
assert creative["tier_minimum"] == "T3"
assert "comfyui" in creative["services"]
PYEOF

preview_output=$(ODS_HOME="$ROOT_DIR" NO_COLOR=1 "$CLI" template preview creative-studio --json)
python3 - "$preview_output" <<'PYEOF'
import json
import sys

preview = json.loads(sys.argv[1])
assert preview["id"] == "creative-studio"
assert isinstance(preview["already_enabled"], list)
assert isinstance(preview["to_enable"], list)
assert sorted(preview["already_enabled"] + preview["to_enable"]) == sorted(preview["services"])
PYEOF

echo "[PASS] template list and preview JSON output"
