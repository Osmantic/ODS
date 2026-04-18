#!/usr/bin/env bash
# Worker container entrypoint — writes a config, then runs the real
# cluster_worker_agent.py. Discovery is UDP-based so no --controller flag
# is passed; the agent must find the controller via beacon.
set -euo pipefail

CFG=/tmp/worker-agent.json
cat > "$CFG" <<EOF
{
  "token": "${CLUSTER_TOKEN}",
  "controller_ip": "",
  "setup_port": 50051,
  "rpc_port": ${RPC_PORT:-50052},
  "gpu_backend": "${GPU_BACKEND:-cpu}",
  "status": "idle"
}
EOF

exec python3 /app/scripts/cluster_worker_agent.py \
    --config "$CFG" \
    --status-port "${STATUS_PORT:-50054}"
