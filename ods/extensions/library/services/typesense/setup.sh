#!/bin/sh
# Typesense — ensure API key and storage directory exist
# Usage: setup.sh [INSTALL_DIR] [GPU_BACKEND]

set -eu

INSTALL_DIR="${1:-.}"
ENV_FILE="${INSTALL_DIR}/.env"
DATA_DIR="${INSTALL_DIR}/data/typesense"

mkdir -p "${DATA_DIR}"

append_if_missing() {
  key="$1"
  value="$2"
  if [ -f "$ENV_FILE" ] && grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    return 0
  fi
  echo "${key}=${value}" >> "$ENV_FILE"
}

append_if_missing "TYPESENSE_PORT" "8108"
append_if_missing "TYPESENSE_API_KEY" "$(openssl rand -hex 16)"
