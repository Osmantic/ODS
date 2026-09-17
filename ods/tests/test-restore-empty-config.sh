#!/bin/bash
# Regression: a backup whose config/ is present but empty must not wipe the
# live configuration (#2925).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_RESTORE="$SCRIPT_DIR/../ods-restore.sh"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

pass() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }
info() { echo -e "${BLUE}ℹ${NC} $1"; }

[[ -x "$ODS_RESTORE" ]] || fail "ods-restore.sh not found or not executable"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Git Bash and minimal CI images may not ship rsync. This deterministic shim is
# enough to prove whether restore_user_data runs before config validation.
mkdir -p "$TMP/bin"
cat > "$TMP/bin/rsync" <<'SH'
#!/bin/bash
if [[ "${1:-}" == "--help" ]]; then
    exit 0
fi
src="${@: -2:1}"
dest="${@: -1}"
cp -R "$src" "$dest"
SH
chmod +x "$TMP/bin/rsync"

FAKE_ODS="$TMP/ods"
mkdir -p "$FAKE_ODS/data" "$FAKE_ODS/.backups"
# ods-restore.sh sources $ODS_DIR/lib/*.sh at startup.
cp -R "$SCRIPT_DIR/../lib" "$FAKE_ODS/lib"

# Live configuration and user data the user would lose.
mkdir -p "$FAKE_ODS/config" "$FAKE_ODS/data/open-webui"
echo "live-settings" > "$FAKE_ODS/config/settings.json"
echo "live-user-data" > "$FAKE_ODS/data/open-webui/data.txt"

# A truncated backup: config/ exists but holds nothing.
BID="20260101-000000"
B="$FAKE_ODS/.backups/$BID"
mkdir -p "$B/config" "$B/data/open-webui"
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

info "Restore from a backup with an empty config/ must refuse and preserve the live config"
set +e
out=$(PATH="$TMP/bin:$PATH" ODS_DIR="$FAKE_ODS" bash "$ODS_RESTORE" -f "$BID" 2>&1)
rc=$?
set -e

[[ $rc -ne 0 ]] || fail "Expected a non-zero exit on an empty backup config, got 0. Output: $out"
pass "Restore exits non-zero"

echo "$out" | grep -q "config directory is empty" || fail "Expected an error naming the empty config dir. Output: $out"
pass "Error names the empty config directory"

[[ -f "$FAKE_ODS/config/settings.json" ]] || fail "Live config was deleted"
[[ "$(cat "$FAKE_ODS/config/settings.json")" == "live-settings" ]] || fail "Live config was modified"
pass "Live config preserved intact"

[[ -f "$FAKE_ODS/data/open-webui/data.txt" ]] || fail "Live user data was deleted"
[[ "$(cat "$FAKE_ODS/data/open-webui/data.txt")" == "live-user-data" ]] \
    || fail "Live user data was modified before config validation failed"
pass "Live user data preserved intact"

echo "All restore empty-config tests passed"
