#!/bin/sh
set -eu
umask 077
case "${MOSQUITTO_PASSWORD:-}" in
  ''|*[!0-9a-fA-F]*) echo 'MOSQUITTO_PASSWORD must contain 64 hexadecimal characters' >&2; exit 1 ;;
esac
if [ "${#MOSQUITTO_PASSWORD}" -ne 64 ]; then
  echo 'MOSQUITTO_PASSWORD must contain 64 hexadecimal characters' >&2
  exit 1
fi
mkdir -p /tmp/ods-mqtt
printf 'ods:%s\n' "$MOSQUITTO_PASSWORD" > /tmp/ods-mqtt/passwords
mosquitto_passwd -U /tmp/ods-mqtt/passwords
# Client config avoids exposing the plaintext credential in healthcheck argv.
printf '%s\n' '-u ods' "-P $MOSQUITTO_PASSWORD" > /tmp/ods-mqtt/mosquitto_sub
unset MOSQUITTO_PASSWORD
exec /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf
