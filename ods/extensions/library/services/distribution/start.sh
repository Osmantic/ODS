#!/bin/sh
set -eu
validate() {
  case "$2" in ''|*[!0-9a-fA-F]*) echo "$1 must contain 64 hexadecimal characters" >&2; exit 1;; esac
  [ "${#2}" -eq 64 ] || { echo "$1 must contain 64 hexadecimal characters" >&2; exit 1; }
}
validate DISTRIBUTION_PASSWORD "${DISTRIBUTION_PASSWORD:-}"
validate REGISTRY_HTTP_SECRET "${REGISTRY_HTTP_SECRET:-}"
[ "$DISTRIBUTION_PASSWORD" != "$REGISTRY_HTTP_SECRET" ] || { echo 'Password and HTTP secret must be distinct' >&2; exit 1; }
umask 077
mkdir -p /tmp/ods-registry
printf '%s\n' "$DISTRIBUTION_PASSWORD" | htpasswd -niB ods > /tmp/ods-registry/htpasswd
printf 'user = "ods:%s"\n' "$DISTRIBUTION_PASSWORD" > /tmp/ods-registry/health.conf
unset DISTRIBUTION_PASSWORD
exec /entrypoint.sh "$@"
