#!/bin/bash
set -eu
for key in MUMBLE_CONFIG_SERVER_PASSWORD MUMBLE_SUPERUSER_PASSWORD; do
  [[ ${!key:-} =~ ^[0-9a-fA-F]{64}$ ]] || { echo "$key must contain 64 hexadecimal characters" >&2; exit 1; }
done
[[ "$MUMBLE_CONFIG_SERVER_PASSWORD" != "$MUMBLE_SUPERUSER_PASSWORD" ]] || { echo 'Connection and administrator passwords must be distinct' >&2; exit 1; }
umask 077
exec /entrypoint.sh "$@"
