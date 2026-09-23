#!/usr/bin/env bash
# `ods update` delegates its pre-update snapshot to `ods-update.sh backup`,
# which historically captured only compose files, .env*, and .version. The
# snapshot_pre_update path used by `ods-update.sh update` also records
# .compose-flags and config/{litellm,n8n,openclaw,searxng}, and its metadata
# (snapshot.json) routes restores through the transactional _restore_snapshot.
#
# A general backup lacked all three, so `ods rollback` of a failed `ods
# update` restored compose/.env but silently left extension configuration and
# the cached stack selection at whatever the failed update had changed them
# to — the opposite of what a pre-update safety net is for.
#
# Strategy: run the real cmd_backup against a fixture install, mutate the
# live config, then run the real cmd_rollback with docker/curl stubbed and
# assert the extension config and .compose-flags came back.
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
if [[ "$args" == *" config --services "* ]]; then
    echo dashboard-api
elif [[ "$args" == *" ps --all --format json "* ]]; then
    echo '{"State":"running","Health":"healthy"}'
fi
exit 0
SH
printf '#!/usr/bin/env bash\nexit 0\n' > "$BIN_DIR/curl"
chmod +x "$BIN_DIR/docker" "$BIN_DIR/curl"

INSTALL="$TMP_DIR/install"
HOME_DIR="$TMP_DIR/home"
mkdir -p "$INSTALL/config/litellm" "$INSTALL/config/n8n" "$INSTALL/data" "$HOME_DIR"
cp "$UPDATE_SCRIPT" "$INSTALL/ods-update.sh"
echo 'services: {dashboard-api: {image: example/dashboard-api:test}}' > "$INSTALL/docker-compose.base.yml"
printf '%s\n' '-f docker-compose.base.yml' > "$INSTALL/.compose-flags"
printf 'GPU_BACKEND=cpu\n' > "$INSTALL/.env"
echo '{"version": "2.6.0"}' > "$INSTALL/.version"
printf 'model_list:\n  - model_name: before-update\n' > "$INSTALL/config/litellm/config.yaml"
printf '{"encryptionKey": "before-update"}\n' > "$INSTALL/config/n8n/config.json"

# ── Take the backup exactly the way cmd_update does ─────────────────────────
(cd "$INSTALL" && HOME="$HOME_DIR" bash ./ods-update.sh backup pre-update) > "$TMP_DIR/backup.out" 2>&1 \
    || { cat "$TMP_DIR/backup.out"; fail "cmd_backup failed"; }
BACKUP="$(find "$HOME_DIR/.ods/backups" -maxdepth 1 -type d -name 'backup-pre-update-*' | head -1)"
[[ -n "$BACKUP" ]] || fail "cmd_backup produced no backup-pre-update-* directory"

[[ -f "$BACKUP/.compose-flags" ]] \
    || fail "general backup dropped .compose-flags — rollback cannot rebuild the active stack"
pass "general backup captures .compose-flags"

[[ -f "$BACKUP/config-litellm/config.yaml" && -f "$BACKUP/config-n8n/config.json" ]] \
    || fail "general backup dropped config/{litellm,n8n} — rollback cannot restore extension configs"
pass "general backup captures per-extension config directories"

if [[ -f "$BACKUP/snapshot.json" ]] && jq empty "$BACKUP/snapshot.json"; then
    pass "general backup writes snapshot.json for the transactional restore path"
else
    fail "general backup lacks a valid snapshot.json — restores would fall back to the flat-copy path"
fi

# ── Mutate the live install the way a failed update leaves it ───────────────
printf 'model_list:\n  - model_name: after-broken-update\n' > "$INSTALL/config/litellm/config.yaml"
rm -f "$INSTALL/config/n8n/config.json"
printf '%s\n' '-f docker-compose.base.yml -f docker-compose.nvidia.yml' > "$INSTALL/.compose-flags"
printf 'GPU_BACKEND=cpu\nMARKER=broken-by-update\n' >> "$INSTALL/.env"

# ── Roll back to the labelled backup ────────────────────────────────────────
(cd "$INSTALL" && HOME="$HOME_DIR" PATH="$BIN_DIR:$PATH" HEALTH_TIMEOUT=5 \
    bash ./ods-update.sh rollback "$(basename "$BACKUP")") > "$TMP_DIR/rollback.out" 2>&1 \
    || { cat "$TMP_DIR/rollback.out"; fail "cmd_rollback failed"; }

grep -q 'model_name: before-update' "$INSTALL/config/litellm/config.yaml" \
    || { cat "$TMP_DIR/rollback.out"; fail "rollback did not restore config/litellm"; }
pass "rollback restores config/litellm content"

[[ -f "$INSTALL/config/n8n/config.json" ]] \
    || fail "rollback did not bring back the deleted config/n8n/config.json"
pass "rollback restores deleted extension config files"

[[ "$(cat "$INSTALL/.compose-flags")" == '-f docker-compose.base.yml' ]] \
    || fail "rollback did not restore .compose-flags"
pass "rollback restores .compose-flags"

grep -q 'MARKER=broken-by-update' "$INSTALL/.env" \
    && fail "rollback left the post-update .env in place"
pass "rollback restores .env"

echo ""
echo "All update-backup config restore tests passed."
