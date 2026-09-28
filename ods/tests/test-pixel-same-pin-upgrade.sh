#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$root/installers/lib/pixel-host-install.sh"
scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
INSTALL_DIR="$scratch/installed"
incoming="$scratch/incoming"
ref="$(printf 'a%.0s' {1..40})"
_ods_pixel_source_transition_state() { printf 'ready|%s\n' "$ref"; }
for tree in "$INSTALL_DIR" "$incoming"; do
    mkdir -p "$tree/installers/lib" "$tree/bin" \
        "$tree/extensions/services/pixel-agent/host" "$tree/extensions/services/pixel-agent/plugin"
    printf '%s\n' original > "$tree/installers/lib/pixel-host-install.sh"
    printf '%s\n' original > "$tree/bin/ods-pixel-approve"
    printf '%s\n' original > "$tree/extensions/services/pixel-agent/host/extension_manager.py"
    printf '%s\n' original > "$tree/extensions/services/pixel-agent/plugin/index.ts"
done
expect_status() {
    local expected="$1" actual=0
    _ods_pixel_source_transition_required owner "$scratch" "$ref" "$incoming" || actual=$?
    [[ "$actual" == "$expected" ]]
}
expect_status 1
mkdir "$INSTALL_DIR/extensions/services/pixel-agent/host/__pycache__"
printf cache > "$INSTALL_DIR/extensions/services/pixel-agent/host/__pycache__/generated.pyc"
expect_status 1
for relative in installers/lib/pixel-host-install.sh bin/ods-pixel-approve \
    extensions/services/pixel-agent/host/extension_manager.py \
    extensions/services/pixel-agent/plugin/index.ts; do
    printf '%s\n' changed > "$incoming/$relative"
    expect_status 0
    [[ "$(cat "$INSTALL_DIR/$relative")" == original ]]
    cp "$INSTALL_DIR/$relative" "$incoming/$relative"
done
printf new > "$incoming/extensions/services/pixel-agent/host/new-helper.py"
expect_status 0
rm "$incoming/extensions/services/pixel-agent/host/new-helper.py"
mv "$incoming/bin" "$incoming/bin-original"
ln -s "$incoming/bin-original" "$incoming/bin"
expect_status 2
python3 - "$root/installers/phases/06-directories.sh" <<'PY'
from pathlib import Path
import sys
phase = Path(sys.argv[1]).read_text()
transition = phase.index('_ods_pixel_source_transition_required')
assert '"$SCRIPT_DIR"' in phase[transition:transition+240]
assert transition < phase.index('rsync ')
PY
echo 'Pixel same-pin upgrade custody checks passed'
