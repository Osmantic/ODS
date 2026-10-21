#!/bin/sh
# Flowise — generate required credentials if not already set
# Usage: setup.sh INSTALL_DIR GPU_BACKEND

set -eu
umask 077

INSTALL_DIR="${1:-.}"
ENV_FILE="$INSTALL_DIR/.env"

# The pinned image writes encryption.key here without creating its parent.
# Keep the existing key location and never replace a retained key. Operators
# choosing a custom FLOWISE_SECRETKEY_PATH must provision that path separately.
mkdir -p "$INSTALL_DIR/data/flowise/secretkey"

append_if_missing() {
  key="$1"
  value="$2"
  if [ -f "$ENV_FILE" ] && grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    return 0
  fi
  echo "${key}=${value}" >> "$ENV_FILE"
}

append_if_missing "FLOWISE_USERNAME" "admin"
append_if_missing "FLOWISE_PASSWORD" "$(openssl rand -hex 16)"
