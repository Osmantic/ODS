#!/usr/bin/env bash
# Regression contract: update must stop after both Compose restart commands fail.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UPDATE_SCRIPT="$SCRIPT_DIR/../ods-update.sh"

python3 - "$UPDATE_SCRIPT" <<'PY'
import pathlib
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
start = text.index("# ── Step 4: restart services")
end = text.index("# ── Step 5: health-check with timeout", start)
block = text[start:end]
assert block.count("if ! docker-compose") == 2, block
assert block.count("_update_rollback \"Both Docker Compose v2 and v1 failed") == 2, block
assert block.count("return 1") >= 2, block
print("PASS: update propagates dual Compose restart failure")
PY
