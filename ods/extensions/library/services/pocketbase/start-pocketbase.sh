#!/bin/sh
# Alpine uses BusyBox ash for /bin/sh.
# shellcheck shell=ash
set -eu
set -o pipefail
POCKETBASE_PASSWORD=${POCKETBASE_PASSWORD:-}
POCKETBASE_ENCRYPTION_KEY=${POCKETBASE_ENCRYPTION_KEY:-}

case "${POCKETBASE_PASSWORD:-}" in
  *[!A-Za-z0-9_-]*) printf 'POCKETBASE_PASSWORD requires 16-72 URL-safe characters\n' >&2; exit 1 ;;
esac
if [ "${#POCKETBASE_PASSWORD}" -lt 16 ] || [ "${#POCKETBASE_PASSWORD}" -gt 72 ]; then
  printf 'POCKETBASE_PASSWORD requires 16-72 URL-safe characters\n' >&2
  exit 1
fi
case "${POCKETBASE_ENCRYPTION_KEY:-}" in
  *[!A-Za-z0-9_-]*) printf 'POCKETBASE_ENCRYPTION_KEY requires exactly 32 URL-safe characters\n' >&2; exit 1 ;;
esac
if [ "${#POCKETBASE_ENCRYPTION_KEY}" -ne 32 ]; then
  printf 'POCKETBASE_ENCRYPTION_KEY requires exactly 32 URL-safe characters\n' >&2
  exit 1
fi
umask 077
export ODS_POCKETBASE_FRESH=0
if [ ! -e /pb/pb_data/data.db ]; then
  if [ -n "$(ls -A /pb/pb_data)" ]; then
    printf 'PocketBase data.db is missing from nonempty storage; restore the complete backup\n' >&2
    exit 1
  fi
  export ODS_POCKETBASE_FRESH=1
fi
printf '%s' "$POCKETBASE_PASSWORD" > /tmp/pocketbase-bootstrap-password
unset POCKETBASE_PASSWORD
exec /usr/local/bin/pocketbase serve --http=0.0.0.0:8090 \
  --dir=/pb/pb_data --hooksDir=/opt/pocketbase/pb_hooks \
  --migrationsDir=/pb/pb_data/migrations \
  --encryptionEnv=POCKETBASE_ENCRYPTION_KEY \
  --origins="http://localhost:${POCKETBASE_PORT:-8090},http://127.0.0.1:${POCKETBASE_PORT:-8090}"
