#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

# Simulate install in clean env
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# Copy necessary installer libs to tmpdir to avoid polluting repo
cp -r installers/lib "$TMPDIR"
cp installers/macos/lib/env-generator.sh "$TMPDIR/lib/"
cp installers/macos/lib/logging.sh "$TMPDIR/lib/"

# Change to tmpdir and source the env generator
cd "$TMPDIR"
source lib/logging.sh
source lib/env-generator.sh

# Generate .env (this function should write .env in current directory)
generate_env_vars

# Assert .env exists and contains N8N_API_KEY
[[ -f .env ]] || { echo "[FAIL] .env not created"; exit 1; }
grep -q '^N8N_API_KEY=' .env || { echo "[FAIL] N8N_API_KEY not set in .env"; exit 1; }

# Extract key and validate length (expect base64 string, min 32 chars)
key=$(grep '^N8N_API_KEY=' .env | cut -d'=' -f2)
[[ -n "$key" ]] || { echo "[FAIL] N8N_API_KEY empty"; exit 1; }
[[ ${#key} -ge 32 ]] || { echo "[FAIL] N8N_API_KEY too short (${#key} chars)"; exit 1; }

# Optional: verify it's valid base64 (alphanumeric, +, /, =)
if [[ ! "$key" =~ ^[A-Za-z0-9+/]+={0,2}$ ]]; then
    echo "[WARN] N8N_API_KEY may not be valid base64: $key"
fi

echo "[PASS] N8N_API_KEY provisioned: ${#key}-char base64 string"
exit 0