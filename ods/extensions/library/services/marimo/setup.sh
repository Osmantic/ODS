#!/usr/bin/env bash
# Purpose: build the pinned notebook runtime through the existing install hook.
# Expects: Docker access; invoked from the installed extension directory.
# Provides: the local image referenced by compose.yaml.
# Modder notes: update this image tag and compose.yaml together when upgrading.
set -euo pipefail

MARIMO_EXTENSION_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
docker build --tag ods-marimo:0.24.2-r1 --file "$MARIMO_EXTENSION_DIR/Dockerfile" "$MARIMO_EXTENSION_DIR"
