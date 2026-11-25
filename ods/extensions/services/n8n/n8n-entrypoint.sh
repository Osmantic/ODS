#!/bin/sh
set -eu

# This shell is PID 1 until it execs tini below, and PID 1 ignores signals it
# does not handle, so `docker stop` would otherwise wait out its timeout. A
# signal that arrives while the start-up step runs takes effect once it ends.
trap 'exit 143' TERM
trap 'exit 130' INT

# Back up n8n's database before a new n8n version migrates it, and decide
# whether the owner account comes from .env (see n8n-prepare.mjs).
owner_source="$(node /opt/ods/n8n-prepare.mjs)"
if [ "$owner_source" = env ]; then
    export N8N_INSTANCE_OWNER_MANAGED_BY_ENV=true
    export N8N_INSTANCE_OWNER_EMAIL="$ODS_N8N_OWNER_EMAIL"
    export N8N_INSTANCE_OWNER_PASSWORD_HASH_FILE=/tmp/.n8n/.ods-owner-password.bcrypt
fi
# n8n and its task runners never need the plaintext password.
unset ODS_N8N_OWNER_PASSWORD ODS_N8N_OWNER_EMAIL

. /opt/ods/n8n-cookie-policy.sh

requested_cookie_policy="${N8N_SECURE_COOKIE:-auto}"
public_protocol="$(ods_n8n_public_protocol \
    "${N8N_PROTOCOL:-http}" \
    "${WEBHOOK_URL:-}")"
resolved_cookie_policy="$(ods_n8n_secure_cookie_policy \
    "$requested_cookie_policy" \
    "$public_protocol" \
    "${ODS_BIND_ADDRESS:-127.0.0.1}")"

if [ "$requested_cookie_policy" = "false" ] \
    && ! ods_n8n_is_loopback_bind "${ODS_BIND_ADDRESS:-127.0.0.1}"; then
    printf '%s\n' \
        'WARNING: N8N_SECURE_COOKIE=false is explicitly configured on a non-loopback bind.' \
        'Use HTTPS and N8N_SECURE_COOKIE=true before exposing n8n beyond a trusted local machine.' >&2
fi

export N8N_SECURE_COOKIE="$resolved_cookie_policy"
# This script starts as PID 1 so that tini, which becomes PID 1 here, is
# exec'd without the plaintext password: /proc/1/environ is readable by every
# process in the container that runs as the same user.
exec tini -- /docker-entrypoint.sh "$@"
