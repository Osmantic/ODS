#!/bin/sh
# Normalize known reasoning modes at every Docker launch. Preserve unknown
# values for llama.cpp to diagnose, and keep empty distinct from unset.
set -eu

case "${LLAMA_ARG_REASONING-}" in
  [Oo][Nn]) export LLAMA_ARG_REASONING=on ;;
  [Oo][Ff][Ff]) export LLAMA_ARG_REASONING=off ;;
  [Aa][Uu][Tt][Oo]) export LLAMA_ARG_REASONING=auto ;;
esac

# Normalize the opt-in diagnostics level to the stable --log-verbosity flag
# that both pinned builds accept (b9014 reads LLAMA_LOG_VERBOSITY; b11429
# reads LLAMA_ARG_LOG_VERBOSITY). Unset or empty leaves the args untouched so
# the upstream default applies. Invalid values fail before exec.
if [ -n "${LLAMA_LOG_VERBOSITY-}" ]; then
  case "$LLAMA_LOG_VERBOSITY" in
    0|1|2|3|4|5) ;;
    *)
      echo "llama-server-entrypoint: LLAMA_LOG_VERBOSITY must be an integer from 0 to 5" >&2
      exit 2
      ;;
  esac
  set -- "$@" --log-verbosity "$LLAMA_LOG_VERBOSITY"
fi
# b9014 also reads this name directly and rejects an empty env value.
# The normalized CLI flag is authoritative across both naming conventions.
unset LLAMA_LOG_VERBOSITY

# Official images use /app; the local Arc SYCL image installs on PATH.
# Replace this process so the server receives the original args and signals.
if [ -x /app/llama-server ]; then
  exec /app/llama-server "$@"
fi
exec llama-server "$@"
