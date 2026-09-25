#!/bin/sh
set -eu
for value in "${LISTMONK_db__password:-}" "${LISTMONK_ADMIN_PASSWORD:-}"; do
  case "$value" in
    ''|*[!0-9a-fA-F]*) echo 'Listmonk requires 64-hex database and administrator secrets' >&2; exit 1;;
  esac
  [ "${#value}" -eq 64 ] || { echo 'Listmonk secrets must contain 64 hexadecimal characters' >&2; exit 1; }
done
[ "$LISTMONK_db__password" != "$LISTMONK_ADMIN_PASSWORD" ] || { echo 'Use separate database and administrator secrets' >&2; exit 1; }
unset value
./listmonk --install --idempotent --yes --config ''
./listmonk --upgrade --yes --config ''
exec ./listmonk --config ''
