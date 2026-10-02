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

# The documented direct macOS checkout path does not run get-ods.sh. Its
# native Pixel retain check reads SOURCE_ROOT before the installer copies it.
eval "$(sed -n '/^_macos_secure_pixel_catalog_sources() {/,/^}/p' \
    "$ROOT/installers/macos/install-macos.sh")"
type _macos_secure_pixel_catalog_sources >/dev/null 2>&1
# shellcheck disable=SC2016 # Match literal installer source, not shell expansion.
secure_line="$(grep -nF 'if ! _macos_secure_pixel_catalog_sources "$SOURCE_ROOT"; then' \
    "$ROOT/installers/macos/install-macos.sh" | cut -d: -f1)"
# shellcheck disable=SC2016 # Match literal installer source, not shell expansion.
retain_line="$(grep -nF '/usr/bin/python3 "${LIB_DIR}/pixel-native-retain.py" "${_pixel_retain_args[@]}" --allow-update' \
    "$ROOT/installers/macos/install-macos.sh" | cut -d: -f1)"
[[ -n "$secure_line" && -n "$retain_line" && "$secure_line" -lt "$retain_line" ]]
chmod -R g+w \
    "$install_root/config/extensions-catalog.json" \
    "$install_root/extensions/library/services" \
    "$install_root/extensions/services"
_macos_secure_pixel_catalog_sources "$install_root"
while IFS= read -r -d '' path; do
    mode="$(stat -c '%a' "$path")"
    (( (8#$mode & 8#022) == 0 )) || {
        printf 'FAIL: direct Mac source remained writable: %s (%s)\n' "$path" "$mode" >&2
        exit 1
    }
done < <(find \
    "$install_root/config/extensions-catalog.json" \
    "$install_root/extensions/library/services" \
    "$install_root/extensions/services" \
    -print0)

mv "$install_root/config/extensions-catalog.json" "$install_root/config/catalog-real.json"
ln -s catalog-real.json "$install_root/config/extensions-catalog.json"
if _macos_secure_pixel_catalog_sources "$install_root"; then
    printf 'FAIL: direct Mac source accepted a symlinked catalog\n' >&2
    exit 1
fi
printf 'PASS: direct Mac checkout secures catalog inputs and rejects symlinks\n'
