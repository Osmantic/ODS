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
        for directory in "$INSTALL_DIR/data/remote-provider" "$INSTALL_DIR/data/remote-provider/secrets"; do
            grep -F "chmod 750 $directory" "$tmp/$platform/permissions" >/dev/null
            grep -F "chgrp $(id -g) $directory" "$tmp/$platform/permissions" >/dev/null
            if [[ "$(uname -s)" != MINGW* ]]; then
                [[ "$(stat -c '%a:%g' "$directory" 2>/dev/null || stat -f '%Lp:%g' "$directory")" == "750:$(id -g)" ]]
            fi
        done
        # Cancellation/refusal must leave a foreign resource and its modes alone.
        : > "$tmp/$platform/permissions"
        rmdir "$INSTALL_DIR/data/remote-provider/secrets"
        printf 'foreign file\n' > "$INSTALL_DIR/data/remote-provider/secrets"
        if ods_prepare_remote_provider_directories "$INSTALL_DIR"; then
            echo "FAIL: $platform accepted a non-directory secret path" >&2
            exit 1
        fi
        [[ ! -s "$tmp/$platform/permissions" ]]
        [[ "$(cat "$INSTALL_DIR/data/remote-provider/secrets")" == 'foreign file' ]]
        rm "$INSTALL_DIR/data/remote-provider/secrets"
        mkdir "$INSTALL_DIR/data/remote-provider/secrets"
        mv "$INSTALL_DIR/data" "$INSTALL_DIR/data-preserved"
        ln -s "$tmp/foreign" "$INSTALL_DIR/data"
        if [[ -L "$INSTALL_DIR/data" ]]; then
            if ods_prepare_remote_provider_directories "$INSTALL_DIR"; then
                echo "FAIL: $platform followed a foreign state symlink" >&2
                exit 1
            fi
            [[ ! -s "$tmp/$platform/permissions" ]]
        else
            echo 'SKIP: host cannot represent the native POSIX symlink refusal case'
        fi
        [[ "$(cat "$tmp/foreign/sentinel")" == 'foreign sentinel' ]]
    )
    echo "PASS: $platform strict-umask producer grants only the provider data group traversal"
done
[[ "$(cat "$tmp/foreign/sentinel")" == 'foreign sentinel' ]]
