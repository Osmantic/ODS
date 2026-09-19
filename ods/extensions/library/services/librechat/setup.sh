#!/bin/sh
# LibreChat — generate required secrets if not already set
# Usage: setup.sh INSTALL_DIR GPU_BACKEND

set -eu

ENV_FILE="${1:-.}/.env"

generate_mongo_password() {
  # POSIX sh has no pipefail: check OpenSSL before the formatting pipeline.
  encoded=$(openssl rand -base64 24) || return 1
  printf '%s' "$encoded" | tr -d '/+=' | head -c 32
}

append_if_missing() {
  key="$1"
  shift
  if [ -f "$ENV_FILE" ] && grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    return 0
  fi
  # Generate only missing values, and preserve the generator's failure status.
  value=$("$@") || return 1
  if [ -z "$value" ]; then
    printf 'Could not generate %s: empty output\n' "$key" >&2
    return 1
  fi
  echo "${key}=${value}" >> "$ENV_FILE"
}

# Required secrets (compose uses :? — service won't start without these)
append_if_missing "JWT_SECRET" openssl rand -hex 32
append_if_missing "JWT_REFRESH_SECRET" openssl rand -hex 32
# Use base64 without special chars for MongoDB URI safety
append_if_missing "LIBRECHAT_MONGO_PASSWORD" generate_mongo_password
append_if_missing "LIBRECHAT_MEILI_KEY" openssl rand -hex 16

# Optional but recommended (compose uses :- defaults)
append_if_missing "CREDS_KEY" openssl rand -hex 16
append_if_missing "CREDS_IV" openssl rand -hex 16
