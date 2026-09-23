#!/usr/bin/env bash
# Run the whole updater with isolated service boundaries and real temp files.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${PYTHON:-python3}" "$SCRIPT_DIR/test_source_update_activation.py"
