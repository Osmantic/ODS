#!/usr/bin/env bash
# `ods rollback` without a target runs `ods-update.sh rollback`, which picks
# the newest pre-update snapshot in <install>/data/backups and falls back to
# the newest general backup in ~/.ods/backups.
#
# `ods update` only writes a general backup (backup-pre-update-<ts>-<ts>), so
# data/backups usually does not exist. The lookup ran `find` on it under
# `set -euo pipefail`, which ended the script with exit 1 and no message
# before anything was restored. When the directory did exist, general
# backups were ordered by whole name, so an older backup whose label sorts
# after "pre-update" (for example backup-working-config-...) was restored
# instead of the snapshot `ods update` had just taken.
#
# Hermetic: docker and curl are stubbed.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPDATE_SCRIPT="$ROOT_DIR/ods-update.sh"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

command -v jq >/dev/null 2>&1 || fail "jq is required"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

BIN_DIR="$TMP_DIR/bin"
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/docker" <<'SH'
#!/usr/bin/env bash
args=" $* "
if [[ "$args" == *" ps "* && "$args" == *" --services "* ]]; then
    echo dashboard-api
elif [[ "$args" == *" ps "* && "$args" == *" --format "* ]]; then
    echo '{"State":"running"}'
fi
exit 0
SH
printf '#!/usr/bin/env bash\nexit 0\n' > "$BIN_DIR/curl"
chmod +x "$BIN_DIR/docker" "$BIN_DIR/curl"

# make_install <dir>: an install whose current .env is the broken one
make_install() {
    local dir="$1"
    mkdir -p "$dir/data"
    cp "$UPDATE_SCRIPT" "$dir/ods-update.sh"
    echo 'services: {dashboard-api: {image: example/dashboard-api:test}}' > "$dir/docker-compose.base.yml"
    printf '%s\n' '-f docker-compose.base.yml' > "$dir/.compose-flags"
    printf 'GPU_BACKEND=cpu\nMARKER=broken-by-update\n' > "$dir/.env"
}

# general_backup <home> <name> <marker>: the layout `ods-update.sh backup` writes
general_backup() {
    local dir="$1/.ods/backups/$2"
    mkdir -p "$dir"
    printf 'GPU_BACKEND=cpu\nMARKER=%s\n' "$3" > "$dir/.env"
    echo '{"version": "2.6.0"}' > "$dir/.version"
    jq -n --arg bid "$2" '{backup_id: $bid, version: "2.6.0"}' > "$dir/metadata.json"
}

# rollback_snapshot <install> <timestamp> <marker>: the layout snapshot_pre_update writes
rollback_snapshot() {
    local dir="$1/data/backups/pre-update-$2"
    mkdir -p "$dir"
    printf 'GPU_BACKEND=cpu\nMARKER=%s\n' "$3" > "$dir/.env"
    jq -n --arg ts "$2" '{type: "pre-update", timestamp: $ts, version: "2.6.0"}' > "$dir/snapshot.json"
}

# run_rollback <install> <home> <output>: prints the exit status
run_rollback() {
    local rc=0
    (cd "$1" && HOME="$2" PATH="$BIN_DIR:$PATH" HEALTH_TIMEOUT=5 \
        bash ./ods-update.sh rollback) > "$3" 2>&1 || rc=$?
    printf '%s' "$rc"
}

marker() { grep '^MARKER=' "$1/.env"; }

# ── 1. After `ods update`: no data/backups, labelled general backups ────────
INSTALL="$TMP_DIR/one/install"; HOME_DIR="$TMP_DIR/one/home"
make_install "$INSTALL"
general_backup "$HOME_DIR" "backup-working-config-20260801-100000" "august-labelled"
general_backup "$HOME_DIR" "backup-20260910-090000" "september-10"
general_backup "$HOME_DIR" "backup-pre-update-20260915-120000-20260915-120000" "before-update"
[[ ! -e "$INSTALL/data/backups" ]] || fail "fixture should not have data/backups"

rc="$(run_rollback "$INSTALL" "$HOME_DIR" "$TMP_DIR/one.out")"
[[ "$rc" == 0 ]] || { cat "$TMP_DIR/one.out"; fail "rollback exited $rc without data/backups"; }
pass "rollback runs when data/backups does not exist"
grep -qF "Rolling back from: backup-pre-update-20260915-120000-20260915-120000" "$TMP_DIR/one.out" \
    || { cat "$TMP_DIR/one.out"; fail "rollback did not pick the newest general backup"; }
[[ "$(marker "$INSTALL")" == "MARKER=before-update" ]] \
    || fail "restored the wrong .env: $(marker "$INSTALL")"
pass "the newest general backup wins over an older one whose label sorts later"

# ── 1b. Same backups with an empty data/backups directory ───────────────────
INSTALL="$TMP_DIR/one-b/install"; HOME_DIR="$TMP_DIR/one-b/home"
make_install "$INSTALL"
mkdir -p "$INSTALL/data/backups"
general_backup "$HOME_DIR" "backup-working-config-20260801-100000" "august-labelled"
general_backup "$HOME_DIR" "backup-pre-update-20260915-120000-20260915-120000" "before-update"

rc="$(run_rollback "$INSTALL" "$HOME_DIR" "$TMP_DIR/one-b.out")"
[[ "$rc" == 0 && "$(marker "$INSTALL")" == "MARKER=before-update" ]] \
    || { cat "$TMP_DIR/one-b.out"; fail "with an empty data/backups, restored $(marker "$INSTALL") (exit $rc)"; }
pass "general backups are ordered by creation time when data/backups exists"

# ── 2. Rollback snapshots keep precedence and are ordered by time ───────────
INSTALL="$TMP_DIR/two/install"; HOME_DIR="$TMP_DIR/two/home"
make_install "$INSTALL"
rollback_snapshot "$INSTALL" "20260101-000000" "snapshot-january"
rollback_snapshot "$INSTALL" "20260201-000000" "snapshot-february"
general_backup "$HOME_DIR" "backup-pre-update-20260915-120000-20260915-120000" "general-september"

rc="$(run_rollback "$INSTALL" "$HOME_DIR" "$TMP_DIR/two.out")"
[[ "$rc" == 0 ]] || { cat "$TMP_DIR/two.out"; fail "snapshot rollback exited $rc"; }
[[ "$(marker "$INSTALL")" == "MARKER=snapshot-february" ]] \
    || { cat "$TMP_DIR/two.out"; fail "expected the newest rollback snapshot, got $(marker "$INSTALL")"; }
pass "the newest pre-update snapshot is still preferred over general backups"

# ── 3. Symlinked backup directories are not candidates ──────────────────────
INSTALL="$TMP_DIR/three/install"; HOME_DIR="$TMP_DIR/three/home"
make_install "$INSTALL"
mkdir -p "$INSTALL/data/backups"
general_backup "$HOME_DIR" "backup-pre-update-20260915-120000-20260915-120000" "before-update"
mkdir -p "$TMP_DIR/three/elsewhere"
general_backup "$TMP_DIR/three/elsewhere" "backup-zz-20991231-235959" "outside-backup-root"
ln -s "$TMP_DIR/three/elsewhere/.ods/backups/backup-zz-20991231-235959" \
    "$HOME_DIR/.ods/backups/backup-zz-20991231-235959"

rc="$(run_rollback "$INSTALL" "$HOME_DIR" "$TMP_DIR/three.out")"
[[ "$rc" == 0 && "$(marker "$INSTALL")" == "MARKER=before-update" ]] \
    || { cat "$TMP_DIR/three.out"; fail "a symlinked backup directory was selected"; }
pass "symlinked backup directories are skipped"

# ── 4. Nothing to restore: say so instead of exiting silently ───────────────
INSTALL="$TMP_DIR/four/install"; HOME_DIR="$TMP_DIR/four/home"
make_install "$INSTALL"
mkdir -p "$HOME_DIR"

rc="$(run_rollback "$INSTALL" "$HOME_DIR" "$TMP_DIR/four.out")"
[[ "$rc" == 1 ]] || { cat "$TMP_DIR/four.out"; fail "expected exit 1 with no backups, got $rc"; }
grep -qF "No backup or rollback snapshot found to restore from." "$TMP_DIR/four.out" \
    || { cat "$TMP_DIR/four.out"; fail "rollback with no backups exited without explaining why"; }
[[ "$(marker "$INSTALL")" == "MARKER=broken-by-update" ]] || fail "rollback changed .env with nothing to restore"
pass "with no backups, rollback reports that nothing was found"

echo ""
echo "All rollback latest-backup tests passed."
