#!/usr/bin/env bash
set -euo pipefail

port="${GAIA_INTERNAL_PORT:-4200}"

if [[ -n "${GAIA_LEMONADE_BASE_URL:-}" ]]; then
  export LEMONADE_BASE_URL="$GAIA_LEMONADE_BASE_URL"
fi

args=(--port "$port" --no-open)

case "${GAIA_UI_SERVE_ONLY:-false}" in
  1|true|TRUE|yes|YES|on|ON)
    args=(--serve "${args[@]}")
    ;;
esac

exec gaia-ui "${args[@]}"
