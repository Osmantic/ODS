#!/usr/bin/env bash
# Read-only final gate for the Windows -> WSL Portal installation.
set -euo pipefail
install_root="${1:?Expected the resolved Linux install directory}"
for unit in openclaw-gateway.service pixel-ingress.service; do
    if ! systemctl is-active --quiet "$unit"; then
        printf 'Pixel service is not active: %s. Inspect: sudo journalctl -u %s -n 80 --no-pager\n' "$unit" "$unit" >&2
        exit 1
    fi
done
if ! curl --fail --silent --show-error --max-time 15 \
    --unix-socket /run/ods-pixel/pixel-ingress.sock http://localhost/health \
    | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("status") == "ok" else 1)'; then
    printf '%s\n' 'Pixel ingress is not healthy or accessible to this Ubuntu user. Inspect pixel-ingress.service and ods-pixel group membership.' >&2
    exit 1
fi
# Never source .env as executable shell code or print its secrets.
dashboard_port="$(python3 - "$install_root/.env" <<'PY'
import pathlib, re, sys
port = '3001'
for line in pathlib.Path(sys.argv[1]).read_text(encoding='utf-8').splitlines():
    if line.startswith('DASHBOARD_PORT='):
        port = line.split('=', 1)[1].strip().strip('\"\x27')
if not re.fullmatch(r'[0-9]{1,5}', port) or not 1 <= int(port) <= 65535:
    raise SystemExit('Invalid DASHBOARD_PORT in installed configuration')
print(port)
PY
)"
if ! curl --fail --silent --show-error --max-time 15 "http://127.0.0.1:$dashboard_port/" >/dev/null; then
    printf 'Portal dashboard is not responding on port %s. Inspect dashboard container logs.\n' "$dashboard_port" >&2
    exit 1
fi
python3 "$(dirname -- "${BASH_SOURCE[0]}")/verify-portal-api.py" "$install_root"
printf 'Pixel ingress, Portal API and dashboard are reachable. Open http://localhost:%s/pixel and send a message to verify your selected model.\n' "$dashboard_port"
printf 'ODS_PORTAL_URL=http://localhost:%s/pixel\n' "$dashboard_port"
