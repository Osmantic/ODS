#!/bin/bash
# Minimal tests for backup/restore disk space preflight.
# Uses stubbed df output to force the low-space path.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_BACKUP="$SCRIPT_DIR/../ods-backup.sh"
ODS_RESTORE="$SCRIPT_DIR/../ods-restore.sh"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

pass() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }
info() { echo -e "${BLUE}ℹ${NC} $1"; }

[[ -x "$ODS_BACKUP" ]] || fail "ods-backup.sh not found or not executable"
[[ -x "$ODS_RESTORE" ]] || fail "ods-restore.sh not found or not executable"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Stub df to pretend disk is almost full (1K-blocks available = 1)
STUB_BIN="$TMP/bin"
mkdir -p "$STUB_BIN"
cat > "$STUB_BIN/df" <<'SH'
#!/bin/sh
# POSIX df -P format
printf "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
printf "/dev/fake 100 99 1 99%% /\n"
SH
chmod +x "$STUB_BIN/df"

# Create a minimal fake ODS dir with some data so the estimated size is > ~0
FAKE_ODS="$TMP/ods"
mkdir -p "$FAKE_ODS/data/open-webui"
mkdir -p "$FAKE_ODS/.backups"
echo test > "$FAKE_ODS/.version"
echo hello > "$FAKE_ODS/data/open-webui/file.txt"

info "Backup should fail preflight when disk is low"
set +e
out=$(PATH="$STUB_BIN:$PATH" ODS_DIR="$FAKE_ODS" bash "$ODS_BACKUP" --type user-data 2>&1)
rc=$?
set -e

if [[ $rc -eq 0 ]]; then
  fail "Expected backup to fail due to low disk space"
fi

echo "$out" | grep -q "Not enough disk space" || fail "Expected 'Not enough disk space' message"
pass "Backup preflight blocks low-space backup"

# Create a minimal backup directory for restore size estimation
BID="20260101-000000"
B="$FAKE_ODS/.backups/$BID"
mkdir -p "$B"
cat > "$B/manifest.json" <<'JSON'
{
  "manifest_version": "1.0",
  "backup_date": "2026-01-01T00:00:00Z",
  "backup_id": "20260101-000000",
  "backup_type": "user-data",
  "ods_version": "test",
  "hostname": "test",
  "description": "test",
  "contents": {"user_data": true, "config": false, "cache": false}
}
JSON

# Make backup big enough to exceed the stubbed 1KB free space
head -c 4096 /dev/zero > "$B/somefile" 2>/dev/null || yes x | head -c 4096 > "$B/somefile"

info "Restore should fail preflight when disk is low"
set +e
out=$(PATH="$STUB_BIN:$PATH" ODS_DIR="$FAKE_ODS" bash "$ODS_RESTORE" -f "$BID" 2>&1 <<<'y')
rc=$?
set -e

if [[ $rc -eq 0 ]]; then
  fail "Expected restore to fail due to low disk space"
fi

echo "$out" | grep -q "Not enough disk space" || fail "Expected 'Not enough disk space' message"
pass "Restore preflight blocks low-space restore"
