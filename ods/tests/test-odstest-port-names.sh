#!/usr/bin/env bash
set -euo pipefail

# ods-test.sh must honor the canonical port names that .env, the schema, and
# compose actually use. Today it reads shadow names that are not the names
# installers write — and one fallback default that nothing binds:
#
#   LLM_PORT             ignores an exported OLLAMA_PORT (canonical, schema)
#   EMBEDDING_PORT       ignores EMBEDDINGS_PORT and falls back to 9103 even
#                        though every other surface defaults to 8090
#   PRIVACY_SHIELD_PORT  ignores SHIELD_PORT (manifest external_port_env)

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script="$root/scripts/ods-test.sh"

fail() {
    printf '[FAIL] %s\n' "$1" >&2
    exit 1
}

# Canonical env names must be consulted for the llama-server port.
grep -Fq '${OLLAMA_PORT:-' "$script" \
    || fail "LLM port resolution ignores exported OLLAMA_PORT"

# Canonical EMBEDDINGS_PORT must be consulted, and the dead 9103 fallback gone
# from both test entry points (manifest/compose/.env all default to 8090).
grep -Fq '${EMBEDDINGS_PORT:-' "$script" \
    || fail "embeddings port resolution ignores exported EMBEDDINGS_PORT"
functional="$root/scripts/ods-test-functional.sh"
if grep -Fq '9103' "$script" || grep -Fq '9103' "$functional"; then
    fail "embeddings still falls back to dead port 9103"
fi

# Canonical SHIELD_PORT must be consulted for the privacy-shield port.
grep -Fq '${SHIELD_PORT:-' "$script" \
    || fail "privacy-shield port resolution ignores exported SHIELD_PORT"

tr -d '\r' < "$script" | bash -n

echo 'ods-test canonical port name contract passed'
