#!/usr/bin/env bash
# The macOS installer and CLI must not log into a /tmp file they do not own.
#
# Every macOS command appends to /tmp/ods-install-macos.log under
# `set -euo pipefail`, and /tmp is shared by every account on the Mac. When
# another account, or one earlier `sudo ods ...`, owned that file, the first log
# line failed with "Permission denied" and aborted the command. A symlink
# planted at that path would have redirected the appends. Such a path must be
# replaced by a private log file, and a safe path must be kept as it is.
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
    for candidate in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$candidate" ]] && exec "$candidate" "$0" "$@"
    done
    echo "[FAIL] Bash 4+ is required" >&2
    exit 1
fi
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONSTANTS="$ROOT_DIR/installers/macos/lib/constants.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

export TMPDIR="$TMP_DIR/private"
mkdir -p "$TMPDIR"
# shellcheck source=/dev/null
. "$CONSTANTS"
declare -F macos_private_log_file >/dev/null || fail "constants.sh does not define macos_private_log_file"
[[ "$ODS_LOG_FILE" == /tmp/ods-install-macos.log || "$ODS_LOG_FILE" == "$TMPDIR"/ods-install-macos.* ]] \
    || fail "ODS_LOG_FILE is neither the shared log nor a private one: $ODS_LOG_FILE"

expect_same() {
    local label="$1" path="$2" chosen
    chosen="$(macos_private_log_file "$path")"
    [[ "$chosen" == "$path" ]] || fail "$label: expected $path, got $chosen"
    pass "$label keeps the shared log path"
}

expect_private() {
    local label="$1" path="$2" chosen
    chosen="$(macos_private_log_file "$path")"
    [[ "$chosen" != "$path" && "$chosen" == "$TMPDIR"/ods-install-macos.* ]] \
        || fail "$label: expected a private log under $TMPDIR, got $chosen"
    [[ -f "$chosen" && -O "$chosen" && ! -L "$chosen" ]] \
        || fail "$label: private log is not a regular file owned by this user"
    printf 'log line\n' >> "$chosen" || fail "$label: private log is not writable"
    pass "$label uses a private log"
}

expect_same "A missing log" "$TMP_DIR/missing.log"

printf 'earlier\n' > "$TMP_DIR/own.log"
expect_same "An own writable log" "$TMP_DIR/own.log"

printf 'keep\n' > "$TMP_DIR/target"
ln -s "$TMP_DIR/target" "$TMP_DIR/link.log"
expect_private "A symlinked log" "$TMP_DIR/link.log"
[[ "$(cat "$TMP_DIR/target")" == keep ]] || fail "the symlink target was written"

ln -s "$TMP_DIR/nowhere" "$TMP_DIR/dangling.log"
expect_private "A dangling symlink" "$TMP_DIR/dangling.log"

mkdir "$TMP_DIR/dir.log"
expect_private "A directory at the log path" "$TMP_DIR/dir.log"

if [[ "$(id -u)" == 0 ]]; then
    # Root can write anything, so stand in for another account by ownership.
    printf 'other\n' > "$TMP_DIR/foreign.log"
    chown 65534 "$TMP_DIR/foreign.log"
    expect_private "A log owned by another account" "$TMP_DIR/foreign.log"
else
    printf 'read-only\n' > "$TMP_DIR/readonly.log"
    chmod 0444 "$TMP_DIR/readonly.log"
    expect_private "An unwritable log" "$TMP_DIR/readonly.log"
fi
