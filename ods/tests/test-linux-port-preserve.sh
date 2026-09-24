#!/usr/bin/env bash
set -euo pipefail

# The generated .env is the only record of a customized service port. A rerun
# that rewrites the file with hardcoded defaults silently moves services back
# onto the ports the operator changed away from. Every installer-written port
# key must resolve env > existing .env > default before the heredoc emits it.

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
phase="$root/installers/phases/06-directories.sh"

fail() {
    printf '[FAIL] %s\n' "$1" >&2
    exit 1
}

# key:default pairs the installer writes to .env on every run.
while IFS=: read -r key default; do
    grep -Fq "${key}_VALUE=\"\$(_env_get_explicit_first ${key} \"${default}\")\"" "$phase" \
        || fail "${key} is not resolved through _env_get_explicit_first"
    grep -Fq "${key}=\$(dotenv_value \"\${${key}_VALUE}\")" "$phase" \
        || fail "${key} is not emitted through dotenv_value"
    if grep -Eq "^${key}=[0-9]+\$" "$phase"; then
        fail "${key} is still hardcoded in the .env template"
    fi
done <<'PORTS'
WEBUI_PORT:3000
SEARXNG_PORT:8888
PERPLEXICA_PORT:3004
TTS_PORT:8880
N8N_PORT:5678
QDRANT_PORT:6333
QDRANT_GRPC_PORT:6334
EMBEDDINGS_PORT:8090
LITELLM_PORT:4000
OPENCLAW_PORT:7860
HERMES_PROXY_PORT:9120
PORTS

tr -d '\r' < "$phase" | bash -n

echo 'Linux service port preservation contract passed'
