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
mkdir -p "$SRC/data/open-webui" "$SRC/data/hermes/sessions" "$SRC/data/persona"
mkdir -p "$SRC/data/n8n"
mkdir -p "$SRC/config"
mkdir -p "$SRC/models"
echo "1.0.0" > "$SRC/.version"
echo "test-env-value" > "$SRC/.env"
echo "compose-content" > "$SRC/docker-compose.yml"
echo "config-data" > "$SRC/config/settings.json"
echo "user-data-file" > "$SRC/data/open-webui/data.txt"
echo "hermes-session" > "$SRC/data/hermes/sessions/session.jsonl"
echo "persona-soul" > "$SRC/data/persona/SOUL.md"
echo "workflow-data-file" > "$SRC/data/n8n/workflow.txt"
echo "model-cache-file" > "$SRC/models/model.gguf"

# Both scripts source lib/rsync.sh relative to ODS_DIR
mkdir -p "$SRC/lib"
cp "$SCRIPT_DIR/../lib/rsync.sh" "$SCRIPT_DIR/../lib/backup-paths.sh" "$SRC/lib/"

info "Creating backup from source"
ODS_DIR="$SRC" bash "$ODS_BACKUP" --type full >/dev/null 2>&1 || fail "Backup failed"

# Find the backup ID
BACKUP_ID=$(ls -1 "$SRC/.backups" | head -n 1)
[[ -n "$BACKUP_ID" ]] || fail "No backup created"
pass "Backup created: $BACKUP_ID"
[[ -f "$SRC/.backups/$BACKUP_ID/data/open-webui/data.txt" ]] \
    || fail "Full backup lost Open WebUI data"
[[ -f "$SRC/.backups/$BACKUP_ID/data/n8n/workflow.txt" ]] \
    || fail "Full backup lost n8n data"
[[ -f "$SRC/.backups/$BACKUP_ID/config/settings.json" ]] \
    || fail "Full backup lost config data"
[[ -f "$SRC/.backups/$BACKUP_ID/models/model.gguf" ]] \
    || fail "Full backup lost model cache"
[[ -f "$SRC/.backups/$BACKUP_ID/manifest.json" ]] \
    || fail "Full backup lost its manifest"
[[ -f "$SRC/.backups/$BACKUP_ID/.env" ]] \
    || fail "Full backup lost its environment config"

BACKUP_DIR="$SRC/.backups/$BACKUP_ID"
[[ -f "$BACKUP_DIR/data/hermes/sessions/session.jsonl" ]] \
    || fail "Backup omitted data/hermes"
[[ -f "$BACKUP_DIR/data/persona/SOUL.md" ]] \
    || fail "Backup omitted data/persona"
jq -e '.paths.data_hermes == "data/hermes" and .paths.data_persona == "data/persona"' \
    "$BACKUP_DIR/manifest.json" >/dev/null \
    || fail "Manifest omitted Hermes or persona backup paths"
pass "Hermes and persona data are present in the backup and manifest"

# Create destination ODS directory (empty)
DST="$TMP/dst"
mkdir -p "$DST/data"
mkdir -p "$DST/.backups"
mkdir -p "$DST/lib"
cp "$SCRIPT_DIR/../lib/rsync.sh" "$SCRIPT_DIR/../lib/backup-paths.sh" "$DST/lib/"

info "Restoring backup to destination"
# Copy backup to destination's backup root
cp -r "$SRC/.backups/$BACKUP_ID" "$DST/.backups/$BACKUP_ID"
mkdir -p "$DST/data/open-webui"
echo "created-after-backup" > "$DST/data/open-webui/local-only.txt"

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
[[ -f "$DST/data/hermes/sessions/session.jsonl" ]] || fail "Missing Hermes session after restore"
[[ -f "$DST/data/persona/SOUL.md" ]] || fail "Missing persona SOUL.md after restore"
[[ -f "$DST/data/n8n/workflow.txt" ]] || fail "Missing data/n8n/workflow.txt after restore"
[[ -f "$DST/data/open-webui/local-only.txt" ]] \
    || fail "Restore deleted a file created after the backup"

pass "All expected files/dirs present after restore"

# Validate content integrity
[[ "$(cat "$DST/.version")" == "1.0.0" ]] || fail ".version content mismatch"
[[ "$(cat "$DST/.env")" == "test-env-value" ]] || fail ".env content mismatch"
[[ "$(cat "$DST/docker-compose.yml")" == "compose-content" ]] || fail "docker-compose.yml content mismatch"
[[ "$(cat "$DST/config/settings.json")" == "config-data" ]] || fail "config/settings.json content mismatch"
[[ "$(cat "$DST/data/open-webui/data.txt")" == "user-data-file" ]] || fail "data/open-webui/data.txt content mismatch"
[[ "$(cat "$DST/data/hermes/sessions/session.jsonl")" == "hermes-session" ]] \
    || fail "Hermes session content mismatch"
[[ "$(cat "$DST/data/persona/SOUL.md")" == "persona-soul" ]] \
    || fail "persona SOUL.md content mismatch"

pass "All file contents match after restore"

# Exercise the older macOS rsync branch, where `--info=progress2` is absent and
# the helper falls back to `--progress`. The fallback must keep the same
# additive contract instead of pruning files created after the snapshot.
OLD_RSYNC_SRC="$TMP/old-rsync-src"
OLD_RSYNC_DST="$TMP/old-rsync-dst"
OLD_RSYNC_BIN="$TMP/old-rsync-bin"
mkdir -p "$OLD_RSYNC_SRC" "$OLD_RSYNC_DST" "$OLD_RSYNC_BIN"
echo "from-backup" > "$OLD_RSYNC_SRC/restored.txt"
echo "created-after-backup" > "$OLD_RSYNC_DST/live-only.txt"
export ODS_TEST_REAL_RSYNC="$(command -v rsync)"
cat > "$OLD_RSYNC_BIN/rsync" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "--help" ]]; then
    echo "rsync 2.x compatibility fixture"
    exit 0
fi
exec "$ODS_TEST_REAL_RSYNC" "$@"
EOF
chmod +x "$OLD_RSYNC_BIN/rsync"
PATH="$OLD_RSYNC_BIN:$PATH" bash -c '
    source "$1"
    rsync_with_progress "$2/" "$3/" "Testing legacy rsync fallback"
' _ "$SCRIPT_DIR/../lib/rsync.sh" "$OLD_RSYNC_SRC" "$OLD_RSYNC_DST" >/dev/null \
    || fail "Legacy rsync fallback failed"
[[ -f "$OLD_RSYNC_DST/restored.txt" ]] || fail "Legacy rsync fallback did not restore source data"
[[ -f "$OLD_RSYNC_DST/live-only.txt" ]] || fail "Legacy rsync fallback deleted live data"
pass "Legacy rsync fallback remains additive"

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
cp "$SCRIPT_DIR/../lib/rsync.sh" "$SCRIPT_DIR/../lib/backup-paths.sh" "$DST2/lib/"
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
