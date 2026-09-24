#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="$ROOT_DIR/extensions/services/langfuse/compose.yaml.disabled"
LOCK="$ROOT_DIR/config/dependency-lock.json"

grep -q 'image: quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z' "$COMPOSE"
grep -q 'image: quay.io/minio/mc:RELEASE.2025-08-13T08-35-41Z' "$COMPOSE"
grep -q '"value": "quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z"' "$LOCK"
grep -q '"value": "quay.io/minio/mc:RELEASE.2025-08-13T08-35-41Z"' "$LOCK"
! grep -q 'image: minio/' "$COMPOSE"

echo "PASS: Langfuse MinIO images use the Quay registry and remain lock-pinned"
