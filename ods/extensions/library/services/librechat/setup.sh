#!/bin/sh
# LibreChat — generate required secrets if not already set
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

# Required secrets (compose uses :? — service won't start without these)
append_if_missing "JWT_SECRET" "$(openssl rand -hex 32)"
append_if_missing "JWT_REFRESH_SECRET" "$(openssl rand -hex 32)"
# Use base64 without special chars for MongoDB URI safety
append_if_missing "LIBRECHAT_MONGO_PASSWORD" "$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)"
append_if_missing "LIBRECHAT_MEILI_KEY" "$(openssl rand -hex 16)"

# Optional but recommended (compose uses :- defaults)
append_if_missing "CREDS_KEY" "$(openssl rand -hex 16)"
append_if_missing "CREDS_IV" "$(openssl rand -hex 16)"
