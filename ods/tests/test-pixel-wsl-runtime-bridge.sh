#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bridge="$root/extensions/services/pixel-agent/host/pixel-wsl-runtime-bridge.sh"
unit="$root/extensions/services/pixel-agent/host/pixel-wsl-runtime-bridge.service"
bash -n "$bridge" "$root/installers/phases/06-directories.sh" \
    "$root/installers/lib/pixel-host-install.sh" "$root/lib/pixel-uninstall.sh"
python3 - "$bridge" "$unit" "$root/installers/phases/06-directories.sh" \
    "$root/installers/lib/pixel-host-install.sh" "$root/lib/pixel-uninstall.sh" <<'PY'
from pathlib import Path
import sys

bridge, unit, phase, installer, uninstall = (Path(value).read_text(encoding='utf-8') for value in sys.argv[1:])
assert 'bridge /run/ods-pixel "$base/ingress"' in bridge
assert 'bridge /run/ods-pixel-preview "$base/preview"' in bridge
assert 'mountpoint -q -- "$target"' in bridge
assert '[[ "$source_inode" == "$target_inode" ]]' in bridge
assert '[[ "$(findmnt -n --direction backward --first-only -o PROPAGATION -T "$target")" == shared ]]' in bridge
assert 'ConditionVirtualization=wsl' in unit
assert 'BindsTo=pixel-ingress.service pixel-workspace-preview.service' in unit
assert 'ExecStart=/usr/local/libexec/ods-pixel-wsl-runtime-bridge ensure' in unit
assert 'ExecStop=/usr/local/libexec/ods-pixel-wsl-runtime-bridge remove' in unit
assert 'PIXEL_RUNTIME_BIND_PROPAGATION_VALUE=rshared' in phase
assert 'PIXEL_INGRESS_RUNTIME_DIR_VALUE=/mnt/wsl/ods-portal-runtime/ingress' in phase
assert '"${docker_command[@]}" info --format' in phase
assert '"${docker_command[@]}" context inspect' in phase
assert 'systemctl enable ods-pixel-wsl-runtime-bridge.service' in installer
assert 'systemctl start ods-pixel-wsl-runtime-bridge.service' in installer
assert 'systemctl disable --now ods-pixel-wsl-runtime-bridge.service' in uninstall

# Exercise mount decisions without root or a WSL kernel. The commands are
# mocked, but the actual bridge function and directory preparation are run.
import subprocess
function = bridge[bridge.index('bridge() {'):bridge.index('\nbase=')]
preparation = bridge[bridge.index('base='):bridge.index('\nbridge /run/')]
mock = r'''
action=ensure
mounted=1
mountpoint() { return 0; }
findmnt() {
    # Multiple mount layers are normal after Docker creates a self-bind.
    # Require selection of the newest (visible) layer, not the covered one.
    [[ "$2 $3 $4" == '--direction backward --first-only' ]] || return 1
    case "$6" in
        FSTYPE) echo "${filesystem:-tmpfs}";;
        MAJ:MIN) if [[ "$8" == /mnt/wsl ]]; then echo 0:32; else echo "${device:-0:32}"; fi;;
        FSROOT) echo "${mountroot:-/ods-portal-runtime/ingress}";;
        PROPAGATION) echo "${propagation:-shared}";;
    esac
}
stat() {
    case "$2" in
        %u:%g:%a) echo "${ownership:-0:0:755}";;
        %d:%i)
            if [[ "$4" == /run/ods-pixel || "$mounted" == 2 ]]; then
                echo 109:42
            else echo 32:17; fi;;
    esac
}
find() { printf '%s' "${contents:-}"; }
mount() { echo BIND; mounted=2; }
umount() { echo UNMOUNT; }
'''
# The real source-directory guard uses the filesystem. Substitute only those
# fixed paths for this unprivileged test, retaining mount and inode logic.
import tempfile
with tempfile.TemporaryDirectory() as directory:
    from pathlib import Path
    source = Path(directory) / 'source'
    target = Path(directory) / 'target'
    source.mkdir(); target.mkdir()
    invocation = f'bridge {str(source)!r} {str(target)!r}'
    function = function.replace('${target#/mnt/wsl}', '/ods-portal-runtime/ingress')
    mock = mock.replace('/run/ods-pixel', str(source))
    cases = [
        ('Docker empty self-bind', '', 0, 'BIND'),
        ('existing bridge', 'mounted=2', 0, ''),
        ('remove bridge', 'mounted=2; action=remove', 0, 'UNMOUNT'),
        ('remove empty Docker mount', 'action=remove', 0, ''),
        ('foreign filesystem', 'filesystem=ext4', 1, ''),
        ('foreign filesystem device', 'device=0:99', 1, ''),
        ('foreign mount root', 'mountroot=/other', 1, ''),
        ('untrusted target owner', 'ownership=1000:1000:755', 1, ''),
        ('nonempty target', 'contents=unexpected.sock', 1, ''),
    ]
    for name, setup, expected, output in cases:
        result = subprocess.run(['bash', '-c', 'set -euo pipefail\n' + mock + function + '\n' + setup + '\n' + invocation], text=True, capture_output=True)
        assert result.returncode == expected, (name, result.returncode, result.stderr)
        assert result.stdout.strip() == output, (name, result.stdout)
    # Repeated ensure must preserve the existing socket directory ownership.
    preparation = preparation.replace('/mnt/wsl/ods-portal-runtime', directory)
    (Path(directory) / 'ingress').mkdir(); (Path(directory) / 'preview').mkdir()
    result = subprocess.run(['bash', '-c', 'set -euo pipefail\naction=ensure\ninstall() { echo MUTATED; return 1; }\n' + preparation], text=True, capture_output=True)
    assert result.returncode == 0 and not result.stdout, result.stderr
PY
echo "Pixel WSL shared runtime bridge checks passed"
