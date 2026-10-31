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
# Execute the emitted no-ods fallback from an install path containing spaces.
hint_block="$(sed -n '/echo "To restore this backup, run:"/,/^        fi$/p' "$ROOT/ods-backup.sh")"
[[ -n "$hint_block" ]] || fail "ods-backup.sh restore hint block not found"
fixture="$(mktemp -d)"
trap 'rm -rf "$fixture"' EXIT
install_dir="$fixture/ODS installation"
mkdir -p "$install_dir" "$fixture/bin"
cat > "$install_dir/ods-restore.sh" <<'FIXTURE'
#!/bin/sh
printf '%s\n' "$@" > "$(dirname "$0")/../restore-args"
FIXTURE
chmod +x "$install_dir/ods-restore.sh"
for tool in bash dirname tail; do ln -s "$(command -v "$tool")" "$fixture/bin/$tool"; done
hint="$(PATH="$fixture/bin" SCRIPT_DIR="$install_dir" backup_id="backup-42-20260101-120000" \
    bash -c "$hint_block" | tail -n 1)"
PATH="$fixture/bin:/usr/bin:/bin" bash -c "$hint" \
    || fail "no-ods restore hint is not runnable from a spaced path: $hint"
[[ "$(cat "$fixture/restore-args" 2>/dev/null)" == "backup-42-20260101-120000" ]] \
    || fail "no-ods restore hint did not pass the backup ID: $hint"
grep -Eq '^ *restore\)' "$ROOT/ods-cli" || fail "ods-cli has no restore subcommand"

if [[ "$FAILED" -ne 0 ]]; then exit 1; fi
echo "PASS: backup ID guidance contract"
