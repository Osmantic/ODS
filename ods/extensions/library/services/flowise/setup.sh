#!/bin/sh
# Flowise — generate required credentials if not already set
# Usage: setup.sh INSTALL_DIR GPU_BACKEND

set -eu

ENV_FILE="${1:-.}/.env"

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

append_if_missing "FLOWISE_USERNAME" printf '%s' admin
append_if_missing "FLOWISE_PASSWORD" openssl rand -hex 16
