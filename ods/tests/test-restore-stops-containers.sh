#!/bin/bash
# Restore must not write into a running stack by default (#4159).
#
# stop_first defaulted to "false", so `ods-restore.sh <id>` rsynced backup data
# over data/* while services held those files open — and the script never
# restarts containers, so its own closing advice ("Start services: docker
# compose up -d") only made sense on the path nobody took by default.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_RESTORE="$SCRIPT_DIR/../ods-restore.sh"

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'
pass() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }
info() { echo -e "${BLUE}ℹ${NC} $1"; }

[[ -x "$ODS_RESTORE" ]] || fail "ods-restore.sh not found or not executable"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAKE_ODS="$TMP/ods"
mkdir -p "$FAKE_ODS/data" "$FAKE_ODS/.backups"
cp -R "$SCRIPT_DIR/../lib" "$FAKE_ODS/lib"

BID="20260101-000000"
B="$FAKE_ODS/.backups/$BID"
mkdir -p "$B/config"
echo "restored" > "$B/config/settings.json"
cat > "$B/manifest.json" <<'JSON'
{
  "manifest_version": "1.0",
  "backup_date": "2026-01-01T00:00:00Z",
  "backup_id": "20260101-000000",
  "backup_type": "config",
  "ods_version": "test",
  "hostname": "test",
  "description": "test",
  "contents": {"user_data": false, "config": true, "cache": false}
}
JSON

# A docker stub that reports a running stack and records whether `down` ran.
mkdir -p "$TMP/bin"
cat > "$TMP/bin/docker" <<STUB
#!/bin/sh
if [ "\$1" = "compose" ] && [ "\$2" = "ls" ]; then
    echo "$(basename "$FAKE_ODS")"
    exit 0
fi
if [ "\$1" = "compose" ] && [ "\$2" = "down" ]; then
    echo down >> "$TMP/down-called"
    exit \${ODS_TEST_DOWN_EXIT:-0}
fi
exit 0
STUB
chmod +x "$TMP/bin/docker"

run_restore() {
    : > "$TMP/down-called"
    set +e
    PATH="$TMP/bin:$PATH" ODS_DIR="$FAKE_ODS" bash "$ODS_RESTORE" -f --config-only "$@" "$BID" >"$TMP/out" 2>&1
    echo $? > "$TMP/rc"
    set -e
}

info "Default run must stop containers before restoring"
run_restore
[[ -s "$TMP/down-called" ]] || fail "containers were NOT stopped by default: $(cat "$TMP/out")"
pass "default stops containers first"
[[ "$(cat "$TMP/rc")" == "0" ]] || fail "default restore failed: $(cat "$TMP/out")"
pass "default restore succeeds"

info "--no-stop-containers must skip the stop but say so"
run_restore --no-stop-containers
[[ -s "$TMP/down-called" ]] && fail "containers were stopped despite --no-stop-containers"
pass "opt-out skips stopping"
grep -q "without stopping containers" "$TMP/out" || fail "no warning on the unsafe path"
pass "opt-out warns about running services"

info "A failed stop must abort rather than restore underneath live services"
: > "$TMP/down-called"
set +e
PATH="$TMP/bin:$PATH" ODS_DIR="$FAKE_ODS" ODS_TEST_DOWN_EXIT=1 \
    bash "$ODS_RESTORE" -f --config-only "$BID" >"$TMP/out2" 2>&1
rc=$?
set -e
[[ $rc -ne 0 ]] || fail "restore continued after a failed stop"
grep -q "Refusing to restore" "$TMP/out2" || fail "no refusal message: $(cat "$TMP/out2")"
pass "failed stop aborts the restore"

echo "All restore stop-first tests passed"
