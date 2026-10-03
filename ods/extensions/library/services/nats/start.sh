#!/bin/sh
set -eu
case "${NATS_PASSWORD:-}" in
  ''|*[!0-9a-fA-F]*) echo 'NATS_PASSWORD must contain 64 hexadecimal characters' >&2; exit 1 ;;
esac
[ "${#NATS_PASSWORD}" -eq 64 ] || { echo 'NATS_PASSWORD must contain 64 hexadecimal characters' >&2; exit 1; }
umask 077
mkdir -p /tmp/ods-nats
cat > /tmp/ods-nats/server.conf <<EOF
server_name: ods-nats
listen: "0.0.0.0:4222"
http: "127.0.0.1:8222"
authorization {
  users: [{user: "ods", password: "$NATS_PASSWORD"}]
}
jetstream {
  store_dir: "/data/jetstream"
  max_mem_store: 128MB
  max_file_store: 2GB
}
max_payload: 1MB
max_connections: 256
debug: false
trace: false
EOF
unset NATS_PASSWORD
exec nats-server --config /tmp/ods-nats/server.conf
