#!/bin/sh
# pgvector — ensure credentials and database storage directory exist
# Usage: setup.sh [INSTALL_DIR] [GPU_BACKEND]

set -eu

INSTALL_DIR="${1:-.}"
ENV_FILE="${INSTALL_DIR}/.env"
DATA_DIR="${INSTALL_DIR}/data/pgvector"

mkdir -p "${DATA_DIR}"

append_if_missing() {
  key="$1"
  value="$2"
  if [ -f "$ENV_FILE" ] && grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    return 0
  fi
  echo "${key}=${value}" >> "$ENV_FILE"
}

append_if_missing "PGVECTOR_USER" "postgres"
append_if_missing "PGVECTOR_DB" "postgres"
append_if_missing "PGVECTOR_PORT" "5435"
append_if_missing "PGVECTOR_PASSWORD" "$(openssl rand -hex 16)"
