#!/usr/bin/env bash
# cafe-llama entrypoint (scaffold)
# Does not install binaries; expects CAFE_LLAMA_SERVER or PATH llama-server from cafe builds.
set -euo pipefail

HOST="${CAFE_LLAMA_HOST:-127.0.0.1}"
PORT="${CAFE_LLAMA_PORT:-8081}"
MODEL="${CAFE_LLAMA_MODEL:-${LLAMA_ARG_MODEL:-}}"
BIN="${CAFE_LLAMA_SERVER:-llama-server}"

if [[ -z "${MODEL}" ]]; then
  echo "cafe-llama: set CAFE_LLAMA_MODEL or LLAMA_ARG_MODEL" >&2
  exit 1
fi

if ! command -v "${BIN}" >/dev/null 2>&1; then
  echo "cafe-llama: binary not found: ${BIN}" >&2
  echo "Install a cafe-llama.cpp release or set CAFE_LLAMA_SERVER to the llama-server path." >&2
  exit 1
fi

# Base args — callers / compose may append extra cafe flags via CAFE_LLAMA_EXTRA_ARGS
# shellcheck disable=SC2206
EXTRA=( ${CAFE_LLAMA_EXTRA_ARGS:-} )

exec "${BIN}" \
  -m "${MODEL}" \
  --host "${HOST}" \
  --port "${PORT}" \
  "${EXTRA[@]}"
