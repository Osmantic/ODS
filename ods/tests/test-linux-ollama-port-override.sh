#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
phase="$root/installers/phases/06-directories.sh"
compose="$root/docker-compose.base.yml"

# A fresh or forced Linux install must carry an explicit alternate inference
# port into the generated .env before the exact Pixel prerequisite Compose run.
grep -Fq 'OLLAMA_PORT_VALUE="$(_env_get_explicit_first OLLAMA_PORT "11434")"' "$phase"
grep -Fq 'OLLAMA_PORT must be a port from 1 to 65535' "$phase"
grep -Fq 'OLLAMA_PORT=$(dotenv_value "${OLLAMA_PORT_VALUE}")' "$phase"
grep -Fq '${OLLAMA_PORT:-11434}:8080' "$compose"
tr -d '\r' < "$phase" | bash -n

echo 'Linux ODS inference port override contract passed'
