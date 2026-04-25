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

# --status-bind 0.0.0.0 because the test-runner container probes /health
# from another container on the e2e docker network. Production default is
# 127.0.0.1; only the test fixture opts into cross-container exposure.
exec python3 /app/scripts/cluster_worker_agent.py \
    --config "$CFG" \
    --status-port "${STATUS_PORT:-50054}" \
    --status-bind 0.0.0.0
