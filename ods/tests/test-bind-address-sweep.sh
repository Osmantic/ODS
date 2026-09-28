#!/usr/bin/env bash
# All private ports must stay loopback-bound even when --lan is enabled.
# Authenticated UI/gateway entrypoints are checked separately.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$ROOT_DIR/lib/python-cmd.sh"
python_cmd="$(ods_detect_python_cmd_with_module yaml)"
exec "$python_cmd" "$ROOT_DIR/tests/contracts/test-private-service-ports.py"
