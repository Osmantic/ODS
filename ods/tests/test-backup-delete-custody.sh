#!/usr/bin/env bash
# Pure CPU/filesystem tests: every backup is inside an owned temporary directory.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/test-backup-delete-custody.py"
