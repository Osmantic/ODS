#!/bin/sh
set -eu
for value in "${GF_SECURITY_ADMIN_PASSWORD:-}" "${GF_SECURITY_SECRET_KEY:-}"; do
  case "$value" in
    ''|*[!0-9a-fA-F]*) echo 'Grafana requires 64-hex administrator and encryption secrets' >&2; exit 1;;
  esac
  [ "${#value}" -eq 64 ] || { echo 'Grafana secrets must be 64 hexadecimal characters' >&2; exit 1; }
done
if [ "$GF_SECURITY_ADMIN_PASSWORD" = "$GF_SECURITY_SECRET_KEY" ]; then
  echo 'Use separate administrator and encryption secrets' >&2
  exit 1
fi
unset value
export GF_SECRETS_MANAGER_ENCRYPTION_SECRET_KEY_V1_SECRET_KEY="$GF_SECURITY_SECRET_KEY"
exec /run.sh "$@"
