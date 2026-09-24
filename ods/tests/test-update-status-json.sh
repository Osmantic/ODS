#!/usr/bin/env bash
# Public update-manager contract for machine-readable lifecycle status.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

INSTALL_DIR="$TMP_DIR/install"
TEST_HOME="$TMP_DIR/home with space"
mkdir -p \
    "$INSTALL_DIR/data/backups/pre-update-20260828-120000" \
    "$INSTALL_DIR/data/backups/pre-update-20260829-120000" \
    "$TEST_HOME/.ods/backups/backup-alpha-20260828-120000" \
    "$TEST_HOME/.ods/backups/backup-20260829-120000"
cp "$ROOT_DIR/ods-update.sh" "$INSTALL_DIR/ods-update.sh"

cat > "$INSTALL_DIR/.version" <<'JSON'
{
  "version": "2.6.0",
  "last_check": "2026-08-29T08:00:00Z",
  "last_update": "2026-08-28T09:30:00Z",
  "last_rollback_point": "data/backups/pre-update-20260829-120000"
}
JSON

json_out=$(HOME="$TEST_HOME" UPDATE_CHANNEL=beta MAX_BACKUPS=12 \
    bash "$INSTALL_DIR/ods-update.sh" status --json)
jq -e --arg install_path "$INSTALL_DIR" --arg backup_path "$TEST_HOME/.ods/backups" '
    .schema_version == "ods.update-status.v1" and
    .version == "2.6.0" and
    .install_path == $install_path and
    .update_channel == "beta" and
    .last_check == "2026-08-29T08:00:00Z" and
    .last_update == "2026-08-28T09:30:00Z" and
    .retention_limit == 12 and
    .rollback.count == 2 and
    .rollback.last_point == "data/backups/pre-update-20260829-120000" and
    .backups.path == $backup_path and
    .backups.count == 2
' <<< "$json_out" >/dev/null || {
    printf 'FAIL: update status JSON contract changed\n' >&2
    exit 1
}
printf 'PASS: update status exposes version, retention, and recovery inventory as JSON\n'

human_out=$(HOME="$TEST_HOME" UPDATE_CHANNEL=beta MAX_BACKUPS=12 \
    bash "$INSTALL_DIR/ods-update.sh" status)
grep -qF 'Rollback snaps: 2' <<< "$human_out" \
    || { printf 'FAIL: human rollback status regressed\n' >&2; exit 1; }
grep -qF 'General backups: 2' <<< "$human_out" \
    || { printf 'FAIL: human backup status regressed\n' >&2; exit 1; }
printf 'PASS: existing human update status remains intact\n'

FRESH_INSTALL="$TMP_DIR/fresh-install"
FRESH_HOME="$TMP_DIR/fresh-home"
mkdir -p "$FRESH_INSTALL" "$FRESH_HOME"
cp "$ROOT_DIR/ods-update.sh" "$FRESH_INSTALL/ods-update.sh"
fresh_json=$(HOME="$FRESH_HOME" bash "$FRESH_INSTALL/ods-update.sh" status --json)
jq -e '
    .version == "0.0.0" and
    .last_check == null and
    .last_update == null and
    .rollback.count == 0 and
    .rollback.last_point == null and
    .backups.count == 0
' <<< "$fresh_json" >/dev/null || {
    printf 'FAIL: fresh-install status did not expose empty lifecycle state\n' >&2
    exit 1
}
printf 'PASS: fresh installs expose null timestamps and empty recovery inventory\n'
