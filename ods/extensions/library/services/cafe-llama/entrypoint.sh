#!/usr/bin/env bash
set -euo pipefail

HOST="${CAFE_LLAMA_HOST:-0.0.0.0}"
PORT="${CAFE_LLAMA_PORT:-8081}"
MODEL="${CAFE_LLAMA_MODEL:-}"
BIN="${CAFE_LLAMA_SERVER:-}"

if [[ -z "${BIN}" ]]; then
  if command -v llama-server >/dev/null 2>&1; then
    BIN="$(command -v llama-server)"
  elif [[ -x /opt/cafe-llama/llama-server ]]; then
    BIN=/opt/cafe-llama/llama-server
  elif [[ -x /usr/local/bin/llama-server ]]; then
    BIN=/usr/local/bin/llama-server
  else
    echo "cafe-llama: llama-server binary not found." >&2
    echo "  • Rebuild the image with a valid CAFE_LLAMA_RELEASE_URL, or" >&2
    echo "  • Set CAFE_LLAMA_SERVER to an absolute path." >&2
    exit 1
  fi
fi

if [[ -z "${MODEL}" ]]; then
  echo "cafe-llama: set CAFE_LLAMA_MODEL to a GGUF or safetensors path under /models" >&2
  exit 1
fi

args=(-m "${MODEL}" --host "${HOST}" --port "${PORT}")

# Map structured env → flags when set (cafe-capable builds)
truthy() { case "${1:-}" in 1|true|TRUE|on|ON|yes|YES) return 0;; *) return 1;; esac; }

if truthy "${LLAMA_ARG_HOST_MOE:-}"; then args+=(-hmoe); fi
if [[ -n "${LLAMA_ARG_N_HOST_MOE:-}" ]]; then args+=(-nhmoe "${LLAMA_ARG_N_HOST_MOE}"); fi
if truthy "${LLAMA_ARG_CPU_MOE:-}"; then args+=(-cmoe); fi
if [[ -n "${LLAMA_ARG_N_CPU_MOE:-}" ]]; then args+=(-ncmoe "${LLAMA_ARG_N_CPU_MOE}"); fi
if truthy "${LLAMA_ARG_SSD_STREAMING:-}"; then args+=(-ssd); fi
if [[ -n "${LLAMA_ARG_N_SSD:-}" ]]; then args+=(-nssd "${LLAMA_ARG_N_SSD}"); fi
if [[ -n "${LLAMA_ARG_CACHE_TYPE_K:-}" ]]; then args+=(-ctk "${LLAMA_ARG_CACHE_TYPE_K}"); fi
if [[ -n "${LLAMA_ARG_CACHE_TYPE_V:-}" ]]; then args+=(-ctv "${LLAMA_ARG_CACHE_TYPE_V}"); fi
if [[ -n "${LLAMA_ARG_FLASH_ATTN:-}" ]]; then args+=(-fa "${LLAMA_ARG_FLASH_ATTN}"); fi
if [[ -n "${LLAMA_ARG_SPEC_TYPE:-}" ]]; then args+=(--spec-type "${LLAMA_ARG_SPEC_TYPE}"); fi
if [[ -n "${LLAMA_ARG_SPEC_DRAFT_N_MAX:-}" ]]; then args+=(--spec-draft-n-max "${LLAMA_ARG_SPEC_DRAFT_N_MAX}"); fi
if truthy "${LLAMA_ARG_PIPELINE_PARALLEL:-}"; then args+=(--pipeline-parallel); fi
if truthy "${LLAMA_ARG_NO_NGRAM:-}"; then args+=(--no-ngram); fi
if truthy "${LLAMA_ARG_NGRAM_SSD:-}"; then args+=(--ngram-ssd); fi

# shellcheck disable=SC2206
EXTRA=( ${CAFE_LLAMA_EXTRA_ARGS:-} )
args+=("${EXTRA[@]}")

echo "cafe-llama: starting ${BIN} with model ${MODEL}"
exec "${BIN}" "${args[@]}"
