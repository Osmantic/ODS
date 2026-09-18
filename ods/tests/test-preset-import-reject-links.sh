#!/usr/bin/env bash
# Regression: `ods preset import` must reject an archive that contains a
# symlink, hardlink, or other non-regular entry, rather than relying on the
# tar implementation's own (version-dependent) symlink-traversal guard. A
# crafted preset archive whose first member is a symlink pointing outside the
# presets directory, followed by a file written "through" it, must be refused
# before extraction — and a failed import must not litter the presets dir.
set -euo pipefail

# ods-cli needs Bash 4+; macOS ships 3.2.
if (( BASH_VERSINFO[0] < 4 )); then
    for b in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$b" ]] && exec "$b" "$0" "$@"
    done
    echo "[SKIP] ods-cli requires Bash 4+"; exit 0
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ODS_CLI="$ROOT_DIR/ods-cli"

fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
pass() { printf '[PASS] %s\n' "$*"; }

command -v tar >/dev/null 2>&1 || { echo "[SKIP] tar required"; exit 0; }
command -v python3 >/dev/null 2>&1 || { echo "[SKIP] python3 required"; exit 0; }
[[ -f "$ODS_CLI" ]] || fail "missing $ODS_CLI"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
INSTALL="$TMP/install"
ESCAPE="$TMP/escape-target"
mkdir -p "$INSTALL/presets" "$ESCAPE"
# cmd_preset runs check_install, which only needs the install dir to exist and
# carry a base compose file.
printf 'services: {}\n' > "$INSTALL/docker-compose.base.yml"

# A legitimate preset archive: a directory of regular files only.
mkdir -p "$TMP/legit/goodpreset"
printf 'ODS_MODE=local\n' > "$TMP/legit/goodpreset/env"
printf 'name=goodpreset\n' > "$TMP/legit/goodpreset/meta.txt"
: > "$TMP/legit/goodpreset/extensions.list"
tar czf "$TMP/legit.tar.gz" -C "$TMP/legit" goodpreset

# A malicious archive: symlink escaping the presets dir + a file through it.
python3 - "$TMP/evil.tar.gz" "$ESCAPE" <<'PY'
import sys, io, tarfile
out, target = sys.argv[1], sys.argv[2]
with tarfile.open(out, "w:gz") as tf:
    s = tarfile.TarInfo("escape"); s.type = tarfile.SYMTYPE; s.linkname = target
    tf.addfile(s)
    data = b"PWNED\n"
    f = tarfile.TarInfo("escape/pwned"); f.size = len(data)
    tf.addfile(f, io.BytesIO(data))
PY

# ods-cli has no `main` source guard, but its `version` case just echoes and
# falls through, so sourcing under `set -- version` defines cmd_preset without
# running a command. Override the install/presets dirs after sourcing.
run_import() {
    local archive="$1"
    (
        # ods-cli's top-level run touches variables that are unset in this
        # stripped harness; don't let the suite's errexit/nounset abort sourcing.
        set +eu
        # `set -- version` drives ods-cli's dispatch to a no-op that falls
        # through, defining its functions; capture the archive path first,
        # because this overwrites the positional parameters.
        set -- version
        # shellcheck source=/dev/null
        source "$ODS_CLI" >/dev/null 2>&1
        INSTALL_DIR="$INSTALL"
        PRESETS_DIR="$INSTALL/presets"
        cmd_preset import "$archive"
    )
}

# 1. Legitimate archive imports.
rm -rf "$INSTALL/presets/goodpreset"
run_import "$TMP/legit.tar.gz" >/dev/null 2>&1 \
    || fail "a legitimate preset archive was rejected"
[[ -f "$INSTALL/presets/goodpreset/meta.txt" ]] \
    || fail "legitimate preset did not import"
pass "legitimate preset archive imports"

# Clear it so the litter assertion below reflects only the malicious import.
rm -rf "$INSTALL/presets/goodpreset"

# 2. Malicious archive is refused before extraction.
set +e
out="$(run_import "$TMP/evil.tar.gz" 2>&1)"
rc=$?
set -e
[[ "$rc" -ne 0 ]] || fail "malicious symlink archive was accepted (rc=0)"
printf '%s' "$out" | grep -qiE 'symlink, hardlink, or special file' \
    || fail "expected the link-rejection message, got: $out"
pass "malicious symlink archive is refused with a clear message"

# 3. No escape: nothing written outside the presets dir.
[[ ! -e "$ESCAPE/pwned" ]] \
    || fail "extraction escaped the presets dir into $ESCAPE"
pass "no file was written outside the presets directory"

# 4. No litter: the rejected import left nothing behind (not even a dangling link).
shopt -s nullglob dotglob
leftovers=("$INSTALL/presets"/*)
shopt -u nullglob dotglob
[[ ${#leftovers[@]} -eq 0 ]] \
    || fail "rejected import left entries in the presets dir: ${leftovers[*]}"
pass "rejected import left no litter in the presets directory"

printf 'preset import link-rejection tests passed.\n'
