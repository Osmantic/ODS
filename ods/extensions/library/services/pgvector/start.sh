#!/bin/sh
set -eu
for value in "${POSTGRES_PASSWORD:-}" "${PGVECTOR_APP_PASSWORD:-}"; do
  case "$value" in
    ''|*[!0-9a-fA-F]*) echo 'Both pgvector passwords must be 64 hexadecimal characters' >&2; exit 1;;
  esac
  if [ "${#value}" -ne 64 ]; then
    echo 'Both pgvector passwords must be 64 hexadecimal characters' >&2
    exit 1
  fi
done
if [ "$POSTGRES_PASSWORD" = "$PGVECTOR_APP_PASSWORD" ]; then
  echo 'Use distinct administrator and application passwords' >&2
  exit 1
fi
unset value
exec /usr/local/bin/docker-entrypoint.sh "$@"
