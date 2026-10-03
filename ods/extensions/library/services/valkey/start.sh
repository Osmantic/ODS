#!/bin/sh
set -eu
umask 077
case "${VALKEY_PASSWORD:-}" in
  ''|*[!a-fA-F0-9]*) echo 'VALKEY_PASSWORD must contain 64 hexadecimal characters' >&2; exit 1 ;;
esac
[ "${#VALKEY_PASSWORD}" -eq 64 ] || { echo 'VALKEY_PASSWORD must contain 64 hexadecimal characters' >&2; exit 1; }
digest="$(printf '%s' "$VALKEY_PASSWORD" | sha256sum)"
digest="${digest%% *}"
printf '%s\n' 'user default off' "user ods on #${digest} ~ods:* &ods:* db=0 +@read +@write +@transaction +@pubsub -@admin -@dangerous -@scripting -keys -scan -randomkey +ping +hello +select +client|setname +client|setinfo" > /tmp/ods-users.acl
unset VALKEY_PASSWORD digest
exec valkey-server /etc/ods-valkey.conf
