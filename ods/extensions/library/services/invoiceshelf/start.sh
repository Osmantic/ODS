#!/bin/sh
set -eu
case "${INVOICESHELF_KEY:-}" in
  ''|*[!0-9a-fA-F]*) echo 'InvoiceShelf requires a stable 64-hex encryption key' >&2; exit 1;;
esac
[ "${#INVOICESHELF_KEY}" -eq 64 ] || { echo 'InvoiceShelf key must contain 64 hexadecimal characters' >&2; exit 1; }
APP_KEY="$(php -r 'echo "base64:" . base64_encode(hex2bin(getenv("INVOICESHELF_KEY")));')"
export APP_KEY
unset INVOICESHELF_KEY
exec docker-php-serversideup-entrypoint "$@"
