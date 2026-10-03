#!/bin/bash
# ods-restore.sh, ods-backup.sh and ods-uninstall.sh prompts must take their
# documented default when stdin is not a terminal.
#
# Each prompt is a bare `read -rp ... confirm` under `set -euo pipefail`; on
# EOF read returns 1 and the script died before its own "cancelled" branch:
# `ods-restore.sh <id> </dev/null` printed the overwrite warning and exited 1
# without "Restore cancelled", and the interactive selection prompt died the
# same way. Companion to the ods-cli fix (#3873).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ODS_RESTORE="$ROOT_DIR/ods-restore.sh"
ODS_BACKUP="$ROOT_DIR/ods-backup.sh"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'
PASS=0
FAIL=0
pass() { echo -e "${GREEN}✓${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "${RED}✗${NC} $1"; FAIL=$((FAIL + 1)); }

[[ -f "$ODS_RESTORE" ]] || { echo "ods-restore.sh not found" >&2; exit 1; }
[[ -f "$ODS_BACKUP" ]] || { echo "ods-backup.sh not found" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Fake install: the scripts source $ODS_DIR/lib/*.sh at startup and expect a
# data/ directory; one complete config backup under .backups/.
FAKE_ODS="$TMP/ods"
BID="20260101-000000"
B="$FAKE_ODS/.backups/$BID"
mkdir -p "$FAKE_ODS/data/open-webui" "$FAKE_ODS/config" "$B/config" "$B/data/open-webui"
cp -R "$ROOT_DIR/lib" "$FAKE_ODS/lib"
echo "live-settings" > "$FAKE_ODS/config/settings.json"
echo "backup-settings" > "$B/config/settings.json"
echo "backup-user-data" > "$B/data/open-webui/data.txt"
cat > "$B/manifest.json" <<'JSON'
{
  "manifest_version": "1.0",
  "backup_date": "2026-01-01T00:00:00Z",
  "backup_id": "20260101-000000",
  "backup_type": "config",
  "ods_version": "test",
  "hostname": "test",
  "description": "test",
  "contents": {"user_data": true, "config": true, "cache": false}
}
JSON

run_closed_stdin() {
    # stdin closed: the non-interactive case.
    local rc
    set +e
    out=$(ODS_DIR="$FAKE_ODS" bash "$@" </dev/null 2>&1)
    rc=$?
    set -e
    return "$rc"
}

echo "Test 1: ods-restore.sh <id> with stdin closed cancels with a message"
if run_closed_stdin "$ODS_RESTORE" "$BID"; then
    if [[ "$out" == *"Restore cancelled"* ]] && [[ "$(cat "$FAKE_ODS/config/settings.json")" == "live-settings" ]]; then
        pass "restore: exit 0, 'Restore cancelled', live config untouched"
    else
        fail "restore exited 0 but message/config wrong: $(printf '%s' "$out" | tail -n 2 | tr '\n' '|')"
    fi
else
    fail "restore died with exit $? instead of cancelling: $(printf '%s' "$out" | tail -n 2 | tr '\n' '|')"
fi

echo "Test 2: ods-restore.sh (interactive selection) with stdin closed reports the empty selection"
if run_closed_stdin "$ODS_RESTORE"; then
    fail "selection prompt with no input should not succeed"
else
    if [[ "$out" == *"Invalid selection"* ]]; then
        pass "selection: non-zero exit with 'Invalid selection', not a silent death"
    else
        fail "selection died silently: $(printf '%s' "$out" | tail -n 2 | tr '\n' '|')"
    fi
fi

echo "Test 3: ods-backup.sh --delete <id> with stdin closed cancels and keeps the backup"
if run_closed_stdin "$ODS_BACKUP" --delete "$BID"; then
    if [[ "$out" == *"Deletion cancelled"* && -d "$B" ]]; then
        pass "backup --delete: exit 0, 'Deletion cancelled', backup kept"
    else
        fail "backup --delete exited 0 but message/backup wrong (exists=$([[ -d "$B" ]] && echo yes || echo no)): $(printf '%s' "$out" | tail -n 2 | tr '\n' '|')"
    fi
else
    fail "backup --delete died with exit $? (backup exists=$([[ -d "$B" ]] && echo yes || echo no)): $(printf '%s' "$out" | tail -n 2 | tr '\n' '|')"
fi

echo "Test 4: every prompt in the lifecycle scripts tolerates EOF"
# Prompts are top-level `read -rp ...` / `read -r <var>` statements, not the
# `while IFS= read -r` loops that consume command output.
bare="$(grep -nE '^[[:space:]]*read -r' "$ODS_RESTORE" "$ODS_BACKUP" "$ROOT_DIR/ods-uninstall.sh" | grep -vE '<<<|read -ra? -d|read -ra ' | grep -vE '\|\| [a-z_]+=""' || true)"
if [[ -z "$bare" ]]; then
    pass "all interactive reads fall back to an empty reply on EOF"
else
    fail "reads without an EOF fallback:"$'\n'"$bare"
fi

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
