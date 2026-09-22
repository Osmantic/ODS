#!/bin/sh
set -eu
umask 077
mkdir -p /prometheus/config /prometheus/tsdb
config=/prometheus/config/prometheus.yml
if [ ! -e "$config" ]; then
  cp /opt/ods/default-prometheus.yml "$config"
fi
# Preserve owner configuration; refuse invalid targets/rules instead of replacing it.
/bin/promtool check config "$config"
exec /bin/prometheus \
  --config.file="$config" \
  --storage.tsdb.path=/prometheus/tsdb \
  --storage.tsdb.retention.time=15d \
  --storage.tsdb.retention.size=2GB \
  --web.listen-address=0.0.0.0:9090 \
  --query.timeout=30s \
  --query.max-concurrency=4
