#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT"' EXIT

# GNU stat reads modes with -c, BSD/macOS stat with -f. Same helper the other
# permission suites use, so this one runs on both platforms.
file_mode() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }

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

[[ "$(file_mode "$install_root/config/extensions-catalog.json")" == 664 ]]
[[ "$(file_mode "$install_root/extensions/services")" == 775 ]]

# Match BSD/macOS chmod's option surface while still applying the permission
# change on Linux. The bootstrap helper must not rely on GNU-only `--`.
# shellcheck disable=SC2317
chmod() {
    local arg
    for arg in "$@"; do
        [[ "$arg" != "--" ]] || return 64
    done
    command chmod "$@"
}
secure_pixel_catalog_sources "$install_root"
unset -f chmod

while IFS= read -r -d '' path; do
    mode="$(file_mode "$path")"
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
