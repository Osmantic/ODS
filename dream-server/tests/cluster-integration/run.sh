#!/usr/bin/env bash
# Build real llama.cpp images (first run: ~5-10 min), launch rpc workers
# + llama controller, then run integration tests against live endpoints.
#
# Usage:
#   ./run.sh                # full suite
#   ./run.sh test_completion_returns_tokens_across_rpc_workers  # subset
set -euo pipefail

cd "$(dirname "$0")"

COMPOSE="docker compose -f docker-compose.integration.yml"

# Stage the supervisor into the llama-rpc build context.
# The canonical lives at scripts/dream-cluster-supervisor.py and is
# .gitignore'd inside images/llama-rpc/ — Docker COPY can't read outside
# the build context, so we mirror it before building (same as
# installers/phases/08-images.sh does at install time).
SUPERVISOR_SRC="../../scripts/dream-cluster-supervisor.py"
SUPERVISOR_DST="../../images/llama-rpc/dream-cluster-supervisor.py"
if [[ ! -f "$SUPERVISOR_SRC" ]]; then
    echo "Canonical supervisor missing: $SUPERVISOR_SRC" >&2
    exit 1
fi
install -m 0644 "$SUPERVISOR_SRC" "$SUPERVISOR_DST"

# Pre-fetch the GGUF on the host and mount it into llama-server.
# The production cluster image is built without OpenSSL (smaller image,
# matches how real installs work — models are mounted, not downloaded),
# so the in-container -hfr/-hff path can't reach huggingface.co. Mirror
# prod by mounting a pre-downloaded file at /models. Cached across runs.
MODEL_DIR="./.model-cache"
MODEL_FILE="qwen2.5-0.5b-instruct-q2_k.gguf"
MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/${MODEL_FILE}"
mkdir -p "$MODEL_DIR"
if [[ ! -s "$MODEL_DIR/$MODEL_FILE" ]]; then
    echo "Fetching test model ($MODEL_FILE, ~340MB) to $MODEL_DIR/"
    curl -fL --retry 3 -o "$MODEL_DIR/$MODEL_FILE.partial" "$MODEL_URL"
    mv "$MODEL_DIR/$MODEL_FILE.partial" "$MODEL_DIR/$MODEL_FILE"
fi

cleanup() {
    $COMPOSE down --remove-orphans --volumes 2>/dev/null || true
    rm -f "$SUPERVISOR_DST"
}
trap cleanup EXIT

$COMPOSE build
$COMPOSE up -d rpc1 rpc2 llama
$COMPOSE run --rm test-runner /app/run-tests.sh "$@"
rc=$?

cleanup
trap - EXIT
exit $rc
