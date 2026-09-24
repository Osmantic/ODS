#!/usr/bin/env bash
# Public CLI contract: workflow templates are discoverable without the dashboard.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
INSTALL_DIR="$TMP_DIR/install"
trap 'rm -rf "$TMP_DIR"' EXIT

mkdir -p "$INSTALL_DIR/config/n8n" "$INSTALL_DIR/scripts"
cp "$ROOT_DIR/docker-compose.base.yml" "$INSTALL_DIR/docker-compose.base.yml"
cp "$ROOT_DIR/scripts/query-workflow-catalog.py" "$INSTALL_DIR/scripts/"
cat > "$INSTALL_DIR/config/n8n/catalog.json" <<'EOF'
{
  "version": "2",
  "workflows": [
    {
      "id": "document-qa",
      "file": "document-qa.json",
      "name": "Document Q&A",
      "description": "Ask questions using local retrieval",
      "category": "productivity",
      "dependencies": ["qdrant", "llama-server"],
      "setupTime": "2 minutes"
    },
    {
      "id": "voice-memo",
      "file": "voice-memo.json",
      "name": "Voice Memo",
      "description": "Transcribe personal notes",
      "category": "voice",
      "dependencies": ["whisper"]
    }
  ]
}
EOF

search=$(ODS_HOME="$INSTALL_DIR" NO_COLOR=1 "$ROOT_DIR/ods-cli" workflow search qdrant --json)
python3 - "$search" <<'PYEOF'
import json
import sys

workflows = json.loads(sys.argv[1])
assert [item["id"] for item in workflows] == ["document-qa"]
PYEOF

detail=$(ODS_HOME="$INSTALL_DIR" NO_COLOR=1 "$ROOT_DIR/ods-cli" workflow show voice-memo --json)
python3 - "$detail" <<'PYEOF'
import json
import sys

workflow = json.loads(sys.argv[1])
assert workflow["id"] == "voice-memo"
assert workflow["dependencies"] == ["whisper"]
PYEOF

table=$(ODS_HOME="$INSTALL_DIR" NO_COLOR=1 "$ROOT_DIR/ods-cli" workflow list --category productivity)
grep -q 'document-qa' <<<"$table"
if grep -q 'voice-memo' <<<"$table"; then
    echo "[FAIL] category filter leaked voice workflow" >&2
    exit 1
fi

echo "[PASS] workflow catalog CLI discovery"
