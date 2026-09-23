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

# Remote overrides must fail before any owner command or Git operation.
for remote in https://github.com/Osmantic/Pixel.git git@github.com:Osmantic/Pixel.git; do
    (
        PIXEL_SOURCE_URL="$remote"
        ods_pixel_run_as_owner() { touch "$scratch/unexpected-owner-command"; return 1; }
        ods_pixel_run_as_owner_with_umask() { touch "$scratch/unexpected-owner-command"; return 1; }
        ! ods_pixel_validate_source 2>/dev/null
        ! _ods_pixel_source_checkout owner "$HOME" "$scratch/remote-checkout" 2>/dev/null
        [[ ! -e "$scratch/unexpected-owner-command" && ! -e "$scratch/remote-checkout" ]]
    )
done

# Reconciliation of an old release reuses its exact local checkout, never a remote.
legacy_source="$scratch/legacy-source"
git init -q "$legacy_source"
git -C "$legacy_source" -c user.name=Fixture -c user.email=fixture@example.test \
    -c commit.gpgsign=false commit --allow-empty -qm 'legacy fixture'
legacy_ref="$(git -C "$legacy_source" rev-parse HEAD)"
(
    INSTALL_DIR="$scratch/legacy-install"
    PIXEL_SOURCE_URL="$legacy_source"
    PIXEL_SOURCE_REF="$legacy_ref"
    cached="$INSTALL_DIR/data/pixel/source-$legacy_ref"
    _ods_pixel_source_checkout owner "$HOME" "$cached" >/dev/null
    PIXEL_SOURCE_URL=https://github.com/Osmantic/Pixel.git
    [[ "$(_ods_pixel_reconciliation_source_url "$legacy_ref")" == "$cached" ]]
    PIXEL_SOURCE_URL="$(_ods_pixel_reconciliation_source_url "$legacy_ref")"
    [[ "$(_ods_pixel_source_checkout owner "$HOME" "$cached")" == "$cached" ]]
    unset PIXEL_SOURCE_URL
    mv "$cached" "$scratch/preserved-legacy"
    ! _ods_pixel_reconciliation_source_url "$legacy_ref" 2>/dev/null
    [[ "$(_ods_pixel_reconciliation_source_url "$ODS_PIXEL_BUNDLED_REF")" == bundled ]]
)

# Execute the shipped source-selection block with old and custom old refs.
selection="$(sed -n '/^        PIXEL_SOURCE_URL_VALUE=/,/^        PIXEL_GATEWAY_PORT_VALUE=/p' \
    "$ROOT/installers/phases/06-directories.sh" | sed '$d')"
[[ -n "$selection" ]]
_env_get_explicit_first() { printf '%s\n' "${!1:-$2}"; }
for old_ref in b33730436baf5d98bf58f7d57c090318fe19f433 "$legacy_ref"; do
    PIXEL_SOURCE_URL=https://github.com/Osmantic/Pixel.git
    PIXEL_SOURCE_REF="$old_ref"
    eval "$selection"
    [[ "$PIXEL_SOURCE_URL_VALUE" == bundled && "$PIXEL_SOURCE_REF_VALUE" == "$ODS_PIXEL_BUNDLED_REF" ]]
done
PIXEL_SOURCE_URL="$legacy_source"
PIXEL_SOURCE_REF="$legacy_ref"
eval "$selection"
[[ "$PIXEL_SOURCE_URL_VALUE" == "$legacy_source" && "$PIXEL_SOURCE_REF_VALUE" == "$legacy_ref" ]]

PIXEL_SOURCE_URL=bundled
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
