#!/usr/bin/env bash
# Regression: a bare ODS proxy hostname must preserve a custom published
# host port when it redirects users to the chat hostname.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CADDYFILE="$PROJECT_DIR/extensions/services/ods-proxy/Caddyfile"
container="ods-proxy-port-redirect-$$"

cleanup() {
    docker rm -f "$container" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run -d --name "$container" \
    -e ODS_DEVICE_NAME=office \
    -e ODS_PROXY_PORT=18080 \
    -v "$CADDYFILE:/etc/caddy/Caddyfile:ro" \
    caddy:2.11.3-alpine >/dev/null

for _ in $(seq 1 30); do
    location="$(docker exec "$container" sh -c \
        "wget -S -O /dev/null --max-redirect=0 --header='Host: office.local:18080' 'http://127.0.0.1/projects?a=1' 2>&1" \
        | tr -d '\r' | sed -n 's/^  Location: //p' | head -n 1 || true)"
    [[ -n "$location" ]] && break
    sleep 1
done

[[ "$location" == 'http://chat.office.local:18080/projects?a=1' ]] || {
    echo "expected custom proxy port in redirect, got: ${location:-<none>}" >&2
    docker logs "$container" >&2
    exit 1
}
