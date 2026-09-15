#!/bin/bash
set -euo pipefail
umask 077
case "${REDPANDA_PASSWORD:?}" in
    *:*|*$'\n'*|*$'\r'*)
        printf '%s\n' 'REDPANDA_PASSWORD must not contain a colon or newline (native bootstrap credential format)' >&2
        exit 2
        ;;
esac
export RP_BOOTSTRAP_USER="ods:${REDPANDA_PASSWORD}:SCRAM-SHA-256"
mkdir -p /tmp/ods-redpanda
cp /etc/ods-redpanda/redpanda.yaml /tmp/ods-redpanda/redpanda.yaml
cp /etc/ods-redpanda/bootstrap.yaml /tmp/ods-redpanda/.bootstrap.yaml
exec /usr/bin/rpk redpanda start --config /tmp/ods-redpanda/redpanda.yaml \
    --check=false --overprovisioned --smp 1 --memory 2G --reserve-memory 0M \
    --kafka-addr internal://0.0.0.0:9092,external://0.0.0.0:19092 \
    --advertise-kafka-addr "internal://redpanda:9092,external://${REDPANDA_ADVERTISE_HOST:?}:${REDPANDA_PORT:?}"
