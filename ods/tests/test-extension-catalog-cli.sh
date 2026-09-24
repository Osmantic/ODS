#!/usr/bin/env bash
# Public CLI contract: operators can search the shipped extension catalog.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
INSTALL_DIR="$TMP_DIR/install"
trap 'rm -rf "$TMP_DIR"' EXIT

mkdir -p "$INSTALL_DIR/config" "$INSTALL_DIR/scripts"
cp "$ROOT_DIR/docker-compose.base.yml" "$INSTALL_DIR/docker-compose.base.yml"
cp "$ROOT_DIR/scripts/query-extensions-catalog.py" "$INSTALL_DIR/scripts/"
cat > "$INSTALL_DIR/config/extensions-catalog.json" <<'EOF'
{
  "schema_version": "1.0.0",
  "extensions": [
    {
      "id": "vector-local",
      "name": "Vector Local",
      "description": "Private vector search",
      "tags": ["rag"],
      "gpu_backends": ["amd", "nvidia"]
    },
    {
      "id": "music-cuda",
      "name": "Music CUDA",
      "description": "Generate audio",
      "tags": ["music"],
      "gpu_backends": ["nvidia"]
    }
  ]
}
EOF

output=$(ODS_HOME="$INSTALL_DIR" NO_COLOR=1 "$ROOT_DIR/ods-cli" \
    catalog search rag --backend amd --json)
python3 - "$output" <<'PYEOF'
import json
import sys

extensions = json.loads(sys.argv[1])
assert [item["id"] for item in extensions] == ["vector-local"]
assert extensions[0]["gpu_backends"] == ["amd", "nvidia"]
PYEOF

table=$(ODS_HOME="$INSTALL_DIR" NO_COLOR=1 "$ROOT_DIR/ods-cli" catalog list --backend nvidia)
grep -q 'vector-local' <<<"$table"
grep -q 'music-cuda' <<<"$table"

echo "[PASS] extension catalog CLI discovery"
