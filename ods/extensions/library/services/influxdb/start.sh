#!/bin/bash
set -eu
for key in DOCKER_INFLUXDB_INIT_PASSWORD DOCKER_INFLUXDB_INIT_ADMIN_TOKEN; do
  [[ ${!key:-} =~ ^[0-9a-fA-F]{64}$ ]] || { echo "$key must contain 64 hexadecimal characters" >&2; exit 1; }
done
[[ "$DOCKER_INFLUXDB_INIT_PASSWORD" != "$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN" ]] || { echo 'Administrator password and operator token must be distinct' >&2; exit 1; }
umask 077
exec /entrypoint.sh "$@"
