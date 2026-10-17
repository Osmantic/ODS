#!/usr/bin/env bash
# Contract: backup/restore help examples use the real backup ID format
# (backup-<pid>-<YYYYMMDD-HHMMSS>, see ods-backup.sh) and the post-backup
# restore hint names a command a user can actually run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FAILED=0
fail() { echo "FAIL: $1"; FAILED=1; }

ID_RE='backup-[0-9]+-[0-9]{8}-[0-9]{6}'

# The ID generator itself must still produce the documented format.
grep -q 'backup_id="backup-\$\$-\$(date +%Y%m%d-%H%M%S)"' "$ROOT/ods-backup.sh" \
    || fail "ods-backup.sh no longer generates backup-\$\$-<ts> IDs; update this contract"

# Any timestamp-shaped example in help text must be a full backup ID.
for f in ods-backup.sh ods-restore.sh ods-cli; do
    while IFS= read -r line; do
        if ! grep -Eq "$ID_RE" <<<"$line"; then
            fail "$f: bare timestamp example: $line"
        fi
    done < <(grep -E '(restore|verify|-d|-f|--data-only|e\.g\.,) [^ ]*[0-9]{8}-[0-9]{6}' "$ROOT/$f" || true)  # no match is a pass
done

# The restore hint must not name ods-restore.sh bare (not on PATH).
if grep -Eq 'echo "  ods-restore\.sh ' "$ROOT/ods-backup.sh"; then
    fail "ods-backup.sh restore hint names bare ods-restore.sh, which is not on PATH"
fi
grep -q 'echo "  ods restore \$backup_id"' "$ROOT/ods-backup.sh" \
    || fail "ods-backup.sh restore hint does not suggest 'ods restore <id>'"
grep -q 'echo "  \$SCRIPT_DIR/ods-restore.sh \$backup_id"' "$ROOT/ods-backup.sh" \
    || fail "ods-backup.sh restore hint lacks absolute-path fallback when ods is not installed"
grep -Eq '^ *restore\)' "$ROOT/ods-cli" || fail "ods-cli has no restore subcommand"

if [[ "$FAILED" -ne 0 ]]; then exit 1; fi
echo "PASS: backup ID guidance contract"
