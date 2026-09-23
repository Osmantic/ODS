#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != Linux ]]; then
    echo 'SKIP: Linux source checkout contract; macOS uses the native Python acquisition path'
    exit 0
fi
command -v timeout >/dev/null || { echo 'GNU timeout is required for the Linux checkout contract' >&2; exit 1; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/installers/lib/pixel-integration.sh"
source "$ROOT/installers/lib/pixel-host-install.sh"

scratch="$(mktemp -d /tmp/ods-pixel-bundle-test.XXXXXX)"
trap '[[ "$scratch" == /tmp/ods-pixel-bundle-test.* ]] && rm -rf -- "$scratch"' EXIT

# The real checkout helper runs all Git work as the owner. This fixture is
# already an unprivileged owner process and retains the same umask boundary.
ods_pixel_run_as_owner() {
    shift 2
    "$@"
}
ods_pixel_run_as_owner_with_umask() {
    local requested_umask="$3"
    shift 3
    (umask "$requested_umask"; "$@")
}

INSTALL_DIR="$ROOT"
PIXEL_SOURCE_URL=bundled
PIXEL_SOURCE_REF="$ODS_PIXEL_BUNDLED_REF"
ods_pixel_validate_source
source_root="$scratch/source-$PIXEL_SOURCE_REF"
observed_checkout="$(_ods_pixel_source_checkout "$(id -un)" "$HOME" "$source_root")" || {
    echo 'Bundled Pixel source checkout failed' >&2
    exit 1
}
[[ "$observed_checkout" == "$source_root" ]]
[[ "$(git -C "$source_root" rev-parse HEAD)" == "$PIXEL_SOURCE_REF" ]]
[[ -z "$(git -C "$source_root" status --porcelain --untracked-files=all)" ]]

# A second verification must not rewrite the already-clean source.
observed_checkout="$(_ods_pixel_source_checkout "$(id -un)" "$HOME" "$source_root")" || {
    echo 'Bundled Pixel source recheck failed' >&2
    exit 1
}
[[ "$observed_checkout" == "$source_root" ]]

PIXEL_SOURCE_REF=b33730436baf5d98bf58f7d57c090318fe19f433
if ods_pixel_validate_source 2>/dev/null; then
    echo 'Bundled source accepted the former private-repository ref' >&2
    exit 1
fi

INSTALL_DIR="$scratch/tampered-ods"
mkdir -p "$INSTALL_DIR/vendor"
cp "$ROOT/vendor/pixel.bundle" "$INSTALL_DIR/vendor/pixel.bundle"
printf 'tampered' >> "$INSTALL_DIR/vendor/pixel.bundle"
PIXEL_SOURCE_REF="$ODS_PIXEL_BUNDLED_REF"
if ods_pixel_validate_source 2>/dev/null; then
    echo 'Bundled source accepted a changed artifact' >&2
    exit 1
fi

echo 'Bundled Pixel source acquisition and tamper tests passed'
