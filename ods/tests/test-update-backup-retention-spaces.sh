#!/usr/bin/env bash
# `ods-update.sh backup` prunes old backups down to MAX_BACKUPS. The retention
# loop used `for dir in $backup_dirs`, which word-splits every path, so a
# BACKUP_DIR containing a space (HOME=/mnt/c/Users/First Last on Windows/WSL)
# produced path fragments: retention silently pruned nothing, and `rm -rf` ran
# against a partial path that could match an unintended directory.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${ODS_UPDATE_UNDER_TEST:-$ROOT_DIR/ods-update.sh}"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ods-retention-XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

[[ -f "$TARGET" ]] || fail "missing $TARGET"
command -v jq >/dev/null 2>&1 || fail "jq is required by ods-update.sh"

# HOME with a space -> BACKUP_DIR with a space. This is the whole point.
FAKE_HOME="$TMP_DIR/My Home"
BACKUPS="$FAKE_HOME/.ods/backups"
INSTALL="$TMP_DIR/install"
mkdir -p "$BACKUPS" "$INSTALL"
cp "$TARGET" "$INSTALL/ods-update.sh"
chmod +x "$INSTALL/ods-update.sh"
printf 'GPU_BACKEND=cpu\n' > "$INSTALL/.env"
printf '{"version":"2.6.0"}\n' > "$INSTALL/.version"
printf 'services: {}\n' > "$INSTALL/docker-compose.yml"

# Pre-existing backups, oldest first by name. With MAX_BACKUPS=2 the newest 2
# survive (the run also creates one, so the oldest must be pruned).
for stamp in 20250101-010101 20250202-020202 20250303-030303 20250404-040404; do
    mkdir -p "$BACKUPS/backup-$stamp"
    printf 'sentinel\n' > "$BACKUPS/backup-$stamp/marker.txt"
done
before=$(find "$BACKUPS" -maxdepth 1 -type d -name 'backup-*' | wc -l | tr -d ' ')
[[ "$before" -eq 4 ]] || fail "fixture setup: expected 4 backups, got $before"

# A sibling directory whose name is the first word of the spaced path. A
# word-split `rm -rf` fragment would target this; it must survive untouched.
mkdir -p "$TMP_DIR/My"
printf 'do-not-delete\n' > "$TMP_DIR/My/canary.txt"

set +e
out="$(cd "$INSTALL" && HOME="$FAKE_HOME" MAX_BACKUPS=2 "$BASH" ./ods-update.sh backup 2>&1)"
rc=$?
set -e
[[ $rc -eq 0 ]] || fail "ods-update.sh backup exited $rc: $(printf '%s' "$out" | tail -n 3 | tr '\n' ' ')"

[[ -f "$TMP_DIR/My/canary.txt" ]] \
    || fail "a word-split path fragment deleted an unrelated sibling directory"
pass "no unrelated directory outside the backup dir was removed"

remaining=$(find "$BACKUPS" -maxdepth 1 -type d -name 'backup-*' | wc -l | tr -d ' ')
[[ "$remaining" -eq 2 ]] \
    || fail "retention did not prune to MAX_BACKUPS=2 on a path with spaces (got $remaining dirs); backups were never removed"
pass "retention prunes to MAX_BACKUPS when the backup path contains spaces"

# The two survivors must be the newest by name, and the oldest must be gone.
[[ ! -d "$BACKUPS/backup-20250101-010101" ]] \
    || fail "the oldest backup was not pruned"
[[ -d "$BACKUPS/backup-20250404-040404" ]] \
    || fail "a recent backup was pruned instead of an old one"
pass "the oldest backups are the ones removed"
