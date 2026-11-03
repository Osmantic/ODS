#!/usr/bin/env bash
# Regression: `ods-backup.sh --delete` must only remove backup IDs, never the
# backup root or an operator's unrelated entries beside the backups.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_BACKUP_ROOT="$(mktemp -d)"
MOCK_BIN="$(mktemp -d)"
trap 'command rm -rf "$TEST_BACKUP_ROOT" "$MOCK_BIN"' EXIT

# The module probes rsync at source time; deletion never runs it.
printf '#!/bin/sh\nexit 0\n' > "$MOCK_BIN/rsync"
chmod +x "$MOCK_BIN/rsync"
PATH="$MOCK_BIN:$PATH"

ODS_BACKUP_SOURCE_ONLY=true source "$SCRIPT_DIR/../ods-backup.sh"
BACKUP_ROOT="$TEST_BACKUP_ROOT/.backups"
mkdir -p "$BACKUP_ROOT/operator-notes" "$BACKUP_ROOT/20260101-000000" "$BACKUP_ROOT/pre-update-20260102-000000"
touch "$BACKUP_ROOT/keep.txt" "$BACKUP_ROOT/manual-20260103-000000.tar.gz"

for bad in . operator-notes keep.txt ""; do
    if printf 'y\n' | delete_backup "$bad" >/dev/null 2>&1; then
        echo "FAIL: delete_backup accepted non-backup ID '$bad'"
        exit 1
    fi
done
[[ -d "$BACKUP_ROOT/operator-notes" && -f "$BACKUP_ROOT/keep.txt" ]] || {
    echo "FAIL: delete_backup removed a non-backup entry"
    exit 1
}

for good in 20260101-000000 pre-update-20260102-000000 manual-20260103-000000; do
    printf 'y\n' | delete_backup "$good" >/dev/null 2>&1
done
[[ ! -e "$BACKUP_ROOT/20260101-000000" && ! -e "$BACKUP_ROOT/pre-update-20260102-000000" \
    && ! -e "$BACKUP_ROOT/manual-20260103-000000.tar.gz" ]] || {
    echo "FAIL: delete_backup did not remove a valid backup ID"
    exit 1
}
echo "PASS: backup deletion accepts only backup IDs"
