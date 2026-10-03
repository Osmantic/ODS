#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bridge="$root/extensions/services/pixel-agent/host/pixel-wsl-runtime-bridge.sh"
unit="$root/extensions/services/pixel-agent/host/pixel-wsl-runtime-bridge.service"
bash -n "$bridge" "$root/installers/phases/06-directories.sh" \
    "$root/installers/lib/pixel-host-install.sh" "$root/lib/pixel-uninstall.sh"
python3 - "$root" <<'PY'
import importlib.util
import os
from pathlib import Path
import sys

root = Path(sys.argv[1])
host = root / 'extensions/services/pixel-agent/host'
bridge = (host / 'pixel-wsl-runtime-bridge.sh').read_text()
unit = (host / 'pixel-wsl-runtime-bridge.service').read_text()
ingress = (host / 'pixel-ingress.service').read_text()
preview = (host / 'pixel-workspace-preview.service').read_text()
preview_code = (host / 'workspace_preview.py').read_text()
gateway_socket_dropin = (host / 'pixel-gateway-wsl-socket.conf').read_text()
phase = (root / 'installers/phases/06-directories.sh').read_text()
installer = (root / 'installers/lib/pixel-host-install.sh').read_text()
uninstall = (root / 'lib/pixel-uninstall.sh').read_text()
health = (root / 'installers/phases/12-health.sh').read_text()
wsl_health = (root / 'installers/verify-wsl-portal.sh').read_text()

# Docker's bind source must remain the same WSL tmpfs directory before and
# after host service restarts. The bridge now prepares directories only.
assert 'mount --bind' not in bridge and 'umount ' not in bridge
assert 'stat -Lc' in bridge and 'wsl_device' in bridge
assert 'old bind mount' in bridge and 'too many mount layers' in bridge
assert 'prepare "$base/ingress" 710' in bridge
assert 'prepare "$base/preview" 750' in bridge
assert 'pixel-ingress.sock' in bridge and 'http.sock' in bridge
assert 'openclaw.json' not in bridge

# At boot the directory setup is required before either socket writer starts.
assert 'Before=pixel-ingress.service pixel-workspace-preview.service' in unit
assert 'RequiredBy=pixel-ingress.service pixel-workspace-preview.service' in unit
assert 'EnvironmentFile=/etc/ods/pixel-agent.env' in unit
assert 'BindsTo=' not in unit
assert 'ExecStart=/usr/local/libexec/ods-pixel-wsl-runtime-bridge ensure' in unit
assert 'RuntimeDirectoryPreserve=yes' in ingress and 'RuntimeDirectoryPreserve=yes' in preview
assert '-/mnt/wsl/ods-portal-sockets/ingress' in ingress
assert '-/mnt/wsl/ods-portal-sockets/preview' in preview
assert 'PIXEL_PREVIEW_HTTP_SOCKET' in preview_code
assert 'Environment=PIXEL_PREVIEW_HTTP_SOCKET=__PIXEL_PREVIEW_HTTP_SOCKET__' in preview
assert 'EnvironmentFile=/etc/ods/pixel-agent.env' not in preview
assert gateway_socket_dropin == ('[Service]\n'
    'Environment=PIXEL_INGRESS_SOCKET=/mnt/wsl/ods-portal-sockets/ingress/pixel-ingress.sock\n')

# No WSL mount propagation is needed to see a socket replaced within a
# stable directory. The gateway token and preview control remain under /run.
assert 'PIXEL_RUNTIME_BIND_PROPAGATION_VALUE=rprivate' in phase
assert 'PIXEL_INGRESS_RUNTIME_DIR_VALUE=/mnt/wsl/ods-portal-sockets/ingress' in phase
assert 'PIXEL_GATEWAY_TOKEN_FILE=$runtime_token_file' in installer
assert 'PIXEL_STATUS_FILE=/run/ods-pixel/ods-status.json' in installer
assert '.replace("__PIXEL_PREVIEW_HTTP_SOCKET__", http_socket)' in installer
assert 'PIXEL_INGRESS_SOCKET=$ingress_socket' in installer
assert 'PIXEL_SERVICE_USER=$owner' in installer
assert '85-ods-ingress-socket.conf' in installer
assert 'systemctl restart openclaw-gateway.service' in installer
assert 'systemctl reenable ods-pixel-wsl-runtime-bridge.service' in installer
assert '_ods_pixel_prepare_wsl_runtime_bridge "$owner"' in installer
assert installer.index('_ods_pixel_prepare_wsl_runtime_bridge "$owner"') < installer.index('"${pixel_prerequisites[@]}" >>"$LOG_FILE"')
assert '--unix-socket "$ingress_socket"' in installer
assert '--unix-socket "$_pixel_ingress_socket"' in health
assert '--unix-socket "$ingress_socket"' in wsl_health
assert 'entries.get("PIXEL_INGRESS_SOCKET") not in' in uninstall
assert 'systemctl disable --now ods-pixel-wsl-runtime-bridge.service' in uninstall
assert 'wsl_gateway_socket_dropin' in uninstall

os.environ['PIXEL_PREVIEW_HTTP_SOCKET'] = '/mnt/wsl/ods-portal-sockets/preview/http.sock'
spec = importlib.util.spec_from_file_location('wsl_workspace_preview', host / 'workspace_preview.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert module.SOCKET_PATH == Path('/run/ods-pixel-preview/control.sock')
assert module.HTTP_SOCKET_PATH == Path('/mnt/wsl/ods-portal-sockets/preview/http.sock')
module.configure_portal('test-profile')
assert module.HTTP_SOCKET_PATH == Path('/run/ods-portal/preview/http.sock')
PY
echo "Pixel WSL stable socket directory contracts passed"
