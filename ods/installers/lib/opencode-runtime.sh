#!/usr/bin/env bash
# Reviewed stable release assets, not a floating installer or package-manager pin.
# Upstream tag v1.18.32: 545f51d26cc39a907d2867492d498d9607ea5fa4.
_ODS_OPENCODE_RELEASES="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/opencode-release.tsv"

ods_opencode_release() {
    local platform arch
    platform="$(uname -s)"; arch="$(uname -m)"
    if [[ "$platform" == Linux ]] && ldd --version 2>&1 | grep -qi musl; then platform=Linux-musl; fi
    awk -F '\t' -v p="$platform" -v a="$arch" 'NR > 1 && $2 == p && $3 == a { print; found=1 } END { if (!found) exit 1 }' "$_ODS_OPENCODE_RELEASES"
}

ods_opencode_version_matches() {
    local actual
    [[ -f "$1" && -x "$1" ]] || return 1
    actual="$("$1" --version 2>/dev/null)" || return 1
    [[ "$actual" == "$2" ]]
}

# Emits only the selected absolute executable. Existing custom PATH binaries are
# reused at the reviewed version, otherwise install under ~/.opencode/bin; never
# overwrite Homebrew or another user's package-managed executable/configuration.
ods_install_opencode() (
    local candidate="${1:-}" row version platform arch asset expected actual stage binary dest
    row="$(ods_opencode_release)" || { echo 'Unsupported OpenCode platform/architecture' >&2; return 1; }
    IFS=$'\t' read -r version platform arch asset expected <<< "$row"
    if ods_opencode_version_matches "$candidate" "$version"; then printf '%s\n' "$candidate"; return; fi
    dest="$HOME/.opencode/bin"
    mkdir -p "$dest" || return 1
    stage="$(mktemp -d "$dest/.ods-update.XXXXXXXX")" || return 1
    trap 'rm -rf -- "$stage"' EXIT
    curl -fsSL --connect-timeout 15 --max-time 300 --retry 2 \
        "https://github.com/anomalyco/opencode/releases/download/v${version}/${asset}" -o "$stage/archive" || return 1
    if command -v sha256sum >/dev/null 2>&1; then
        actual="$(sha256sum "$stage/archive")"; actual="${actual%% *}"
    elif command -v shasum >/dev/null 2>&1; then
        actual="$(shasum -a 256 "$stage/archive")"; actual="${actual%% *}"
    else echo 'Cannot verify OpenCode download: no SHA256 tool' >&2; return 1; fi
    [[ "$actual" == "$expected" ]] || { echo 'OpenCode archive SHA256 mismatch' >&2; return 1; }
    mkdir "$stage/extracted" || return 1
    case "$asset" in
        *.tar.gz) tar -xzf "$stage/archive" -C "$stage/extracted" || return 1 ;;
        *.zip) unzip -q "$stage/archive" -d "$stage/extracted" || return 1 ;;
        *) return 1 ;;
    esac
    binary="$(find "$stage/extracted" -type f -name opencode)"
    [[ -n "$binary" && "$binary" != *$'\n'* ]] || return 1
    chmod 755 "$binary" || return 1
    ods_opencode_version_matches "$binary" "$version" || { echo 'OpenCode staged version mismatch' >&2; return 1; }
    # Same filesystem rename: a running old Unix process keeps its inode, while
    # the installer service restart below selects the complete new binary.
    mv -f "$binary" "$dest/opencode" || return 1
    printf '%s\n' "$dest/opencode"
)
