#!/usr/bin/env bash
# Purpose: build the pinned official PocketBase release into the ODS runtime.
# Expects: Docker access; invoked from the installed extension directory.
# Provides: the local image referenced by compose.yaml.
# Modder notes: keep the local image tag and release checksums in sync.
set -euo pipefail

POCKETBASE_EXTENSION_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
docker build --tag ods-pocketbase:0.40.4-r1 --file "$POCKETBASE_EXTENSION_DIR/Dockerfile" "$POCKETBASE_EXTENSION_DIR"
