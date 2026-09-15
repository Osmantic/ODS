#!/bin/sh
# Mailpit — ensure data directory and ports configuration exist
# Usage: setup.sh [INSTALL_DIR] [GPU_BACKEND]

set -eu

INSTALL_DIR="${1:-.}"
ENV_FILE="${INSTALL_DIR}/.env"
DATA_DIR="${INSTALL_DIR}/data/mailpit"

mkdir -p "${DATA_DIR}"

append_if_missing() {
  key="$1"
  value="$2"
  if [ -f "$ENV_FILE" ] && grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    return 0
  fi
  echo "${key}=${value}" >> "$ENV_FILE"
}

append_if_missing "MAILPIT_PORT" "8025"
append_if_missing "MAILPIT_SMTP_PORT" "1025"
