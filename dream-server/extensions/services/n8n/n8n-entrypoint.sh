#!/bin/sh
set -eu

. /opt/dream/n8n-cookie-policy.sh

requested_cookie_policy="${N8N_SECURE_COOKIE:-auto}"
public_protocol="$(dream_n8n_public_protocol \
    "${N8N_PROTOCOL:-http}" \
    "${WEBHOOK_URL:-}")"
resolved_cookie_policy="$(dream_n8n_secure_cookie_policy \
    "$requested_cookie_policy" \
    "$public_protocol" \
    "${DREAM_BIND_ADDRESS:-127.0.0.1}")"

if [ "$requested_cookie_policy" = "false" ] \
    && ! dream_n8n_is_loopback_bind "${DREAM_BIND_ADDRESS:-127.0.0.1}"; then
    printf '%s\n' \
        'WARNING: N8N_SECURE_COOKIE=false is explicitly configured on a non-loopback bind.' \
        'Use HTTPS and N8N_SECURE_COOKIE=true before exposing n8n beyond a trusted local machine.' >&2
fi

export N8N_SECURE_COOKIE="$resolved_cookie_policy"
exec /docker-entrypoint.sh "$@"
