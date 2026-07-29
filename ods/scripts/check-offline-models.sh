#!/bin/bash
# ODS Offline Mode - host-side model readiness check
#
# Keep one implementation of the readiness contract. This shell entry point
# delegates to validate-models.py so path layouts, active-service handling,
# and incomplete-download detection cannot drift.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD=python3
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD=python
else
    echo "ERROR: Python is required to validate offline model artifacts." >&2
    exit 2
fi

export ODS_ROOT="$ODS_DIR"
exec "$PYTHON_CMD" "$SCRIPT_DIR/validate-models.py"
