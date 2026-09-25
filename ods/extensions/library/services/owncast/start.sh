#!/bin/sh
set -eu
for value in "${OWNCAST_ADMIN_PASSWORD:-}" "${OWNCAST_STREAM_KEY:-}"; do
  case "$value" in
    ''|*[!0-9a-fA-F]*) echo 'Owncast credentials must each contain 64 hexadecimal characters' >&2; exit 1 ;;
  esac
  [ "${#value}" -eq 64 ] || { echo 'Owncast credentials must each contain 64 hexadecimal characters' >&2; exit 1; }
done
[ "$OWNCAST_ADMIN_PASSWORD" != "$OWNCAST_STREAM_KEY" ] || { echo 'Administrator and stream credentials must be distinct' >&2; exit 1; }
umask 077
exec /app/owncast -adminpassword "$OWNCAST_ADMIN_PASSWORD" -streamkey "$OWNCAST_STREAM_KEY" -webserverip 0.0.0.0 -webserverport 8080 -rtmpport 1935
