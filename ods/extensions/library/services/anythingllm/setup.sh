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

append_if_missing "ANYTHINGLLM_JWT_SECRET" generate_secret
append_if_missing "ANYTHINGLLM_AUTH_TOKEN" generate_secret
