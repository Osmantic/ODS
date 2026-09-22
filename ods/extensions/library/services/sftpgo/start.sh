#!/bin/sh
set -eu
value="${SFTPGO_HTTPD__SETUP__INSTALLATION_CODE:-}"
case "$value" in
  ''|*[!0-9a-fA-F]*) echo 'SFTPGo requires a 64-hex installation code' >&2; exit 1;;
esac
[ "${#value}" -eq 64 ] || { echo 'SFTPGo installation code must contain 64 hexadecimal characters' >&2; exit 1; }
unset value
exec "$@"
