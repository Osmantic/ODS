#!/bin/sh
set -eu
for value in "${DB_PASSWORD:-}" "${INITIAL_API_KEY:-}"; do
  case "$value" in
    ''|*[!0-9a-fA-F]*) echo 'Shlink requires 64-hex database and API secrets' >&2; exit 1;;
  esac
  [ "${#value}" -eq 64 ] || { echo 'Shlink secrets must contain 64 hexadecimal characters' >&2; exit 1; }
done
[ "$DB_PASSWORD" != "$INITIAL_API_KEY" ] || { echo 'Use separate database and API secrets' >&2; exit 1; }
unset value
exec /bin/sh ./docker-entrypoint.sh
