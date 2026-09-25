#!/bin/sh
set -eu
export VALKEYCLI_AUTH="${VALKEY_PASSWORD:?Missing Valkey password}"
reply="$(valkey-cli --user ods -h 127.0.0.1 -p 6379 --raw PING)"
[ "$reply" = PONG ]
