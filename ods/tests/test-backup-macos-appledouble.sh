#!/bin/bash
# Compressed backups must restore on macOS.
#
# macOS `tar` (bsdtar) writes AppleDouble `._*` metadata companions when the
# staged files carry extended attributes (com.apple.provenance is set by the OS
# on ordinary files). The top-level `._<backup_id>` entry then fails
# backup-archive.py's member check on restore — its first path component is not
# the backup id — so the whole archive is rejected and macOS users cannot
# recover their own backups. `compress_backup` sets COPYFILE_DISABLE so bsdtar
# omits that metadata (a no-op for GNU tar on Linux).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_BACKUP="$SCRIPT_DIR/../ods-backup.sh"
ODS_RESTORE="$SCRIPT_DIR/../ods-restore.sh"

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'
pass() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }
info() { echo -e "${BLUE}ℹ${NC} $1"; }

[[ -x "$ODS_BACKUP" ]] || fail "ods-backup.sh not found or not executable"

# --- Source contract: the compress step must disable AppleDouble metadata -----
# Has teeth on every platform, including Linux CI where bsdtar's `._*` behavior
# cannot be reproduced: a future edit that drops the guard fails here.
if ! grep -Eq 'COPYFILE_DISABLE=[^ ]* +tar +czf' "$ODS_BACKUP"; then
    fail "compress_backup must run 'tar czf' under COPYFILE_DISABLE to omit AppleDouble metadata"
fi
pass "compress_backup guards the tar invocation with COPYFILE_DISABLE"

# --- Behavioral: the produced archive must not escape <backup_id>/ ------------
# Has teeth on macOS (where bsdtar would otherwise inject `._<backup_id>`); a
# harmless sanity check on Linux.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
SRC="$TMP/src"; DST="$TMP/dst"
mkdir -p "$SRC/config" "$SRC/data/open-webui" "$SRC/data/models" "$SRC/lib" \
         "$DST/data" "$DST/lib" "$DST/.backups"
printf '1.0.0' > "$SRC/.version"
printf 'test-env-value' > "$SRC/.env"
printf 'compose-content' > "$SRC/docker-compose.yml"
printf 'config-data' > "$SRC/config/settings.json"
printf 'user-data-file' > "$SRC/data/open-webui/data.txt"
printf 'gguf-weights' > "$SRC/data/models/weights.gguf"
cp "$SCRIPT_DIR/../lib/rsync.sh" "$SCRIPT_DIR/../lib/backup-paths.sh" "$SRC/lib/"
cp "$SCRIPT_DIR/../lib/rsync.sh" "$SCRIPT_DIR/../lib/backup-paths.sh" "$DST/lib/"
printf 'compose-content' > "$DST/docker-compose.yml"

info "Creating compressed backup"
ODS_DIR="$SRC" bash "$ODS_BACKUP" --type full --compress >/dev/null 2>&1 \
    || fail "Compressed backup failed"
TARBALL="$(ls -1 "$SRC/.backups"/*.tar.gz 2>/dev/null | head -n1)"
[[ -n "$TARBALL" ]] || fail "No compressed backup created"
BACKUP_ID="$(basename "$TARBALL" .tar.gz)"

# Every member's first path component must be the backup id. An AppleDouble
# `._<backup_id>` sibling (or any stray top-level entry) breaks this.
escapees="$(tar tzf "$TARBALL" | awk -v id="$BACKUP_ID" -F/ '$1 != id { print }')"
if [[ -n "$escapees" ]]; then
    echo "$escapees" | sed 's/^/    stray member: /'
    fail "Archive contains members outside ${BACKUP_ID}/ (backup-archive.py will reject it)"
fi
pass "Archive members all live under ${BACKUP_ID}/"

# --- Full round-trip: restore must succeed and preserve content ---------------
if [[ -x "$ODS_RESTORE" ]]; then
    cp "$TARBALL" "$DST/.backups/"
    info "Restoring the compressed backup"
    ODS_DIR="$DST" bash "$ODS_RESTORE" -f "$BACKUP_ID" >/dev/null 2>&1 \
        || fail "Compressed restore failed"
    [[ "$(cat "$DST/.env" 2>/dev/null)" == "test-env-value" ]] \
        || fail ".env content mismatch after compressed restore"
    [[ "$(cat "$DST/data/models/weights.gguf" 2>/dev/null)" == "gguf-weights" ]] \
        || fail "model weights lost after compressed restore"
    pass "Compressed backup restored with intact content"
fi

echo "All macOS AppleDouble backup checks passed."
