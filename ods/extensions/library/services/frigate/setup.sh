#!/bin/sh
# Frigate — generate RTSP password if not already set
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

append_if_missing "FRIGATE_RTSP_PASSWORD" "$(openssl rand -hex 16)"
