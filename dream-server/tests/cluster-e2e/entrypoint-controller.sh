#!/usr/bin/env bash
set -euo pipefail

CFG=/tmp/cluster.json
echo '{"nodes": []}' > "$CFG"

exec python3 /app/scripts/cluster-setup-listener.py \
    --port 50051 \
    --token "${CLUSTER_TOKEN}" \
    --config "$CFG" </dev/null
