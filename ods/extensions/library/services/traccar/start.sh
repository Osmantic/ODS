#!/bin/sh
set -eu
case "${DATABASE_PASSWORD:-}" in
  ''|*[!0-9a-fA-F]*) echo 'Traccar requires a 64-hex database password' >&2; exit 1;;
esac
[ "${#DATABASE_PASSWORD}" -eq 64 ] || { echo 'Traccar database password must be 64 hexadecimal characters' >&2; exit 1; }
exec /opt/traccar/jre/bin/java -XX:+ExitOnOutOfMemoryError -Xmx1400m -jar tracker-server.jar conf/traccar.xml
