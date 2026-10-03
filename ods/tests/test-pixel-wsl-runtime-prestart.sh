#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$root/installers/lib/pixel-host-install.sh"
scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
INSTALL_DIR="$scratch/install"
mkdir -p "$INSTALL_DIR/extensions/services/pixel-agent/host"
bridge="$INSTALL_DIR/extensions/services/pixel-agent/host/pixel-wsl-runtime-bridge.sh"
cp "$root/extensions/services/pixel-agent/host/pixel-wsl-runtime-bridge.sh" "$bridge"
calls="$scratch/calls"
ods_sudo() {
    printf '%s\n' "$*" >> "$calls"
    [[ "${INSTALL_FAIL:-false}" != true || "$1" != install ]] || return 1
    [[ "${BRIDGE_FAIL:-false}" != true || "$1" != /bin/bash ]]
}
write_desktop_env() {
    printf '%s\n' 'PIXEL_RUNTIME_BIND_PROPAGATION=rshared' \
        'PIXEL_INGRESS_RUNTIME_DIR=/mnt/wsl/ods-portal-runtime/ingress' \
        'PIXEL_PREVIEW_RUNTIME_DIR=/mnt/wsl/ods-portal-runtime/preview' > "$INSTALL_DIR/.env"
}
printf '%s\n' 'PIXEL_RUNTIME_BIND_PROPAGATION=rprivate' > "$INSTALL_DIR/.env"
_ods_pixel_prepare_wsl_runtime_bridge "$(id -un)"
[[ ! -e "$calls" ]]
write_desktop_env
_ods_pixel_prepare_wsl_runtime_bridge "$(id -un)"
[[ "$(wc -l < "$calls")" -eq 3 ]]
grep -Fxq "install -d -o $(id -un) -g ods-pixel -m 0710 /run/ods-pixel" "$calls"
grep -Fxq "install -d -o $(id -un) -g ods-pixel -m 0750 /run/ods-pixel-preview" "$calls"
[[ "$(tail -n 1 "$calls")" == "/bin/bash $bridge ensure" ]]
export BRIDGE_FAIL=true
if _ods_pixel_prepare_wsl_runtime_bridge "$(id -un)"; then
    echo 'Bridge failure was ignored' >&2; exit 1
fi
unset BRIDGE_FAIL
: > "$calls"
export INSTALL_FAIL=true
if _ods_pixel_prepare_wsl_runtime_bridge "$(id -un)"; then
    echo 'Runtime directory creation failure was ignored' >&2; exit 1
fi
! grep -q '/bin/bash' "$calls"
unset INSTALL_FAIL
: > "$calls"
sed -i 's@/mnt/wsl/@/mnt/host/wsl/@g' "$INSTALL_DIR/.env"
if _ods_pixel_prepare_wsl_runtime_bridge "$(id -un)"; then
    echo 'Daemon-only paths accepted before WSL startup' >&2; exit 1
fi
[[ ! -s "$calls" ]]
write_desktop_env
mv "$bridge" "$bridge.original"
ln -s "$bridge.original" "$bridge"
if _ods_pixel_prepare_wsl_runtime_bridge "$(id -un)"; then
    echo 'Symlink bridge accepted for privileged execution' >&2; exit 1
fi
[[ ! -s "$calls" ]]
python3 - "$root/installers/lib/pixel-host-install.sh" \
    "$root/installers/phases/11-services.sh" <<'PY'
from pathlib import Path
import sys
host, phase = (Path(path).read_text() for path in sys.argv[1:])
identity = host.split('ods_pixel_prepare_runtime_identity() {', 1)[1].split('\n}\n', 1)[0]
assert '_ods_pixel_prepare_wsl_runtime_bridge "$owner"' in identity
assert phase.index('ods_pixel_prepare_runtime_identity') < phase.index('ods_pixel_install_default_agent')
PY
echo 'Pixel WSL pre-start bridge checks passed'
