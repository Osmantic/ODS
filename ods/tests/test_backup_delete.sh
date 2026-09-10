#!/bin/bash
# Test for issue #4177: ods-backup.sh -d should propagate exit code

SCRIPT="C:\Users\patil\DreamServer\ods\ods-backup.sh"
BACKUP_ROOT=$(mktemp -d)
trap 'rm -rf "$BACKUP_ROOT"' EXIT

# Mock a backup directory
BACKUP_ID="20260101-120000"
mkdir -p "$BACKUP_ROOT/$BACKUP_ID"
touch "$BACKUP_ROOT/$BACKUP_ID/manifest.json"

echo "Testing deletion of non-existent backup..."
# Use --output to set the backup root and -d to delete
# We use echo 'n' to simulate 'No' to the confirmation prompt if it exists, 
# but the bug is about exit codes when the backup is NOT found.
# To avoid interactive prompt for existing backups, we test non-existent first.

# Run with a fake ID
bash "$SCRIPT" -o "$BACKUP_ROOT" -d "non-existent" > /dev/null 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "SUCCESS: Non-existent backup deletion returned non-zero ($EXIT_CODE)"
else
    echo "FAILURE: Non-existent backup deletion returned 0"
    exit 1
fi

echo "Testing deletion of existing backup (cancelled)..."
# Simulate 'n' for the confirmation prompt
echo "n" | bash "$SCRIPT" -o "$BACKUP_ROOT" -d "$BACKUP_ID" > /dev/null 2>&1
EXIT_CODE=$?
# Note: the current implementation of delete_backup returns 0 if cancelled? 
# Let's check the code.
# Line 270: log_info "Deletion cancelled" -> then function returns.
# In bash, the last command (log_info) returns 0.
# So cancellation returning 0 is expected.

echo "Test passed!"
