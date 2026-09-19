#!/bin/sh
# MinIO — ensure root credentials and data directory exist
# Usage: setup.sh [INSTALL_DIR] [GPU_BACKEND]

set -eu

INSTALL_DIR="${1:-.}"
ENV_FILE="${INSTALL_DIR}/.env"
DATA_DIR="${INSTALL_DIR}/data/minio"

mkdir -p "${DATA_DIR}"

append_if_missing() {
  key="$1"
  value="$2"
  if [ -f "$ENV_FILE" ] && grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    return 0
  fi
  echo "${key}=${value}" >> "$ENV_FILE"
}

append_if_missing "MINIO_ROOT_USER" "minioadmin"
append_if_missing "MINIO_ROOT_PASSWORD" "$(openssl rand -hex 16)"
append_if_missing "MINIO_PORT" "9020"
append_if_missing "MINIO_CONSOLE_PORT" "9021"
