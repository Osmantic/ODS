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

echo ""
echo -e "${GREEN}✓ Round-trip backup/restore test passed${NC}"
