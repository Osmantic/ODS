#!/usr/bin/env bash
# The macOS README clones the repository and runs ods/install.sh from it. On a
# case-insensitive volume ~/ods (the default install dir) and a clone at ~/ODS
# are the same directory; install-macos.sh used to compare them as strings,
# copy the product into the clone and let uninstall delete it. The guard must
# refuse any overlap except running the installer from the install directory.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="$ROOT_DIR/installers/macos/install-macos.sh"
guard="$(sed -n '/^_ods_macos_install_dir_overlaps_source() {/,/^}/p' "$INSTALLER")"
[[ -n "$guard" ]] || { echo "[FAIL] overlap guard not found in install-macos.sh"; exit 1; }
grep -q '^if _ods_macos_install_dir_overlaps_source "\$INSTALL_DIR" "\$SOURCE_ROOT"; then$' "$INSTALLER" \
    || { echo "[FAIL] install-macos.sh does not call the overlap guard"; exit 1; }
guard_line=$(grep -n '^if _ods_macos_install_dir_overlaps_source' "$INSTALLER" | cut -d: -f1)
# Function bodies above may mkdir under INSTALL_DIR when called later; the first
# top-level directory creation is the config tree.
first_mkdir=$(grep -n 'mkdir -p "\${INSTALL_DIR}/config/' "$INSTALLER" | head -1 | cut -d: -f1)
[[ "$guard_line" -lt "$first_mkdir" ]] \
    || { echo "[FAIL] overlap guard must run before the installer creates INSTALL_DIR"; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/home/ODS/ods" "$tmp/home/src/ODS/ods"
# A second name for the clone root stands in for the case-insensitive alias.
ln -s "$tmp/home/ODS" "$tmp/home/ods-alias"

failures=0
expect() {
    local want="$1" install="$2" source="$3" got
    if bash -c "$guard"'; _ods_macos_install_dir_overlaps_source "$1" "$2"' _ "$install" "$source"; then
        got=refuse
    else
        got=allow
    fi
    if [[ "$got" == "$want" ]]; then
        echo "[PASS] $want install=${install#"$tmp"/} source=${source#"$tmp"/}"
    else
        echo "[FAIL] expected $want, got $got: install=$install source=$source"
        failures=$((failures + 1))
    fi
}

if [[ -L "$tmp/home/ods-alias" ]]; then
    expect refuse "$tmp/home/ods-alias" "$tmp/home/ODS/ods"    # clone root under another name
else
    echo "[SKIP] symlinks unavailable here; the alias case runs on Linux and macOS"
fi
expect refuse "$tmp/home/ODS" "$tmp/home/ODS/ods"              # clone root itself
expect refuse "$tmp/home" "$tmp/home/ODS/ods"                  # any ancestor
expect refuse "$tmp/home/ODS/ods/nested/install" "$tmp/home/ODS/ods"  # inside the checkout
expect allow  "$tmp/home/ODS/ods" "$tmp/home/ODS/ods"          # running from the install dir
expect allow  "$tmp/home/ods" "$tmp/home/src/ODS/ods"          # separate, not yet created
expect allow  "$tmp/home/ods-alias/../other" "$tmp/home/src/ODS/ods"

[[ "$failures" -eq 0 ]] || { echo "[FAIL] $failures overlap case(s) wrong"; exit 1; }
echo "[PASS] macOS install-directory overlap guard"
