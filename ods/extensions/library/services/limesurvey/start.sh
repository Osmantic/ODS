#!/bin/bash
set -eu
for key in DB_PASSWORD ADMIN_PASSWORD; do
  if [[ ! ${!key:-} =~ ^[0-9a-fA-F]{64}$ ]]; then
    echo "$key must contain 64 hexadecimal characters" >&2
    exit 1
  fi
done
if [ "$DB_PASSWORD" = "$ADMIN_PASSWORD" ]; then
  echo 'Database and initial administrator passwords must be distinct' >&2
  exit 1
fi
php -r 'if (!filter_var(getenv("ADMIN_EMAIL"), FILTER_VALIDATE_EMAIL)) {fwrite(STDERR, "Valid owner email required\n"); exit(1);}'
umask 077
exec /usr/local/bin/entrypoint.sh "$@"
