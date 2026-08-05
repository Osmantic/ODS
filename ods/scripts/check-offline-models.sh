#!/bin/bash
# ODS Offline Mode - host-side model readiness check
#
# Keep one implementation of the readiness contract. This shell entry point
# delegates to validate-models.py so path layouts, active-service handling,
# and incomplete-download detection cannot drift.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Resolve Python through the shared resolver. `command -v python3` alone is not
# enough on Windows: the Microsoft Store app-execution alias is on PATH by
# default, so `command -v` succeeds and the interpreter then refuses to run and
# prints a Store advert instead. lib/python-cmd.sh probes that the candidate
# actually executes, skips the alias, and honours ODS_PYTHON_CMD.
PYTHON_CMD=""
if [[ -f "$ODS_DIR/lib/python-cmd.sh" ]]; then
    # shellcheck source=../lib/python-cmd.sh
    . "$ODS_DIR/lib/python-cmd.sh"
    PYTHON_CMD="$(ods_detect_python_cmd 2>/dev/null)" || PYTHON_CMD=""
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD=python3
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD=python
fi

if [[ -z "$PYTHON_CMD" ]]; then
    echo "ERROR: Python is required to validate offline model artifacts." >&2
    echo "       Install Python 3, or set ODS_PYTHON_CMD to a working interpreter." >&2
    exit 2
fi

export ODS_ROOT="$ODS_DIR"
exec "$PYTHON_CMD" "$SCRIPT_DIR/validate-models.py"
