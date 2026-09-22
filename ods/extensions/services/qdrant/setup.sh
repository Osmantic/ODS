#!/bin/sh
# Qdrant — generate API key if not already set
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

# Generate a 32-byte cryptographic hex token if QDRANT_API_KEY is unset
if [ -z "${QDRANT_API_KEY:-}" ]; then
  append_if_missing "QDRANT_API_KEY" "$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
fi
