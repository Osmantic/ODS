#!/bin/sh
set -eu
for value in "${RABBITMQ_DEFAULT_PASS:-}" "${RABBITMQ_ERLANG_COOKIE:-}"; do
  case "$value" in
    ''|*[!0-9a-fA-F]*) echo 'RabbitMQ requires 64-hex administrator and node secrets' >&2; exit 1;;
  esac
  [ "${#value}" -eq 64 ] || { echo 'RabbitMQ secrets must be 64 hexadecimal characters' >&2; exit 1; }
done
if [ "$RABBITMQ_DEFAULT_PASS" = "$RABBITMQ_ERLANG_COOKIE" ]; then
  echo 'Use distinct administrator and Erlang cookie secrets' >&2
  exit 1
fi
unset value
exec /usr/local/bin/docker-entrypoint.sh "$@"
