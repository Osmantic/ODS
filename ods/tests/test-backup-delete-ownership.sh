#!/usr/bin/env bash
# Exercise the public delete command against produced backups and foreign data.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
install="$fixture/install"
backups="$fixture/backups with spaces"
mkdir -p "$install/lib" "$backups" "$fixture/bin"
cp "$root/lib/rsync.sh" "$root/lib/backup-paths.sh" "$install/lib/"
printf 'services: {}\n' > "$install/docker-compose.yml"
printf 'ODS_MODE=local\n' > "$install/.env"
# Reject unexpected host/network activity rather than contacting the QA app.
for tool in docker sudo systemctl curl wget; do
    printf '#!/bin/sh\nexit 97\n' > "$fixture/bin/$tool"
    chmod +x "$fixture/bin/$tool"
done
export PATH="$fixture/bin:$PATH" ODS_DIR="$install" INSTALL_DIR="$install" ODS_HOME="$install"
run_delete() { printf 'y\n' | bash "$root/ods-backup.sh" --output "$backups" --delete "$1"; }
refuse() {
    local id="$1"
    if run_delete "$id" > "$fixture/delete.log" 2>&1; then
        cat "$fixture/delete.log" >&2
        printf '[FAIL] foreign backup artifact was accepted: %s\n' "$id" >&2
        exit 1
    fi
    [[ -e "$backups/$id" || -L "$backups/$id" ]]
    printf '[PASS] refuses %s\n' "$id"
}
mkdir "$backups/operator-notes"
printf 'keep\n' > "$backups/operator-notes/sentinel"
refuse operator-notes
[[ "$(cat "$backups/operator-notes/sentinel")" == keep ]]
# A timestamp alone cannot prove that a directory is an ODS backup.
mkdir "$backups/20260101-010101"
printf 'keep\n' > "$backups/20260101-010101/sentinel"
refuse 20260101-010101
printf 'not an archive\n' > "$backups/backup-foreign-20260101-010101.tar.gz"
refuse backup-foreign-20260101-010101.tar.gz
for id in backup-wrong-20260101-010101 backup-malformed-20260101-010101; do mkdir "$backups/$id"; done
printf '{"manifest_version":"1.0","backup_id":"different","backup_type":"config"}\n' > "$backups/backup-wrong-20260101-010101/manifest.json"
printf 'broken-json\n' > "$backups/backup-malformed-20260101-010101/manifest.json"
refuse backup-wrong-20260101-010101
refuse backup-malformed-20260101-010101
mkdir "$fixture/foreign"
printf 'keep\n' > "$fixture/foreign/sentinel"
ln -s "$fixture/foreign" "$backups/backup-link-20260101-010101"
refuse backup-link-20260101-010101
[[ "$(cat "$fixture/foreign/sentinel")" == keep ]]

# Linked and unreadable/uninspectable metadata cannot prove backup ownership.
mkdir "$backups/backup-linked-manifest-20260101-010101"
ln -s "$backups/backup-wrong-20260101-010101/manifest.json" \
    "$backups/backup-linked-manifest-20260101-010101/manifest.json"
refuse backup-linked-manifest-20260101-010101
id=backup-check-failure-20260101-010101
mkdir "$backups/$id"
printf '{"manifest_version":"1.0","backup_id":"%s","backup_type":"config"}\n' "$id" > "$backups/$id/manifest.json"
printf '#!/bin/sh\nexit 19\n' > "$fixture/bin/jq"
chmod +x "$fixture/bin/jq"
refuse "$id"
rm "$fixture/bin/jq"
run_delete "$id" > "$fixture/delete.log"
[[ ! -e "$backups/$id" ]]
printf '[PASS] failed ownership check preserves backup and permits inspected retry\n'

# Real configuration backups remain deletable, with their current process-ID
# labels and with compressed archives selected by either the bare ID or name.
for compressed in false true true; do
    args=(--output "$backups" --type config)
    [[ "$compressed" == false ]] || args+=(--compress)
    bash "$root/ods-backup.sh" "${args[@]}" > "$fixture/create.log"
    target=$(find "$backups" -maxdepth 1 -name 'backup-[0-9]*-*.tar.gz' -o -name 'backup-[0-9]*-*' | head -1)
    [[ -n "$target" ]]
    id=$(basename "$target")
    if [[ "$compressed" == true && "${full_archive_name:-false}" != true ]]; then
        full_archive_name=true
        id="${id%.tar.gz}"
    fi
    run_delete "$id" > "$fixture/delete.log"
    [[ ! -e "$target" ]]
    printf '[PASS] deletes produced backup: %s\n' "$id"
done

# Legacy unlabelled IDs still use the same manifest contract.
legacy=20260102-020202
mkdir "$backups/$legacy"
printf '{"manifest_version":"1.0","backup_id":"%s","backup_type":"config"}\n' "$legacy" > "$backups/$legacy/manifest.json"
printf 'n\n' | bash "$root/ods-backup.sh" --output "$backups" --delete "$legacy" > "$fixture/delete.log"
[[ -d "$backups/$legacy" ]]
run_delete "$legacy" > "$fixture/delete.log"
[[ ! -e "$backups/$legacy" ]]
printf '[PASS] legacy ID supports cancellation and confirmed deletion\n'
