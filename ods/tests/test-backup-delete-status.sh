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
mkdir -p "$BACKUP_ROOT/backup-fixture-20261009-000000"
printf '{"manifest_version":"1.0","backup_id":"backup-fixture-20261009-000000","backup_type":"config"}\n' \
    > "$BACKUP_ROOT/backup-fixture-20261009-000000/manifest.json"

python3() {
    printf called > "$TEST_BACKUP_ROOT/helper-called"
    return 1
}

if printf 'y\n' | delete_backup backup-fixture-20261009-000000 >/dev/null 2>&1; then
    echo "FAIL: delete_backup reported success after its deletion helper failed"
    exit 1
fi
[[ -d "$BACKUP_ROOT/backup-fixture-20261009-000000" ]] || {
    echo "FAIL: failed deletion unexpectedly removed the backup"
    exit 1
}
[[ -f "$TEST_BACKUP_ROOT/helper-called" ]] || { echo "FAIL: fixture never reached the deletion helper"; exit 1; }
echo "PASS: failed backup deletion propagates non-zero status"
