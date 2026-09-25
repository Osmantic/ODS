#!/bin/sh
set -eu
umask 077
if ! printf '%s' "${REST_SERVER_PASSWORD:-}" | grep -Eq '^[a-f0-9]{64}$'; then
    echo 'REST_SERVER_PASSWORD must contain 64 lowercase hexadecimal characters.' >&2
    exit 1
fi
printf '%s\n' "$REST_SERVER_PASSWORD" | htpasswd -niB ods > /tmp/htpasswd
printf 'user = "ods:%s"\n' "$REST_SERVER_PASSWORD" > /tmp/health-curl.conf
unset REST_SERVER_PASSWORD
exec rest-server --path /data --htpasswd-file /tmp/htpasswd --listen :8000 --private-repos --append-only
