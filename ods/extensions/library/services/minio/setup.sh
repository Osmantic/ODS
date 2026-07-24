#!/bin/sh
# MinIO — generate required credentials if not already set
# Usage: setup.sh INSTALL_DIR GPU_BACKEND

set -eu

ENV_FILE="${1:-.}/.env"

append_if_missing() {
  key="$1"
  value="$2"
  if [ -f "$ENV_FILE" ] && grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    return 0
  fi
  echo "${key}=${value}" >> "$ENV_FILE"
}

append_if_missing "MINIO_ROOT_USER" "minioadmin"

# Generate password lazily — only call openssl when the key is actually absent
# (eager evaluation inside append_if_missing would abort under set -eu if openssl fails)
if ! [ -f "$ENV_FILE" ] || ! grep -q "^MINIO_ROOT_PASSWORD=" "$ENV_FILE" 2>/dev/null; then
  echo "MINIO_ROOT_PASSWORD=$(openssl rand -hex 16)" >> "$ENV_FILE"
fi
