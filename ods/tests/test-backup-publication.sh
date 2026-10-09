#!/usr/bin/env bash
# Exercise the CLI's copy/compression/publication failures with real manifests,
# checksums and tar archives. Only rsync transfers and injected failures are fake.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP="$SCRIPT_DIR/../ods-backup.sh"
RESTORE="$SCRIPT_DIR/../ods-restore.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
REAL_JQ="$(command -v jq)"
REAL_TAR="$(command -v tar)"
if command -v sha256sum >/dev/null 2>&1; then HASH_TOOL=sha256sum; else HASH_TOOL=shasum; fi
REAL_HASH="$(command -v "$HASH_TOOL")"
export REAL_JQ REAL_TAR REAL_HASH
mkdir -p "$TMP/bin" "$TMP/ods/lib" "$TMP/ods/config"
cp "$SCRIPT_DIR/../lib/rsync.sh" "$SCRIPT_DIR/../lib/backup-paths.sh" "$TMP/ods/lib/"
printf 'TEST_SECRET=retained\n' > "$TMP/ods/.env"
printf 'config payload\n' > "$TMP/ods/config/settings.json"
printf 'services: {}\n' > "$TMP/ods/docker-compose.yml"

cat > "$TMP/bin/rsync" <<'SH'
#!/usr/bin/env bash
set -eu
if [[ "${1:-}" == --help ]]; then echo '--info=progress2'; exit 0; fi
src="${@: -2:1}"; dest="${@: -1}"
if [[ "${INJECT_FAILURE:-}" == rsync ]]; then
    printf 'partial transfer\n' > "$dest/partial-file"
    exit 23
fi
if [[ "${INJECT_FAILURE:-}" == term ]]; then
    kill -TERM "$PPID"
    exit 23
fi
/usr/bin/cp -a "$src" "$dest"
SH
cat > "$TMP/bin/cp" <<'SH'
#!/usr/bin/env bash
if [[ "${INJECT_FAILURE:-}" == cp ]]; then exit 1; fi
exec /usr/bin/cp "$@"
SH
cat > "$TMP/bin/tar" <<'SH'
#!/usr/bin/env bash
set -eu
if [[ "${INJECT_FAILURE:-}" == tar && "$1" == czf ]]; then
    printf 'truncated archive\n' > "$2"
    exit 2
fi
exec "$REAL_TAR" "$@"
SH
cat > "$TMP/bin/jq" <<'SH'
#!/usr/bin/env bash
if [[ "${INJECT_FAILURE:-}" == manifest && "$1" == -n ]]; then exit 4; fi
exec "$REAL_JQ" "$@"
SH
cat > "$TMP/bin/$HASH_TOOL" <<'SH'
#!/usr/bin/env bash
if [[ "${INJECT_FAILURE:-}" == checksum ]]; then exit 1; fi
exec "$REAL_HASH" "$@"
SH
cat > "$TMP/bin/mv" <<'SH'
#!/usr/bin/env bash
if [[ "${INJECT_FAILURE:-}" == publish ]]; then exit 1; fi
exec /usr/bin/mv "$@"
SH
chmod +x "$TMP/bin/"*
export PATH="$TMP/bin:$PATH" ODS_DIR="$TMP/ods"

failed=0
assert_empty_publication() {
    local root="$1" label="$2" status="$3"
    if [[ "$status" == 0 ]]; then
        echo "FAIL: $label reported success"; failed=$((failed + 1))
    fi
    local debris
    debris=$(find "$root" -mindepth 1 -maxdepth 1 ! -name '20000101-000000' -print)
    if [[ -n "$debris" ]]; then
        echo "FAIL: $label left a partial or published artifact: $debris"
        failed=$((failed + 1))
    fi
    [[ "$(cat "$root/20000101-000000/sentinel")" == retained ]] || {
        echo "FAIL: $label pruned the previous recovery snapshot"; failed=$((failed + 1));
    }
}

for failure in rsync cp manifest checksum tar publish term; do
    root="$TMP/fail-$failure"
    mkdir -p "$root/20000101-000000"
    printf retained > "$root/20000101-000000/sentinel"
    args=(--output "$root" --type config)
    [[ "$failure" != tar ]] || args+=(--compress)
    rc=0
    INJECT_FAILURE="$failure" RETENTION_COUNT=1 bash "$BACKUP" "${args[@]}" > "$TMP/$failure.log" 2>&1 || rc=$?
    assert_empty_publication "$root" "$failure" "$rc"
done

# Operators can still see an orphan after uncatchable process death. It must
# remain outside the discoverable backup namespace and retention pool.
root="$ODS_DIR/.backups"
mkdir -p "$root/.partial-interrupted/backup-123-20000101-000000"
printf '{}' > "$root/.partial-interrupted/backup-123-20000101-000000/manifest.json"
bash "$BACKUP" --output "$root" --type config > "$TMP/restore-source.log"
list_out=$(bash "$BACKUP" --output "$root" --list)
[[ "$list_out" != *interrupted* ]] || { echo 'FAIL: list shows staging'; failed=$((failed + 1)); }
restore_list=$(bash "$RESTORE" --list)
[[ "$restore_list" != *interrupted* ]] || {
    echo 'FAIL: restore list shows staging'; failed=$((failed + 1));
}
selection_out=$(printf '1\n' | bash "$RESTORE" --dry-run --config-only 2>&1)
[[ "$selection_out" != *interrupted* ]] || {
    echo 'FAIL: restore selection shows staging'; failed=$((failed + 1));
}
selection_rc=0
selection_out=$(printf '2\n' | bash "$RESTORE" --dry-run --config-only 2>&1) || selection_rc=$?
if [[ "$selection_rc" == 0 || "$selection_out" != *'Invalid selection: 2'* ]]; then
    echo 'FAIL: restore chooser still indexes an undisplayed staging directory'
    failed=$((failed + 1))
fi

for compress in false true; do
    root="$TMP/success-$compress"
    args=(--output "$root" --type config)
    [[ "$compress" == false ]] || args+=(--compress)
    bash "$BACKUP" "${args[@]}" > "$TMP/success-$compress.log"
    artifact=$(find "$root" -mindepth 1 -maxdepth 1 -name 'backup-*' -print)
    [[ -n "$artifact" && "$artifact" != *$'\n'* ]] || { echo 'FAIL: expected exactly one completed artifact'; exit 1; }
    id=$(basename "$artifact" .tar.gz)
    if [[ "$compress" == true ]]; then
        mkdir "$TMP/extracted"
        "$REAL_TAR" xzf "$artifact" -C "$TMP/extracted"
        snapshot="$TMP/extracted/$id"
        escapees=$("$REAL_TAR" tzf "$artifact" | awk -v id="$id" -F/ '$1 != id { print }')
        [[ -z "$escapees" ]] || { echo 'FAIL: compressed archive lost the restore root'; exit 1; }
    else
        snapshot="$artifact"
    fi
    [[ "$("$REAL_JQ" -r .backup_id "$snapshot/manifest.json")" == "$id" ]] || {
        echo 'FAIL: manifest records staging instead of the public ID'; exit 1;
    }
    [[ "$(cat "$snapshot/config/settings.json")" == 'config payload' ]] || exit 1
    [[ "$(cat "$snapshot/.env")" == 'TEST_SECRET=retained' ]] || exit 1
    bash "$BACKUP" --output "$root" verify "$(basename "$artifact")" > /dev/null
    [[ -z "$(find "$root" -mindepth 1 -maxdepth 1 -name '.partial-*' -print)" ]] || exit 1
done

[[ "$failed" == 0 ]] || exit 1
echo 'Backup publication: all failure, recovery and archive-root cases passed.'
