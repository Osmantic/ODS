#!/usr/bin/env bash
# `ods preset import <archive>` must accept the archive the way a user types
# it: relative to the current directory. The import checks that the file
# exists, then changes into the presets directory to extract, so a relative
# path used to stop resolving and every such import failed with
# "Failed to extract archive". Export already resolves its output path first.

set -euo pipefail

# ods-cli needs Bash 4+; macOS ships 3.2.
if (( BASH_VERSINFO[0] < 4 )); then
    for modern_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$modern_bash" ]] && exec "$modern_bash" "$0" "$@"
    done
    echo "[SKIP] ods-cli requires Bash 4+"
    exit 0
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ODS_CLI="$ROOT_DIR/ods-cli"

fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
pass() { printf '[PASS] %s\n' "$*"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
INSTALL="$TMP/install"
mkdir -p "$INSTALL/presets" "$TMP/home" "$TMP/user/archives"
# The preset commands only need an install dir with a base compose file.
printf 'services: {}\n' > "$INSTALL/docker-compose.base.yml"

mkdir -p "$TMP/build/mypreset"
printf 'name=mypreset\n' > "$TMP/build/mypreset/meta.txt"
: > "$TMP/build/mypreset/extensions.list"
tar czf "$TMP/user/archives/mypreset.tar.gz" -C "$TMP/build" mypreset

# import_from DIR ARCHIVE: run the real CLI from DIR, as a user would.
import_from() {
    local dir="$1" archive="$2" rc
    rm -rf "$INSTALL/presets/mypreset"
    set +e
    (cd "$dir" && HOME="$TMP/home" INSTALL_DIR="$INSTALL" "$BASH" "$ODS_CLI" preset import "$archive") \
        > "$TMP/out.log" 2>&1 < /dev/null
    rc=$?
    set -e
    [[ "$rc" -eq 0 && -f "$INSTALL/presets/mypreset/meta.txt" && -f "$INSTALL/presets/mypreset/extensions.list" ]] \
        || { cat "$TMP/out.log" >&2; fail "import of '$archive' from $dir (exit $rc)"; }
}

import_from "$TMP/user/archives" "mypreset.tar.gz"
pass "archive name relative to the current directory"

import_from "$TMP/user" "archives/mypreset.tar.gz"
pass "archive in a subdirectory of the current directory"

import_from "$TMP/user/archives" "./mypreset.tar.gz"
pass "archive given as ./name"

import_from "$TMP" "$TMP/user/archives/mypreset.tar.gz"
pass "absolute archive path still works"
