#!/bin/sh
# AnythingLLM — generate required secrets if not already set
# Usage: setup.sh INSTALL_DIR GPU_BACKEND

set -eu

ENV_FILE="${1:-.}/.env"

generate_secret() {
  openssl rand -hex 32
}

# Only append if the variable is not already defined in .env
append_if_missing() {
  key="$1"
  value="$2"
  if [ -f "$ENV_FILE" ] && grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    return 0
  fi
  echo "${key}=${value}" >> "$ENV_FILE"
}

append_if_missing "ANYTHINGLLM_JWT_SECRET" "$(generate_secret)"
append_if_missing "ANYTHINGLLM_AUTH_TOKEN" "$(generate_secret)"
