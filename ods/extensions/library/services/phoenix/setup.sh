#!/bin/sh
# Phoenix — ensure authentication secret and data directory exist
# Usage: setup.sh [INSTALL_DIR] [GPU_BACKEND]

set -eu

INSTALL_DIR="${1:-.}"
ENV_FILE="${INSTALL_DIR}/.env"
DATA_DIR="${INSTALL_DIR}/data/phoenix"

mkdir -p "${DATA_DIR}"

append_if_missing() {
  key="$1"
  value="$2"
  if [ -f "$ENV_FILE" ] && grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    return 0
  fi
  echo "${key}=${value}" >> "$ENV_FILE"
}

append_if_missing "PHOENIX_PORT" "6006"
append_if_missing "PHOENIX_GRPC_PORT" "4317"
append_if_missing "PHOENIX_SECRET" "$(openssl rand -hex 16)"
