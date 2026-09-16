#!/usr/bin/env bash
# `ods-update.sh rollback` restores a pre-update snapshot. _restore_snapshot
# removed the live config/<ext>/ directory and only then copied the snapshot
# over it, and callers invoke it as `if ! _restore_snapshot ...` — a condition
# context, which disables `set -e` for the whole function. A failed copy was
# therefore not fatal: the function fell through to `log_info "Restored: ..."`,
# logged "Snapshot restored." and returned 0, while the directory it had
# already deleted stayed deleted. Running the recovery command destroyed the
# configuration and reported success.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${ODS_UPDATE_UNDER_TEST:-$ROOT_DIR/ods-update.sh}"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ods-rollback-XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

[[ -f "$TARGET" ]] || fail "missing $TARGET"
command -v jq >/dev/null 2>&1 || fail "jq is required by ods-update.sh"

# Build an install with a live config and a pre-update snapshot to restore.
# $1 = install root, $2 = "break-cp" to make the config-* copy fail.
build_fixture() {
    local install="$1" mode="${2:-}"
    mkdir -p "$install"/{config/litellm,data/backups,bin}
    cp "$TARGET" "$install/ods-update.sh"
    chmod +x "$install/ods-update.sh"
    [[ -d "$ROOT_DIR/lib" ]] && cp -r "$ROOT_DIR/lib" "$install/"
    printf 'LIVE-CONFIG\n' > "$install/config/litellm/config.yaml"
    printf 'ODS_VERSION=2.0.0\n' > "$install/.env"

    local snap="$install/data/backups/pre-update-20260101-000000"
    mkdir -p "$snap/config-litellm"
    printf 'SNAPSHOT-CONFIG\n' > "$snap/config-litellm/config.yaml"
    printf 'ODS_VERSION=1.9.0\n' > "$snap/.env"
    printf '{"version":"1.9.0","timestamp":"2026-01-01T00:00:00Z"}\n' > "$snap/snapshot.json"

    # Rollback stops the stack first; no daemon here.
    cat > "$install/bin/docker" <<'SH'
#!/usr/bin/env bash
case "$*" in
    *'ps --services'*) echo fixture ;;
    *'ps --format json'*) echo '{"State":"running"}' ;;
esac
exit 0
SH
    chmod +x "$install/bin/docker"
    printf '#!/usr/bin/env bash\nexit 0\n' > "$install/bin/curl"
    chmod +x "$install/bin/curl"

    if [[ "$mode" == "break-cp" ]]; then
        # Fail only the snapshot config copy — a full disk mid-restore.
        cat > "$install/bin/cp" <<'SH'
#!/usr/bin/env bash
for arg in "$@"; do
    case "$arg" in
        *config-litellm*) echo "cp: simulated failure" >&2; exit 1 ;;
    esac
done
exec /bin/cp "$@"
SH
        chmod +x "$install/bin/cp"
    fi
}

run_rollback() {
    local install="$1"
    ( cd "$install" && PATH="$install/bin:$PATH" HOME="$TMP_DIR/home" \
        bash ./ods-update.sh rollback 2>&1 ) || true
}

# ---------------------------------------------------------------------------
# 1. A failed restore must not destroy the configuration it is replacing.
# ---------------------------------------------------------------------------
BROKEN="$TMP_DIR/broken"
build_fixture "$BROKEN" break-cp
out="$(run_rollback "$BROKEN")"

[[ -f "$BROKEN/config/litellm/config.yaml" ]] \
    || fail "a failed restore deleted the live config instead of leaving it in place"
[[ "$(cat "$BROKEN/config/litellm/config.yaml")" == "LIVE-CONFIG" ]] \
    || fail "the live config was modified by a restore that did not complete"
pass "a failed config restore leaves the existing directory untouched"

grep -q "Restored: config/litellm/" <<<"$out" \
    && fail "a failed restore still logged success: $(grep -i restored <<<"$out" | tr '\n' ' ')"
pass "a failed config restore is not reported as restored"

grep -qi "manual recovery required" <<<"$out" \
    || fail "restore failure did not reach the caller; output: $(tail -3 <<<"$out" | tr '\n' ' ')"
pass "restore failure propagates so rollback reports it"

# No staging directory may be left behind.
if [[ -n "$(find "$BROKEN" -type d -name '.ods-restore.*' -print -quit)" ]]; then
    fail "a staging directory was left behind after a failed restore"
fi
pass "a failed restore leaves no staging directory behind"

# ---------------------------------------------------------------------------
# 2. The successful path is unchanged.
# ---------------------------------------------------------------------------
OK_DIR="$TMP_DIR/ok"
build_fixture "$OK_DIR"
out_ok="$(run_rollback "$OK_DIR")"

[[ "$(cat "$OK_DIR/config/litellm/config.yaml")" == "SNAPSHOT-CONFIG" ]] \
    || fail "a healthy rollback did not restore the snapshot config"
grep -q "^ODS_VERSION=1.9.0$" "$OK_DIR/.env" \
    || fail "a healthy rollback did not restore the snapshot .env"
grep -q "Restored: config/litellm/" <<<"$out_ok" \
    || fail "a healthy rollback did not report the restored config"
if [[ -n "$(find "$OK_DIR" -type d -name '.ods-restore.*' -print -quit)" ]]; then
    fail "a successful restore left a staging directory behind"
fi
pass "a healthy rollback still restores the snapshot and cleans up staging"

echo "Rollback restore-failure tests passed."
python3 "$ROOT_DIR/tests/test_update_rollback_atomicity.py"
