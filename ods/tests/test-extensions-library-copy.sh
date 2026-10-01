#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../installers/lib/extensions-library-copy.sh
source "$root/installers/lib/extensions-library-copy.sh"
tmp="$(mktemp -d "${TMPDIR:-/tmp}/ods-ext-library-copy.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT

source_dir="$tmp/source services"
mkdir -p "$source_dir/sample/nested"
printf 'template\n' >"$source_dir/sample/nested/manifest.yaml"
printf '#!/bin/sh\nexit 0\n' >"$source_dir/sample/start.sh"
chmod 755 "$source_dir/sample/start.sh"

for mask in 022 077; do
    data_dir="$tmp/install $mask/data"
    (umask "$mask"; mkdir -p "$data_dir")
    (
        umask "$mask"
        ods_copy_extensions_library "$source_dir" "$data_dir"
        [[ "$(umask)" == "0$mask" ]]
    )
    [[ "$(stat -c %a "$data_dir")" == "$(if [[ "$mask" == 077 ]]; then printf 711; else printf 755; fi)" ]]
    [[ "$(stat -c %a "$data_dir/extensions-library")" == 755 ]]
    [[ "$(stat -c %a "$data_dir/extensions-library/sample/nested")" == 755 ]]
    [[ "$(stat -c %a "$data_dir/extensions-library/sample/nested/manifest.yaml")" == 644 ]]
    [[ "$(stat -c %a "$data_dir/extensions-library/sample/start.sh")" == 755 ]]

    mkdir -p "$data_dir/extensions-library/custom"
    printf 'private retained data\n' >"$data_dir/extensions-library/custom/private.txt"
    chmod 700 "$data_dir/extensions-library/custom"
    chmod 600 "$data_dir/extensions-library/custom/private.txt"
    chmod 700 "$data_dir/extensions-library/sample/nested"
    chmod 600 "$data_dir/extensions-library/sample/nested/manifest.yaml"
    (umask 077; ods_copy_extensions_library "$source_dir" "$data_dir")
    [[ "$(stat -c %a "$data_dir/extensions-library/sample/nested")" == 755 ]]
    [[ "$(stat -c %a "$data_dir/extensions-library/sample/nested/manifest.yaml")" == 644 ]]
    [[ "$(stat -c %a "$data_dir/extensions-library/custom")" == 700 ]]
    [[ "$(stat -c %a "$data_dir/extensions-library/custom/private.txt")" == 600 ]]
    [[ "$(cat "$data_dir/extensions-library/custom/private.txt")" == 'private retained data' ]]
done

outside="$tmp/outside"
mkdir -p "$outside"
printf 'unchanged\n' >"$outside/sentinel"
symlink_data="$tmp/symlink-install/data"
mkdir -p "$symlink_data"
ln -s "$outside" "$symlink_data/extensions-library"
if ods_copy_extensions_library "$source_dir" "$symlink_data"; then
    printf 'symlink destination unexpectedly accepted\n' >&2
    exit 1
fi
[[ "$(cat "$outside/sentinel")" == unchanged ]]
[[ "$(find "$outside" -mindepth 1 -type f | wc -l)" == 1 ]]

printf 'PASS: Extensions Library templates remain readable under umask 022/077 without widening custom entries\n'
