#!/bin/bash
# ODS Root Installer
# Delegates to ods/install.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Check if ods directory exists
if [ ! -d "$SCRIPT_DIR/ods" ]; then
    echo "Error: ods directory not found"
    echo "Expected: $SCRIPT_DIR/ods"
    exit 1
fi

# Delegate to ods installer
cd "$SCRIPT_DIR/ods"
exec ./install.sh "$@"
