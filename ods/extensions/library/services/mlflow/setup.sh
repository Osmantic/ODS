#!/usr/bin/env bash
# Purpose: build the pinned MLflow authentication runtime during library install.
# Expects: Docker access; invoked from the installed extension directory.
# Provides: the local image referenced by compose.yaml.
# Modder notes: update the image tag here and in compose.yaml together.
set -euo pipefail

MLFLOW_EXTENSION_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
docker build --tag ods-mlflow:3.16.0-r1 --file "$MLFLOW_EXTENSION_DIR/Dockerfile" "$MLFLOW_EXTENSION_DIR"
