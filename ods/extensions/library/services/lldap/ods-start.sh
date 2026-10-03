#!/bin/sh
set -eu
if [ "${#LLDAP_JWT_SECRET}" -lt 32 ] || [ "${#LLDAP_KEY_SEED}" -lt 32 ] || [ "${#LLDAP_LDAP_USER_PASS}" -lt 12 ]; then
  echo 'Configure persistent secrets of at least 32 characters and an initial password of at least 12 characters.' >&2
  exit 1
fi
if [ ! -f /data/lldap_config.toml ]; then
  cp /app/ods-default.toml /data/lldap_config.toml
fi
exec /app/lldap run --config-file /data/lldap_config.toml
