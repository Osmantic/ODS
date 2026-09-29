#!/usr/bin/env bash
# Presets exported on macOS must import on every platform.
#
# macOS `tar` (bsdtar) writes AppleDouble `._*` metadata companions for files
# carrying extended attributes, and puts `._<name>` ahead of `<name>/`. bsdtar
# hides those members from its own `tar tzf` listing, so the exporting machine
# sees a clean archive, but GNU tar lists them. `preset import` took the first
# listed member as the preset name, so on Linux the name resolved to
# `._<name>`, validation could not find `<name>/meta.txt`, and the import died
# with "Invalid preset: missing meta.txt" while leaving the real preset
# directory extracted on disk.
#
# Export now sets COPYFILE_DISABLE so new archives carry no such members, and
# import skips them so archives already in circulation still work.
#
# Runs the real CLI against a throwaway install. No Docker is needed.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ODS_CLI="$ROOT_DIR/ods-cli"

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); printf '[PASS] %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf '[FAIL] %s\n' "$1" >&2; }

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

# make_install <dir>: the minimal install the CLI accepts (check_install + sr_load)
make_install() {
    local dir="$1"
    mkdir -p "$dir/lib" "$dir/extensions/services"
    cp "$ODS_CLI" "$dir/ods-cli"
    cp "$ROOT_DIR"/lib/*.sh "$dir/lib/"
    : > "$dir/docker-compose.base.yml"
}

run_cli() {  # run_cli <install> <args...>
    local install="$1"
    shift
    ODS_HOME="$install" bash "$install/ods-cli" "$@"
}

# ── Source contract: export must disable AppleDouble metadata ────────────────
# Has teeth on every platform, including Linux CI where bsdtar's `._*` behavior
# cannot be reproduced: a future edit that drops the guard fails here.
if grep -Eq 'COPYFILE_DISABLE=[^ ]* +tar +czf' "$ODS_CLI"; then
    pass "preset export guards the tar invocation with COPYFILE_DISABLE"
else
    fail "preset export must run 'tar czf' under COPYFILE_DISABLE to omit AppleDouble metadata"
fi

# ── Behavioral: an archive that already carries `._*` members must import ────
# The archive is synthesized rather than produced by bsdtar, so it is identical
# on every platform. The teeth are on Linux, which is where the bug bites and
# where CI runs: GNU tar lists the `._<name>` member, so before the fix the
# name resolved to `._shared-setup` and this import failed. macOS bsdtar hides
# the member from its own listing, so there the check is a sanity check.
make_appledouble_archive() {  # make_appledouble_archive <path> <preset_name>
    python3 - "$1" "$2" <<'PY'
import io, sys, tarfile

archive, name = sys.argv[1], sys.argv[2]

def add(tar, path, payload):
    data = payload.encode()
    info = tarfile.TarInfo(path)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))

with tarfile.open(archive, "w:gz") as tar:
    # bsdtar emits the AppleDouble companion immediately before what it
    # describes, so the top-level `._<name>` lands first — exactly where the
    # CLI looked for the preset name. Only that member is modelled: the
    # per-file companions play no part in resolving the name, and bsdtar
    # would try to apply a synthetic one as real metadata on macOS.
    add(tar, "._%s" % name, "Mac OS X AppleDouble payload")
    directory = tarfile.TarInfo("%s/" % name)
    directory.type = tarfile.DIRTYPE
    directory.mode = 0o755
    tar.addfile(directory)
    add(tar, "%s/meta.txt" % name, "name=%s\n" % name)
    add(tar, "%s/extensions.list" % name, "dashboard\n")
PY
}

TARGET="$TMP_DIR/target"
make_install "$TARGET"
ARCHIVE="$TMP_DIR/shared-setup.tar.gz"
make_appledouble_archive "$ARCHIVE" "shared-setup"

if run_cli "$TARGET" preset import "$ARCHIVE" > "$TMP_DIR/import.out" 2>&1; then
    if [[ -f "$TARGET/presets/shared-setup/meta.txt" ]]; then
        pass "a macOS archive carrying AppleDouble members imports as 'shared-setup'"
    else
        fail "import reported success but presets/shared-setup/meta.txt is missing"
    fi
else
    sed 's/^/    /' "$TMP_DIR/import.out"
    fail "importing an archive with AppleDouble members failed"
fi

# The AppleDouble entry must never be mistaken for the preset itself.
if [[ -d "$TARGET/presets/._shared-setup" ]]; then
    fail "import created a preset directory from the AppleDouble member"
else
    pass "no preset directory is created from the AppleDouble member"
fi

# ── Regression guard: a clean archive still imports unchanged ────────────────
CLEAN_SRC="$TMP_DIR/clean"
mkdir -p "$CLEAN_SRC/plain-setup"
printf 'name=plain-setup\n' > "$CLEAN_SRC/plain-setup/meta.txt"
printf 'dashboard\n' > "$CLEAN_SRC/plain-setup/extensions.list"
CLEAN_ARCHIVE="$TMP_DIR/plain-setup.tar.gz"
COPYFILE_DISABLE=1 tar czf "$CLEAN_ARCHIVE" -C "$CLEAN_SRC" plain-setup

if run_cli "$TARGET" preset import "$CLEAN_ARCHIVE" > "$TMP_DIR/clean.out" 2>&1 \
    && [[ -f "$TARGET/presets/plain-setup/meta.txt" ]]; then
    pass "an archive without AppleDouble members imports unchanged"
else
    sed 's/^/    /' "$TMP_DIR/clean.out"
    fail "importing a clean archive regressed"
fi

# ── Round trip: export then import through the real CLI ──────────────────────
SOURCE="$TMP_DIR/source"
make_install "$SOURCE"
(umask 077 && printf 'GPU_BACKEND=nvidia\n' > "$SOURCE/.env")
run_cli "$SOURCE" preset save round-trip > "$TMP_DIR/save.out" 2>&1 \
    || { sed 's/^/    /' "$TMP_DIR/save.out"; fail "preset save failed"; }

ROUND_TRIP="$TMP_DIR/round-trip.tar.gz"
if run_cli "$SOURCE" preset export round-trip "$ROUND_TRIP" > "$TMP_DIR/export.out" 2>&1; then
    stray="$(python3 -c '
import sys, tarfile
names = tarfile.open(sys.argv[1]).getnames()
print("\n".join(n for n in names if n.split("/")[-1].startswith("._")))
' "$ROUND_TRIP")"
    if [[ -n "$stray" ]]; then
        echo "$stray" | sed 's/^/    stray member: /'
        fail "exported archive contains AppleDouble members"
    else
        pass "exported archive carries no AppleDouble members"
    fi

    if run_cli "$TARGET" preset import "$ROUND_TRIP" > "$TMP_DIR/rt-import.out" 2>&1 \
        && [[ -f "$TARGET/presets/round-trip/meta.txt" ]]; then
        pass "exported preset imports on a second install"
    else
        sed 's/^/    /' "$TMP_DIR/rt-import.out"
        fail "exported preset did not import"
    fi
else
    sed 's/^/    /' "$TMP_DIR/export.out"
    fail "preset export failed"
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
