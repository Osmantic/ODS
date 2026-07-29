#!/bin/bash
# Round-trip backup/restore integration test
# Creates a backup, then restores it to a different location, and validates contents.

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

# Create source ODS directory with minimal data
SRC="$TMP/src"
mkdir -p "$SRC/data/open-webui"
mkdir -p "$SRC/config"
echo "1.0.0" > "$SRC/.version"
echo "test-env-value" > "$SRC/.env"
echo "compose-content" > "$SRC/docker-compose.yml"
echo "config-data" > "$SRC/config/settings.json"
echo "user-data-file" > "$SRC/data/open-webui/data.txt"

# Both scripts source lib/rsync.sh relative to ODS_DIR
mkdir -p "$SRC/lib"
cp "$SCRIPT_DIR/../lib/rsync.sh" "$SRC/lib/"

info "Creating backup from source"
ODS_DIR="$SRC" bash "$ODS_BACKUP" --type full >/dev/null 2>&1 || fail "Backup failed"

# Find the backup ID
BACKUP_ID=$(ls -1 "$SRC/.backups" | head -n 1)
[[ -n "$BACKUP_ID" ]] || fail "No backup created"
pass "Backup created: $BACKUP_ID"

# Create destination ODS directory (empty)
DST="$TMP/dst"
mkdir -p "$DST/data"
mkdir -p "$DST/.backups"
mkdir -p "$DST/lib"
cp "$SCRIPT_DIR/../lib/rsync.sh" "$DST/lib/"

info "Restoring backup to destination"
# Copy backup to destination's backup root
cp -r "$SRC/.backups/$BACKUP_ID" "$DST/.backups/$BACKUP_ID"

# Restore (force, no interactive prompts)
ODS_DIR="$DST" bash "$ODS_RESTORE" -f "$BACKUP_ID" >/dev/null 2>&1 || fail "Restore failed"
pass "Restore completed"

info "Validating restored contents"

# Check key files exist
[[ -f "$DST/.version" ]] || fail "Missing .version after restore"
[[ -f "$DST/.env" ]] || fail "Missing .env after restore"
[[ -f "$DST/docker-compose.yml" ]] || fail "Missing docker-compose.yml after restore"
[[ -d "$DST/config" ]] || fail "Missing config/ after restore"
[[ -f "$DST/config/settings.json" ]] || fail "Missing config/settings.json after restore"
[[ -d "$DST/data/open-webui" ]] || fail "Missing data/open-webui after restore"
[[ -f "$DST/data/open-webui/data.txt" ]] || fail "Missing data/open-webui/data.txt after restore"

pass "All expected files/dirs present after restore"

# Validate content integrity
[[ "$(cat "$DST/.version")" == "1.0.0" ]] || fail ".version content mismatch"
[[ "$(cat "$DST/.env")" == "test-env-value" ]] || fail ".env content mismatch"
[[ "$(cat "$DST/docker-compose.yml")" == "compose-content" ]] || fail "docker-compose.yml content mismatch"
[[ "$(cat "$DST/config/settings.json")" == "config-data" ]] || fail "config/settings.json content mismatch"
[[ "$(cat "$DST/data/open-webui/data.txt")" == "user-data-file" ]] || fail "data/open-webui/data.txt content mismatch"

pass "All file contents match after restore"

# ── Compressed round-trip ─────────────────────────────────────────────
# extract_backup's stdout is command-substituted into the backup path, so a
# log line leaking to stdout garbles the path and fails every .tar.gz restore.

info "Creating compressed backup from source"
ODS_DIR="$SRC" bash "$ODS_BACKUP" --type config --compress >/dev/null 2>&1 || fail "Compressed backup failed"

TARBALL=$(ls -1 "$SRC/.backups"/*.tar.gz 2>/dev/null | head -n 1)
[[ -n "$TARBALL" ]] || fail "No compressed backup created"
CBACKUP_ID=$(basename "$TARBALL" .tar.gz)
pass "Compressed backup created: $CBACKUP_ID.tar.gz"

DST2="$TMP/dst2"
mkdir -p "$DST2/data" "$DST2/.backups" "$DST2/lib"
cp "$SCRIPT_DIR/../lib/rsync.sh" "$DST2/lib/"
echo "compose-content" > "$DST2/docker-compose.yml"
cp "$TARBALL" "$DST2/.backups/"

info "Restoring from compressed backup"
ODS_DIR="$DST2" bash "$ODS_RESTORE" -f "$CBACKUP_ID" >/dev/null 2>&1 || fail "Compressed restore failed"
[[ -f "$DST2/.env" ]] || fail "Missing .env after compressed restore"
[[ "$(cat "$DST2/.env")" == "test-env-value" ]] || fail ".env content mismatch after compressed restore"
pass "Compressed backup restores correctly"

# ── Interactive selection ─────────────────────────────────────────────
# select_backup's stdout is command-substituted into backup_id, so the list
# and prompt must print to stderr or the selection table is swallowed and
# the captured ID is garbage.

info "Restoring via interactive selection (dry run)"
interactive_err=$(printf '1\n' | ODS_DIR="$DST2" bash "$ODS_RESTORE" -f -d 2>&1 >/dev/null) \
    || fail "Interactive selection restore failed"
echo "$interactive_err" | grep -q "Available Backups" || fail "Selection table not shown to the user (stderr)"
pass "Interactive selection shows the table and resolves a clean backup ID"

echo ""
echo -e "${GREEN}✓ Round-trip backup/restore test passed${NC}"
