#!/bin/sh
set -eu
case "${ADMIN_TOKEN:-}" in
  ''|*[!0-9a-fA-F]*) echo 'ADMIN_TOKEN must contain 64 hexadecimal characters' >&2; exit 1 ;;
esac
if [ "${#ADMIN_TOKEN}" -ne 64 ]; then
  echo 'ADMIN_TOKEN must contain 64 hexadecimal characters' >&2
  exit 1
fi
exec "$@"
