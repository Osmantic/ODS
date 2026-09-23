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
assert '[[ "$(findmnt -n -o PROPAGATION -T "$target")" == shared ]]' in bridge
assert 'ConditionVirtualization=wsl' in unit
assert 'BindsTo=pixel-ingress.service pixel-workspace-preview.service' in unit
assert 'ExecStart=/usr/local/libexec/ods-pixel-wsl-runtime-bridge ensure' in unit
assert 'ExecStop=/usr/local/libexec/ods-pixel-wsl-runtime-bridge remove' in unit
assert 'PIXEL_RUNTIME_BIND_PROPAGATION_VALUE=rshared' in phase
assert 'PIXEL_INGRESS_RUNTIME_DIR_VALUE=/mnt/host/wsl/ods-portal-runtime/ingress' in phase
assert 'docker info --format' in phase
assert 'systemctl enable ods-pixel-wsl-runtime-bridge.service' in installer
assert 'systemctl start ods-pixel-wsl-runtime-bridge.service' in installer
assert 'systemctl disable --now ods-pixel-wsl-runtime-bridge.service' in uninstall
PY
echo "Pixel WSL shared runtime bridge checks passed"
