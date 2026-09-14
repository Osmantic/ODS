#!/usr/bin/env bash
# Purpose: Launch the Aider CLI while preserving the extension's one-shot install receipt.
# Expects: Run from the ODS install directory; Docker Compose and the Aider extension.
# Provides: Interactive Aider process with caller arguments passed unchanged.
# Modder notes: The image executable overrides the Compose installation-only echo entrypoint.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_ROOT="$PWD"
if [[ ! -f "$INSTALL_ROOT/docker-compose.base.yml" ]]; then
    printf '%s\n' 'Run this command from your ODS install directory.' >&2
    exit 1
fi

exec docker compose --project-directory "$INSTALL_ROOT" \
    --env-file "$INSTALL_ROOT/.env" -f "$SCRIPT_DIR/compose.yaml" \
    run --rm --no-deps --entrypoint /venv/bin/aider aider "$@"
