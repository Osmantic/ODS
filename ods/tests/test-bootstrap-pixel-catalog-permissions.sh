#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT"' EXIT

eval "$(sed -n '/^secure_pixel_catalog_sources() {/,/^}/p' "$ROOT/get-ods.sh")"
type secure_pixel_catalog_sources >/dev/null 2>&1

install_root="$TEST_ROOT/ods"
(
    umask 0002
    mkdir -p \
        "$install_root/config" \
        "$install_root/extensions/library/services/library-fixture" \
        "$install_root/extensions/services/builtin-fixture"
    printf '%s\n' '{"extensions":[]}' > "$install_root/config/extensions-catalog.json"
    printf '%s\n' 'services: {}' > "$install_root/extensions/library/services/library-fixture/compose.yaml"
    printf '%s\n' 'services: {}' > "$install_root/extensions/services/builtin-fixture/compose.yaml"
)

[[ "$(stat -c '%a' "$install_root/config/extensions-catalog.json")" == 664 ]]
[[ "$(stat -c '%a' "$install_root/extensions/services")" == 775 ]]

secure_pixel_catalog_sources "$install_root"

while IFS= read -r -d '' path; do
    mode="$(stat -c '%a' "$path")"
    (( (8#$mode & 8#022) == 0 )) || {
        printf 'FAIL: Pixel catalog input remained writable: %s (%s)\n' "$path" "$mode" >&2
        exit 1
    }
done < <(find \
    "$install_root/config/extensions-catalog.json" \
    "$install_root/extensions/library/services" \
    "$install_root/extensions/services" \
    -print0)

printf 'PASS: bootstrap removes group/world write from Pixel catalog inputs under umask 0002\n'
