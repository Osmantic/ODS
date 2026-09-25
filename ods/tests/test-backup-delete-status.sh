#!/usr/bin/env bash
# Regression: a failed backup deletion must reach the CLI exit status.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_BACKUP_ROOT="$(mktemp -d)"
trap 'command rm -rf "$TEST_BACKUP_ROOT" "$MOCK_BIN"' EXIT

# The module checks optional backup dependencies at import time; this unit test
# does not exercise rsync, so provide a harmless dependency probe.
MOCK_BIN="$(mktemp -d)"
printf '#!/bin/sh\nexit 0\n' > "$MOCK_BIN/rsync"
chmod +x "$MOCK_BIN/rsync"
PATH="$MOCK_BIN:$PATH"

ODS_BACKUP_SOURCE_ONLY=true source "$SCRIPT_DIR/../ods-backup.sh"
BACKUP_ROOT="$TEST_BACKUP_ROOT"
mkdir -p "$BACKUP_ROOT/backup-fixture"

rm() {
    return 1
}

if printf 'y\n' | delete_backup backup-fixture >/dev/null 2>&1; then
    echo "FAIL: delete_backup reported success after rm failed"
    exit 1
fi
[[ -d "$BACKUP_ROOT/backup-fixture" ]] || {
    echo "FAIL: failed deletion unexpectedly removed the backup"
    exit 1
}
echo "PASS: failed backup deletion propagates non-zero status"
