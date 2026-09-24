#!/usr/bin/env bash
# Public CLI contract: saved presets can be enumerated without parsing tables.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
INSTALL_DIR="$TMP_DIR/install"
trap 'rm -rf "$TMP_DIR"' EXIT

mkdir -p "$INSTALL_DIR/presets/creative"
cp "$ROOT_DIR/docker-compose.base.yml" "$INSTALL_DIR/docker-compose.base.yml"
cat > "$INSTALL_DIR/presets/creative/meta.txt" <<'EOF'
name=creative
created=2026-08-29T12:34:56Z
gpu_backend=nvidia
tier=T3
EOF
cat > "$INSTALL_DIR/presets/creative/extensions.list" <<'EOF'
enabled:comfyui
enabled:whisper
disabled:langfuse
EOF

output=$(ODS_HOME="$INSTALL_DIR" NO_COLOR=1 "$ROOT_DIR/ods-cli" preset list --json)
python3 - "$output" <<'PYEOF'
import json
import sys

presets = json.loads(sys.argv[1])
assert presets == [
    {
        "name": "creative",
        "created": "2026-08-29T12:34:56Z",
        "gpu_backend": "nvidia",
        "tier": "T3",
        "enabled_services": ["comfyui", "whisper"],
        "disabled_services": ["langfuse"],
    }
]
PYEOF

echo "[PASS] preset list JSON output"
