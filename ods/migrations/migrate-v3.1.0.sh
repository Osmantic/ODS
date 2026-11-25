#!/bin/bash
# Migration: Lemonade runtime → upstream llama.cpp (round F)
# Description: ODS no longer installs or launches Lemonade. Move a Lemonade-era
#              .env to the llama.cpp runtime before the stack restarts: managed
#              AMD keeps its GGUF and active model on the llama.cpp container,
#              the Windows Portal route becomes the host-native llama-server
#              settings, and a Lemonade the owner runs becomes the generic
#              external OpenAI-compatible route (its LiteLLM map is rendered
#              here, since a source update does not run the installer).
#              Model state and router endpoints are re-proven by the host agent.
# Date: 2026-10-04
# Idempotent: an installation without Lemonade-era settings is not changed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$SCRIPT_DIR/..}"

if [[ ! -f "$INSTALL_DIR/.env" ]]; then
    echo "Migration v3.1.0: no .env at $INSTALL_DIR/.env — skipping"
    exit 0
fi
python3 "$INSTALL_DIR/scripts/migrate-lemonade-install.py" env --install-dir "$INSTALL_DIR" --render
echo "Migration v3.1.0 complete"
