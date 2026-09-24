#!/usr/bin/env bash
# Regression coverage for explicit backup IDs at the restore CLI boundary.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/ods-restore-id.XXXXXX")"
trap 'rm -rf "$FIXTURE"' EXIT

pass_count=0

pass() {
    echo "PASS: $1"
    pass_count=$((pass_count + 1))
}

fail() {
    echo "FAIL: $1" >&2
    exit 1
}

mkdir -p "$FIXTURE/.backups" "$FIXTURE/data" "$FIXTURE/lib"
cp "$ROOT_DIR/lib/rsync.sh" "$FIXTURE/lib/rsync.sh"

write_manifest() {
    local directory="$1"
    local backup_id="$2"

    mkdir -p "$directory"
    cat > "$directory/manifest.json" <<JSON
{
  "manifest_version": "1.0",
  "backup_date": "2026-08-30T00:00:00Z",
  "backup_id": "$backup_id",
  "backup_type": "user-data",
  "ods_version": "test",
  "hostname": "test",
  "description": "boundary fixture",
  "contents": {"user_data": true, "config": false, "cache": false}
}
JSON
}

run_restore() {
    local output_file="$1"
    shift

    set +e
    ODS_DIR="$FIXTURE" bash "$ROOT_DIR/ods-restore.sh" \
        --dry-run --skip-verify "$@" >"$output_file" 2>&1
    local status=$?
    set -e
    return "$status"
}

assert_rejected() {
    local description="$1"
    local backup_id="$2"
    local output_file="$FIXTURE/output.log"

    if run_restore "$output_file" "$backup_id"; then
        fail "$description was accepted"
    fi
    grep -qF "Invalid backup ID" "$output_file" \
        || fail "$description did not explain the backup-ID constraint"
    pass "$description is rejected"
}

# This directory is a sibling of .backups. Before validation, ../external
# passed extraction and manifest checks and became the restore source.
write_manifest "$FIXTURE/external" "external"
assert_rejected "parent-directory traversal" ../external

assert_rejected "dot ID" .
assert_rejected "backslash-separated ID" 'group\backup'
assert_rejected "control-character ID" $'bad\nbackup'

# Backup IDs produced by ods-backup.sh remain accepted.
valid_id="20260830-120000"
write_manifest "$FIXTURE/.backups/$valid_id" "$valid_id"
if ! run_restore "$FIXTURE/valid.log" "$valid_id"; then
    fail "valid generated backup ID was rejected"
fi
grep -qF "DRY RUN - Preview of restore operation" "$FIXTURE/valid.log" \
    || fail "valid backup did not reach the restore preview"
pass "valid generated backup ID reaches dry-run preview"

echo "Result: $pass_count passed"
