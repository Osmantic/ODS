#!/bin/bash
set -euo pipefail

# Test backup with custom label
LABEL="test-snapshot-123"
if bash ods/ods-backup.sh --label "$LABEL" > /dev/null 2>&1; then
    # Check if the directory exists in .backups
    if [[ -d ".backups/$LABEL" || -f ".backups/$LABEL.tar.gz" ]]; then
        echo "SUCCESS: Backup created with label $LABEL"
        exit 0
    else
        echo "FAIL: Backup directory $LABEL not found"
        exit 1
    fi
else
    echo "FAIL: ods-backup.sh failed to run"
    exit 1
fi
