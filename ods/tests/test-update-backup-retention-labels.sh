#!/usr/bin/env bash
# `ods-update.sh backup [label]` stores backups as backup-<ts> or
# backup-<label>-<ts>; the dashboard's Backup action passes the label
# "dashboard-<ts>". Retention must keep the newest backups by that creation
# time. Ordering by the whole name sorted every labelled backup after every
# unlabelled one, so a new `backup` run could delete the backup it had just
# created while much older labelled backups survived.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${ODS_UPDATE_UNDER_TEST:-$ROOT_DIR/ods-update.sh}"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ods-retention-labels-XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

[[ -f "$TARGET" ]] || fail "missing $TARGET"
command -v jq >/dev/null 2>&1 || fail "jq is required by ods-update.sh"

FAKE_HOME="$TMP_DIR/home"
BACKUPS="$FAKE_HOME/.ods/backups"
INSTALL="$TMP_DIR/install"
mkdir -p "$BACKUPS" "$INSTALL" "$TMP_DIR/outside"
cp "$TARGET" "$INSTALL/ods-update.sh"
chmod +x "$INSTALL/ods-update.sh"
printf 'GPU_BACKEND=cpu\n' > "$INSTALL/.env"
printf '{"version":"2.6.0"}\n' > "$INSTALL/.version"
printf 'services: {}\n' > "$INSTALL/docker-compose.yml"

# Older backups, labelled and unlabelled, with interleaved creation times.
for name in \
    backup-dashboard-20250101-000000-20250101-000001 \
    backup-20250201-000000 \
    backup-before-upgrade-20250301-000000 \
    backup-dashboard-20250401-000000-20250401-000001; do
    mkdir -p "$BACKUPS/$name"
done
# Not created by this command: no creation timestamp, and a symlink out of the
# backup directory. Retention must leave both alone.
mkdir -p "$BACKUPS/backup-manual-copy"
printf 'keep\n' > "$TMP_DIR/outside/canary.txt"
ln -s "$TMP_DIR/outside" "$BACKUPS/backup-zz-link"

run_backup() {
    local out rc
    set +e
    out="$(cd "$INSTALL" && HOME="$FAKE_HOME" MAX_BACKUPS=3 "$BASH" ./ods-update.sh backup "$@" 2>&1)"
    rc=$?
    set -e
    [[ $rc -eq 0 ]] || fail "ods-update.sh backup $* exited $rc: $(printf '%s' "$out" | tail -n 3 | tr '\n' ' ')"
    printf '%s\n' "$out" | sed 's/\x1b\[[0-9;]*m//g' | sed -n 's/.*Backup created: .*\/\(backup-[^/]*\)$/\1/p'
}

first="$(run_backup)"
[[ -n "$first" ]] || fail "could not identify the unlabelled backup that was created"
[[ -d "$BACKUPS/$first" ]] \
    || fail "retention deleted the backup it had just created ($first)"
pass "an unlabelled backup survives older labelled backups"

[[ -d "$BACKUPS/backup-dashboard-20250401-000000-20250401-000001" \
    && -d "$BACKUPS/backup-before-upgrade-20250301-000000" ]] \
    || fail "a newer labelled backup was pruned instead of an older one"
[[ ! -d "$BACKUPS/backup-dashboard-20250101-000000-20250101-000001" \
    && ! -d "$BACKUPS/backup-20250201-000000" ]] \
    || fail "the two oldest backups by creation time were not pruned"
pass "retention keeps the MAX_BACKUPS newest by creation time across labels"

second="$(run_backup nightly)"
[[ "$second" == backup-nightly-* && -d "$BACKUPS/$second" && -d "$BACKUPS/$first" ]] \
    || fail "a labelled backup or the previous run's backup was pruned"
[[ ! -d "$BACKUPS/backup-before-upgrade-20250301-000000" \
    && -d "$BACKUPS/backup-dashboard-20250401-000000-20250401-000001" ]] \
    || fail "the labelled run did not prune the oldest remaining backup"
pass "a labelled backup is ordered by its creation time, not its label"

[[ -d "$BACKUPS/backup-manual-copy" && -L "$BACKUPS/backup-zz-link" && -f "$TMP_DIR/outside/canary.txt" ]] \
    || fail "retention removed a directory it did not create or followed a symlink"
pass "entries without a creation timestamp and symlinks are left untouched"
