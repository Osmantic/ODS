#!/bin/bash
set -eu
for key in KC_DB_PASSWORD KC_BOOTSTRAP_ADMIN_PASSWORD; do
  [[ ${!key:-} =~ ^[0-9a-fA-F]{64}$ ]] || { echo "$key must contain 64 hexadecimal characters" >&2; exit 1; }
done
[[ "$KC_DB_PASSWORD" != "$KC_BOOTSTRAP_ADMIN_PASSWORD" ]] || { echo 'Database and bootstrap passwords must be distinct' >&2; exit 1; }
exec /opt/keycloak/bin/kc.sh "$@"
