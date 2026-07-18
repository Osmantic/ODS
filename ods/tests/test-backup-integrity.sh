#!/bin/bash
# Basic integrity test for ods-backup.sh checksums + verify

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_BACKUP="$SCRIPT_DIR/../ods-backup.sh"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

pass() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }
info() { echo -e "${BLUE}ℹ${NC} $1"; }

if [[ ! -x "$ODS_BACKUP" ]]; then
  fail "ods-backup.sh not found or not executable at $ODS_BACKUP"
fi

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

# Create a minimal fake ODS directory with some data
FAKE_ODS="$TMP_ROOT/ods"
mkdir -p "$FAKE_ODS/data/open-webui"
mkdir -p "$FAKE_ODS/config"

# ods-backup.sh sources lib/rsync.sh relative to ODS_DIR
mkdir -p "$FAKE_ODS/lib"
cp "$SCRIPT_DIR/../lib/rsync.sh" "$FAKE_ODS/lib/"

# Required by create_manifest()
echo "test" > "$FAKE_ODS/.version"

# Add files
echo "hello" > "$FAKE_ODS/data/open-webui/file.txt"
echo "world" > "$FAKE_ODS/config/settings.json"

BACKUPS_DIR="$TMP_ROOT/backups"

info "Creating backup"
ODS_DIR="$FAKE_ODS" "$ODS_BACKUP" --output "$BACKUPS_DIR" --type full >/dev/null

backup_id="$(ls -1 "$BACKUPS_DIR" | head -n 1)"
[[ -n "$backup_id" ]] || fail "No backup created"

[[ -f "$BACKUPS_DIR/$backup_id/checksums.sha256" ]] || fail "checksums.sha256 not created"
pass "checksums.sha256 created"

info "Verifying backup"
ODS_DIR="$FAKE_ODS" "$ODS_BACKUP" --output "$BACKUPS_DIR" verify "$backup_id" >/dev/null
pass "verify passes on untampered backup"

info "Tampering with a file and expecting verify to fail"
echo "tampered" >> "$BACKUPS_DIR/$backup_id/data/open-webui/file.txt"

set +e
ODS_DIR="$FAKE_ODS" "$ODS_BACKUP" --output "$BACKUPS_DIR" verify "$backup_id" >/dev/null 2>&1
rc=$?
set -e

if [[ $rc -eq 0 ]]; then
  fail "verify unexpectedly succeeded after tampering"
fi

pass "verify fails after tampering"

# ── Lifecycle: list, retention, and delete must see the script's own IDs ──
# Backup IDs are YYYYMMDD-HHMMSS (one hyphen); a glob requiring two hyphens
# regressed list/retention into ignoring every backup this script creates.

LIFECYCLE_DIR="$TMP_ROOT/lifecycle-backups"
mkdir -p "$LIFECYCLE_DIR"

# Six pre-existing backups in the script's own ID format, plus operator
# debris that retention must never delete.
for i in 1 2 3 4 5 6; do
  d="$LIFECYCLE_DIR/2026010${i}-00000${i}"
  mkdir -p "$d"
  echo '{"backup_type": "user-data", "description": "old"}' > "$d/manifest.json"
done
mkdir -p "$LIFECYCLE_DIR/my-notes"

info "Listing pre-existing backups"
list_out=$(ODS_DIR="$FAKE_ODS" "$ODS_BACKUP" --output "$LIFECYCLE_DIR" --list)
echo "$list_out" | grep -q "20260101-000001" || fail "--list does not show own-format backup IDs"
if echo "$list_out" | grep -q "my-notes"; then
  fail "--list shows non-backup directories"
fi
pass "--list shows own-format backup IDs and skips other directories"

info "Running backup with RETENTION_COUNT=5"
ODS_DIR="$FAKE_ODS" RETENTION_COUNT=5 "$ODS_BACKUP" --output "$LIFECYCLE_DIR" --type config >/dev/null

[[ ! -d "$LIFECYCLE_DIR/20260101-000001" ]] || fail "retention kept the oldest backup beyond RETENTION_COUNT"
[[ ! -d "$LIFECYCLE_DIR/20260102-000002" ]] || fail "retention kept the second-oldest backup beyond RETENTION_COUNT"
[[ -d "$LIFECYCLE_DIR/20260103-000003" ]] || fail "retention deleted a backup inside RETENTION_COUNT"
[[ -d "$LIFECYCLE_DIR/my-notes" ]] || fail "retention deleted an unrelated directory"
pass "retention prunes oldest own-format backups and leaves other directories"

info "Deleting a compressed backup by bare ID"
(cd "$LIFECYCLE_DIR" && mkdir -p 20260601-120000 && echo x > 20260601-120000/f \
  && tar czf 20260601-120000.tar.gz 20260601-120000 && rm -rf 20260601-120000)
echo y | ODS_DIR="$FAKE_ODS" "$ODS_BACKUP" --output "$LIFECYCLE_DIR" -d 20260601-120000 >/dev/null
[[ ! -f "$LIFECYCLE_DIR/20260601-120000.tar.gz" ]] || fail "delete left the compressed backup behind"
pass "delete removes compressed backups by bare ID"
