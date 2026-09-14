#!/usr/bin/env bash
# Phase 5C secure pre-update snapshot and exact restore contract.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SNAPSHOT_LIB="$ROOT_DIR/lib/update-snapshots.sh"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

command -v jq >/dev/null 2>&1 || fail "jq is required"
if ! command -v sha256sum >/dev/null 2>&1 && ! command -v shasum >/dev/null 2>&1; then
    fail "sha256sum or shasum is required"
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

INSTALL_DIR="$TMP/ods"
VERSION_FILE="$INSTALL_DIR/.version"
ROLLBACK_DIR="$INSTALL_DIR/data/backups"
mkdir -p \
    "$INSTALL_DIR/config/documents" \
    "$INSTALL_DIR/data/assistant-first/desired-state" \
    "$INSTALL_DIR/data/assistant-first/transaction-store/txn-test" \
    "$INSTALL_DIR/data/assistant-first/secrets" \
    "$INSTALL_DIR/data/user-extensions/documents" \
    "$INSTALL_DIR/data/user-extensions/.backups/documents" \
    "$INSTALL_DIR/data/user-extensions/.tmp/in-flight" \
    "$INSTALL_DIR/lib" \
    "$ROLLBACK_DIR"
cp "$ROOT_DIR/ods-update.sh" "$INSTALL_DIR/ods-update.sh"
cp "$SNAPSHOT_LIB" "$INSTALL_DIR/lib/update-snapshots.sh"
chmod +x "$INSTALL_DIR/ods-update.sh"

log_info() { echo "[INFO] $*" >&2; }
log_ok() { echo "[OK] $*" >&2; }
log_warn() { echo "[WARN] $*" >&2; }
log_error() { echo "[ERROR] $*" >&2; }
get_current_version() { jq -r '.version' "$VERSION_FILE"; }
_prune_rollback_snapshots() { :; }

# shellcheck source=/dev/null
. "$SNAPSHOT_LIB"

cat > "$INSTALL_DIR/.env" <<'EOF'
ODS_INSTALL_PROFILE=assistant-first
SECRET_TOKEN=fixture-secret
EOF
cat > "$VERSION_FILE" <<'EOF'
{"version":"2.6.0-beta"}
EOF
cat > "$INSTALL_DIR/docker-compose.base.yml" <<'EOF'
services: {}
EOF
cat > "$INSTALL_DIR/config/documents/settings.json" <<'EOF'
{"collection":"baseline"}
EOF
cat > "$INSTALL_DIR/data/assistant-first/desired-state/extensions.lock.json" <<'EOF'
{"schema":"ods.extensions.lockfile.v1","stateRevision":"state-baseline"}
EOF
cat > "$INSTALL_DIR/data/assistant-first/transaction-store/txn-test/journal.jsonl" <<'EOF'
{"sequence":1,"state":"committed"}
EOF
cat > "$INSTALL_DIR/data/assistant-first/transaction-store/txn-test/finalization.json" <<'EOF'
{"schema":"ods.extensions.finalization.v1","status":"committed"}
EOF
cat > "$INSTALL_DIR/data/user-extensions/documents/manifest.yaml" <<'EOF'
schema_version: ods.services.v2
service:
  id: documents
persistent_data:
  - path: data/documents
    backup_class: user-data
    owner: documents
    uninstall: preserve
    purge: separate-approval
EOF
cat > "$INSTALL_DIR/data/user-extensions/documents/compose.yaml" <<'EOF'
services: {}
EOF
cat > "$INSTALL_DIR/data/user-extensions/documents/.ods-library-receipt.json" <<'EOF'
{"source_digest":"baseline","installed_digest":"baseline"}
EOF
echo 'previous-definition' > "$INSTALL_DIR/data/user-extensions/.backups/documents/manifest.yaml"
echo 'transient-staging' > "$INSTALL_DIR/data/user-extensions/.tmp/in-flight/manifest.yaml"
cat > "$INSTALL_DIR/data/assistant-first/secrets/txn-test.json" <<'EOF'
{"value":"baseline-secret-material"}
EOF

COMMAND_SNAPSHOT="$(bash "$INSTALL_DIR/ods-update.sh" snapshot 20260912-115959)" \
    || fail "ods-update snapshot command failed"
[[ -d "$COMMAND_SNAPSHOT" ]] || fail "ods-update snapshot command did not return a snapshot path"
[[ "$(jq -r '.schema' "$COMMAND_SNAPSHOT/snapshot.json" | tr -d '\r')" == "ods.pre-update-snapshot.v2" ]] \
    || fail "ods-update snapshot command did not use the v2 contract"
pass "ods-update exposes the shared v2 snapshot contract"

SNAPSHOT="$(snapshot_pre_update 20260912-120000)" || fail "snapshot creation failed"
[[ -d "$SNAPSHOT" ]] || fail "snapshot directory was not created"
[[ "$(jq -r '.schema' "$SNAPSHOT/snapshot.json" | tr -d '\r')" == "ods.pre-update-snapshot.v2" ]] \
    || fail "snapshot schema is not v2"
[[ "$(jq -r '.secret_custody.disposition' "$SNAPSHOT/snapshot.json" | tr -d '\r')" == "preserved-in-place" ]] \
    || fail "snapshot does not record secret custody disposition"
[[ -f "$SNAPSHOT/payload/config/documents/settings.json" ]] \
    || fail "generic extension configuration was not captured"
[[ -f "$SNAPSHOT/payload/data/assistant-first/desired-state/extensions.lock.json" ]] \
    || fail "extension lockfile was not captured"
[[ -f "$SNAPSHOT/payload/data/assistant-first/transaction-store/txn-test/journal.jsonl" ]] \
    || fail "operation journal was not captured"
[[ -f "$SNAPSHOT/payload/data/assistant-first/transaction-store/txn-test/finalization.json" ]] \
    || fail "finalization receipt was not captured"
[[ -f "$SNAPSHOT/payload/data/user-extensions/documents/.ods-library-receipt.json" ]] \
    || fail "user-extension definition receipt was not captured"
[[ -f "$SNAPSHOT/payload/data/user-extensions/.backups/documents/manifest.yaml" ]] \
    || fail "durable user-extension rollback definition was not captured"
[[ ! -e "$SNAPSHOT/payload/data/user-extensions/.tmp" ]] \
    || fail "transient user-extension staging entered the update snapshot"
[[ "$(jq -r '.transient_exclusions[0]' "$SNAPSHOT/snapshot.json" | tr -d '\r')" == \
    "data/user-extensions/.tmp" ]] || fail "snapshot metadata omitted the transient exclusion"
[[ ! -e "$SNAPSHOT/payload/data/assistant-first/secrets" ]] \
    || fail "host-owned secret material entered the update snapshot"

snapshot_mode="$(_snapshot_mode "$SNAPSHOT")"
metadata_mode="$(_snapshot_mode "$SNAPSHOT/snapshot.json")"
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) ;;
    *)
        (( (8#$snapshot_mode & 077) == 0 )) || fail "snapshot directory is not owner-private"
        (( (8#$metadata_mode & 077) == 0 )) || fail "snapshot metadata is not owner-private"
        ;;
esac
_validate_snapshot "$SNAPSHOT" || fail "new snapshot did not pass its own validator"
pass "snapshot captures the complete nonsecret Assistant First control state"

# Mutate every tracked category, introduce files that were absent, and mutate
# the excluded secret store. Exact restore must remove the additions while
# leaving host-owned secrets in their current state.
echo '{"collection":"updated"}' > "$INSTALL_DIR/config/documents/settings.json"
echo '{"new":true}' > "$INSTALL_DIR/config/new-extension.json"
echo '{"stateRevision":"state-updated"}' > "$INSTALL_DIR/data/assistant-first/desired-state/extensions.lock.json"
rm "$INSTALL_DIR/data/assistant-first/transaction-store/txn-test/journal.jsonl"
mkdir -p "$INSTALL_DIR/data/assistant-first/transaction-store/txn-new"
echo '{"sequence":1}' > "$INSTALL_DIR/data/assistant-first/transaction-store/txn-new/journal.jsonl"
mkdir -p "$INSTALL_DIR/data/user-extensions/new-extension"
echo 'schema_version: ods.services.v2' > "$INSTALL_DIR/data/user-extensions/new-extension/manifest.yaml"
mkdir -p "$INSTALL_DIR/data/user-extensions/.tmp/post-snapshot"
echo 'new-staging' > "$INSTALL_DIR/data/user-extensions/.tmp/post-snapshot/manifest.yaml"
echo 'created-after-snapshot' > "$INSTALL_DIR/.compose-flags"
echo 'created-after-snapshot' > "$INSTALL_DIR/.env.after"
echo 'services: {new: {}}' > "$INSTALL_DIR/docker-compose.new.yml"
echo '{"value":"updated-secret-material"}' > "$INSTALL_DIR/data/assistant-first/secrets/txn-test.json"

_restore_snapshot "$SNAPSHOT" || fail "exact snapshot restore failed"
grep -q '"collection":"baseline"' "$INSTALL_DIR/config/documents/settings.json" \
    || fail "generic config did not return to baseline"
grep -q 'state-baseline' "$INSTALL_DIR/data/assistant-first/desired-state/extensions.lock.json" \
    || fail "extension lockfile did not return to baseline"
[[ -f "$INSTALL_DIR/data/assistant-first/transaction-store/txn-test/journal.jsonl" ]] \
    || fail "operation journal was not restored"
[[ ! -e "$INSTALL_DIR/data/assistant-first/transaction-store/txn-new" ]] \
    || fail "post-snapshot transaction survived exact rollback"
[[ ! -e "$INSTALL_DIR/data/user-extensions/new-extension" ]] \
    || fail "post-snapshot user extension survived exact rollback"
[[ ! -e "$INSTALL_DIR/data/user-extensions/.tmp" ]] \
    || fail "transient user-extension staging survived exact rollback"
[[ -f "$INSTALL_DIR/data/user-extensions/.backups/documents/manifest.yaml" ]] \
    || fail "durable user-extension rollback definition was not restored"
[[ ! -e "$INSTALL_DIR/config/new-extension.json" ]] \
    || fail "post-snapshot generic config survived exact rollback"
[[ ! -e "$INSTALL_DIR/.compose-flags" && ! -e "$INSTALL_DIR/.env.after" ]] \
    || fail "paths declared absent were not restored to absence"
[[ ! -e "$INSTALL_DIR/docker-compose.new.yml" ]] \
    || fail "post-snapshot Compose overlay survived exact rollback"
grep -q 'updated-secret-material' "$INSTALL_DIR/data/assistant-first/secrets/txn-test.json" \
    || fail "rollback replaced the excluded host-owned secret store"
pass "restore returns tracked state exactly and preserves secret custody in place"

# Payload tampering must fail before any restore mutation. cmd_rollback performs
# the same validation before its first docker invocation.
TAMPERED="$(snapshot_pre_update 20260912-120001)" || fail "tamper fixture snapshot failed"
echo '{"collection":"tampered"}' > "$TAMPERED/payload/config/documents/settings.json"
echo '{"collection":"live-sentinel"}' > "$INSTALL_DIR/config/documents/settings.json"
if _restore_snapshot "$TAMPERED" >/dev/null 2>&1; then
    fail "tampered payload passed snapshot restore"
fi
grep -q 'live-sentinel' "$INSTALL_DIR/config/documents/settings.json" \
    || fail "tampered restore mutated live state before checksum failure"

BIN_DIR="$TMP/bin"
DOCKER_LOG="$TMP/docker.log"
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/docker" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$DOCKER_LOG"
exit 0
EOF
chmod +x "$BIN_DIR/docker"
if HOME="$TMP/home" PATH="$BIN_DIR:$PATH" \
    bash "$INSTALL_DIR/ods-update.sh" rollback 20260912-120001 >/dev/null 2>&1; then
    fail "manual rollback accepted a tampered v2 snapshot"
fi
[[ ! -s "$DOCKER_LOG" ]] || fail "manual rollback stopped services before snapshot validation"
grep -q 'live-sentinel' "$INSTALL_DIR/config/documents/settings.json" \
    || fail "manual tamper rejection changed live state"
pass "checksum tampering fails before files or services are mutated"

# Snapshot creation itself rejects symlinked source content and cleans up the
# incomplete snapshot directory.
ln -s "$INSTALL_DIR/.env" "$INSTALL_DIR/config/documents/unsafe-link"
if [[ -L "$INSTALL_DIR/config/documents/unsafe-link" ]]; then
    if snapshot_pre_update 20260912-120002 >/dev/null 2>&1; then
        fail "snapshot accepted a symlinked config entry"
    fi
    [[ ! -e "$ROLLBACK_DIR/pre-update-20260912-120002" ]] \
        || fail "failed snapshot left a partial rollback directory"
    pass "symlinked sources fail closed without leaving a partial snapshot"
else
    pass "symlink rejection deferred to Linux filesystem qualification"
fi

echo "[PASS] Phase 5C Assistant First update snapshot contract"
