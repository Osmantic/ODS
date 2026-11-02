#!/bin/sh
# Normalize known reasoning modes at every Docker launch. Preserve unknown
# values for llama.cpp to diagnose, and keep empty distinct from unset.
set -eu

case "${LLAMA_ARG_REASONING-}" in
  [Oo][Nn]) export LLAMA_ARG_REASONING=on ;;
  [Oo][Ff][Ff]) export LLAMA_ARG_REASONING=off ;;
  [Aa][Uu][Tt][Oo]) export LLAMA_ARG_REASONING=auto ;;
esac

# Official images use /app; the local Arc SYCL image installs on PATH.
# Replace this process so the server receives the original args and signals.
if [ -x /app/llama-server ]; then
  exec /app/llama-server "$@"
fi
exec llama-server "$@"
