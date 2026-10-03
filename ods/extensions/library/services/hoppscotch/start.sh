#!/bin/sh
set -eu
case "${HOPPSCOTCH_DB_PASSWORD:-}" in
  ''|*[!a-fA-F0-9]*) echo 'HOPPSCOTCH_DB_PASSWORD must be 64 hexadecimal characters' >&2; exit 1 ;;
esac
[ "${#HOPPSCOTCH_DB_PASSWORD}" -eq 64 ] || { echo 'HOPPSCOTCH_DB_PASSWORD must be 64 hexadecimal characters' >&2; exit 1; }
case "${DATA_ENCRYPTION_KEY:-}" in
  ''|*[!a-fA-F0-9]*) echo 'HOPPSCOTCH_ENCRYPTION_KEY must be 32 hexadecimal characters' >&2; exit 1 ;;
esac
[ "${#DATA_ENCRYPTION_KEY}" -eq 32 ] || { echo 'HOPPSCOTCH_ENCRYPTION_KEY must be 32 hexadecimal characters' >&2; exit 1; }
export DATABASE_URL="postgresql://hoppscotch:${HOPPSCOTCH_DB_PASSWORD}@hoppscotch-db:5432/hoppscotch"
unset HOPPSCOTCH_DB_PASSWORD
cd /dist/backend
pnpm exec prisma migrate deploy
exec node /usr/src/app/aio_run.mjs
