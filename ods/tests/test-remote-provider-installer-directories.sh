#!/usr/bin/env bash
# Execute the actual Linux/macOS directory producer blocks in private fixtures.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
export ODS_HOME="$tmp/foreign"
mkdir -p "$ODS_HOME"
printf 'foreign sentinel\n' > "$ODS_HOME/sentinel"

python3 - "$ROOT" "$tmp" <<'PY'
from pathlib import Path
import sys
root, output = map(Path, sys.argv[1:])
for platform, file, start, end in (
    ('linux', 'installers/phases/06-directories.sh', '    # Create directories\n', '    _phase06_repair_host_path()'),
    ('macos', 'installers/macos/install-macos.sh', '    # Create directory structure\n', '    ai_ok "Created directory structure"'),
):
    text = (root / file).read_text(encoding='utf-8')
    begin = text.index(start)
    block = text[begin:text.index(end, begin)]
    if platform == 'linux' and '    # Prepare remote-provider group traversal' in text:
        permission_start = text.index('    # Prepare remote-provider group traversal')
        block += text[permission_start:text.index('    # Ensure we can write to config/data subtrees', permission_start)]
    (output / (platform + '.sh')).write_text(block, encoding='utf-8')
PY

mode_of() {
    stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"
}

for platform in linux macos; do
    (
        export INSTALL_DIR="$tmp/$platform/install" ODS_HOME="$tmp/$platform/install"
        export SCRIPT_DIR="$ROOT" SOURCE_ROOT="$ROOT"
        mkdir -p "$INSTALL_DIR"
        umask 077
        _phase06_step() { :; }
        ods_progress() { :; }
        error() { printf '%s\n' "$*" >&2; return 1; }
        ai_err() { printf '%s\n' "$*" >&2; return 1; }
        warn() { printf '%s\n' "$*" >> "$tmp/$platform/warnings"; }
        ai_warn() { printf '%s\n' "$*" >> "$tmp/$platform/warnings"; }
        # Refuse any accidental escape from the private permission fixture.
        sudo() { return 99; }
        ods_sudo() { return 99; }
        docker() { return 99; }
        systemctl() { return 99; }
        launchctl() { return 99; }
        curl() { return 99; }
        chmod() {
            printf 'chmod %s\n' "$*" >> "$tmp/$platform/permissions"
            command chmod "$@"
        }
        chgrp() {
            printf 'chgrp %s\n' "$*" >> "$tmp/$platform/permissions"
            command chgrp "$@"
        }
        source "$tmp/$platform.sh"
        [[ -f "$tmp/$platform/permissions" ]] || {
            echo "FAIL: $platform producer never granted remote-provider directory traversal"
            exit 1
        }
        [[ ! -s "$tmp/$platform/warnings" ]] || {
            echo "FAIL: $platform producer warned on a clean install" >&2
            exit 1
        }
        # Under umask 077 both folders start at 0700 and only gain g+rx.
        for directory in "$INSTALL_DIR/data/remote-provider" "$INSTALL_DIR/data/remote-provider/secrets"; do
            grep -F "chmod g+rx $directory" "$tmp/$platform/permissions" >/dev/null
            grep -F "chgrp $(id -g) $directory" "$tmp/$platform/permissions" >/dev/null
            if [[ "$(uname -s)" != MINGW* ]]; then
                [[ "$(stat -c '%a:%g' "$directory" 2>/dev/null || stat -f '%Lp:%g' "$directory")" == "750:$(id -g)" ]]
            fi
        done
        # Additive only: rootless Docker and the Dashboard API reach the route
        # through the other bits, so a 0755 folder must keep all of them.
        command chmod 755 "$INSTALL_DIR/data/remote-provider"
        command chmod 700 "$INSTALL_DIR/data/remote-provider/secrets"
        ods_prepare_remote_provider_directories "$INSTALL_DIR"
        if [[ "$(uname -s)" != MINGW* ]]; then
            [[ "$(mode_of "$INSTALL_DIR/data/remote-provider")" == 755 ]] || {
                echo "FAIL: $platform removed permission bits from a 0755 remote-provider folder" >&2
                exit 1
            }
            [[ "$(mode_of "$INSTALL_DIR/data/remote-provider/secrets")" == 750 ]] || {
                echo "FAIL: $platform did not add group traversal to a 0700 secrets folder" >&2
                exit 1
            }
        fi
        # A refusal changes no permissions and leaves a file in the way alone.
        : > "$tmp/$platform/permissions"
        rmdir "$INSTALL_DIR/data/remote-provider/secrets"
        printf 'foreign file\n' > "$INSTALL_DIR/data/remote-provider/secrets"
        if ods_prepare_remote_provider_directories "$INSTALL_DIR"; then
            echo "FAIL: $platform accepted a non-directory secret path" >&2
            exit 1
        fi
        [[ ! -s "$tmp/$platform/permissions" ]]
        [[ "$(cat "$INSTALL_DIR/data/remote-provider/secrets")" == 'foreign file' ]]
        # The remote provider is optional: the producer warns, with a recovery
        # step and the help link, and the install continues.
        source "$tmp/$platform.sh"
        grep -F 'chmod g+rx' "$tmp/$platform/warnings" >/dev/null || {
            echo "FAIL: $platform refusal warning gave no recovery step" >&2
            exit 1
        }
        grep -F 'https://discord.gg/4ntNp9MAwC' "$tmp/$platform/warnings" >/dev/null || {
            echo "FAIL: $platform refusal warning gave no help link" >&2
            exit 1
        }
        [[ ! -s "$tmp/$platform/permissions" ]]
        [[ "$(cat "$INSTALL_DIR/data/remote-provider/secrets")" == 'foreign file' ]]
        rm "$INSTALL_DIR/data/remote-provider/secrets"
        mkdir "$INSTALL_DIR/data/remote-provider/secrets"
        # Folders are created as before, but permissions never change through
        # a link.
        mv "$INSTALL_DIR/data" "$INSTALL_DIR/data-preserved"
        ln -s "$tmp/foreign" "$INSTALL_DIR/data"
        if [[ -L "$INSTALL_DIR/data" ]]; then
            if ods_prepare_remote_provider_directories "$INSTALL_DIR"; then
                echo "FAIL: $platform changed permissions through a state symlink" >&2
                exit 1
            fi
            [[ ! -s "$tmp/$platform/permissions" ]]
        else
            echo 'SKIP: host cannot represent the native POSIX symlink refusal case'
        fi
        [[ "$(cat "$tmp/foreign/sentinel")" == 'foreign sentinel' ]]
    )
    echo "PASS: $platform strict-umask producer only adds the provider data group traversal"
done
[[ "$(cat "$tmp/foreign/sentinel")" == 'foreign sentinel' ]]
