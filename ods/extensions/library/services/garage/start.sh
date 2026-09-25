#!/bin/sh
set -eu
case "${GARAGE_RPC_SECRET:-}" in
  ''|*[!0-9a-fA-F]*) echo 'Garage requires a 64-hex RPC secret' >&2; exit 1;;
esac
[ "${#GARAGE_RPC_SECRET}" -eq 64 ] || { echo 'Garage RPC secret must contain 64 hexadecimal characters' >&2; exit 1; }
exec garage server --single-node
