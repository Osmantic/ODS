#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
CODEX_VERSION=${PIXEL_FRONTIER_TEST_CODEX_VERSION:-0.147.0}
IMAGE="pixel-frontier-isolation-test:${CODEX_VERSION//[^A-Za-z0-9_.-]/-}"
mount_root=$ROOT
if command -v cygpath >/dev/null 2>&1; then mount_root=$(cygpath -w "$mount_root"); fi

docker build \
  --build-arg "CODEX_VERSION=$CODEX_VERSION" \
  --tag "$IMAGE" \
  "$ROOT/security-evals/frontier-isolation"

MSYS_NO_PATHCONV=1 docker run --rm --network none \
  --hostname pixel-frontier-isolation \
  --add-host pixel-frontier-isolation:127.0.0.1 \
  --mount "type=bind,source=$mount_root,target=/src,readonly" \
  "$IMAGE" bash /src/security-evals/frontier-isolation/inside-container.sh
