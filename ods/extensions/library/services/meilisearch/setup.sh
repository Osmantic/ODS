#!/bin/sh
# Meilisearch — generate master key if not already set
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

# Meilisearch master key must be a secure UTF-8 string (at least 16 bytes)
if [ -z "${MEILI_MASTER_KEY:-}" ]; then
  append_if_missing "MEILI_MASTER_KEY" "$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
fi
append_if_missing "MEILI_ENV" "production"
