#!/usr/bin/env bash
# Regression: ods list --json must escape registry strings from user extensions.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
USER_EXT_DIR="$PROJECT_DIR/data/user-extensions/json-escape-test"

cleanup() {
    rm -rf "$USER_EXT_DIR"
}
trap cleanup EXIT

if ! command -v python3 >/dev/null 2>&1; then
    echo "[SKIP] python3 not available"
    exit 0
fi

if ! python3 -c 'import yaml' >/dev/null 2>&1; then
    echo "[SKIP] PyYAML not available"
    exit 0
fi

mkdir -p "$USER_EXT_DIR"
cat > "$USER_EXT_DIR/manifest.yaml" <<'YAML'
schema_version: ods.services.v1
service:
  id: json-escape-test
  name: JSON Escape Test
  port: 65535
  health: /health
  category: 'optional "quoted" \ slash'
YAML

output=$(ODS_HOME="$PROJECT_DIR" NO_COLOR=1 "$PROJECT_DIR/ods-cli" list --json)

python3 - "$output" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
service = next(item for item in payload if item["id"] == "json-escape-test")
assert service["category"] == 'optional "quoted" \\ slash'
assert service["status"] == "disabled"
PY

echo "[PASS] ods list --json escapes user-extension strings"
