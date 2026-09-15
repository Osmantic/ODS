#!/bin/ash
# BusyBox ash in the pinned Alpine image supports pipefail.
set -euo pipefail
umask 077
cp /etc/ods-mosquitto/acl /tmp/acl
mosquitto_passwd -b -c /tmp/passwords publisher "${MOSQUITTO_PUBLISH_PASSWORD:?}"
mosquitto_passwd -b /tmp/passwords reader "${MOSQUITTO_READ_PASSWORD:?}"
exec mosquitto -c /etc/ods-mosquitto/mosquitto.conf
