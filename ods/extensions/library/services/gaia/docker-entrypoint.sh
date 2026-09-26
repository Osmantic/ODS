#!/usr/bin/env bash
# ODS entrypoint for the AMD GAIA Agent UI.
#
# Serve-only mode (gaia-ui --serve) is a Node static server that listens on
# every interface, so it runs directly on the service port.
#
# Full mode starts AMD's Python backend, which binds uvicorn to 127.0.0.1
# only (amd-gaia 0.19.0, gaia/cli.py _launch_agent_ui). Nothing outside the
# container could reach it: not the dashboard's health check, not the
# published host port. The backend therefore runs on a loopback-only port and
# ods-gaia-forward publishes it on the service port. This script supervises
# both: when either exits, it stops the other and exits non-zero so Docker's
# restart policy applies. TERM and INT stop both (with TERM: background jobs
# of a non-interactive shell ignore INT).
#
# GAIA's API has no authentication. Only its ngrok "mobile access" tunnel has
# a login, and that login trusts loopback peers, which every client behind the
# forwarder is. Tunnel mode must stay unavailable: refuse to start when an
# ngrok binary is on the PATH GAIA's backend searches.
set -euo pipefail

port="${GAIA_INTERNAL_PORT:-4200}"
backend_port="${GAIA_BACKEND_PORT:-4201}"
stop_grace_seconds=8

log() {
  echo "ods-gaia: $*" >&2
}

if [[ -n "${GAIA_LEMONADE_BASE_URL:-}" ]]; then
  export LEMONADE_BASE_URL="$GAIA_LEMONADE_BASE_URL"
fi

case "${GAIA_UI_SERVE_ONLY:-false}" in
  1|true|TRUE|yes|YES|on|ON)
    exec gaia-ui --serve --port "$port" --no-open
    ;;
esac

if [[ "$backend_port" == "$port" ]]; then
  log "GAIA_BACKEND_PORT must differ from GAIA_INTERNAL_PORT ($port)"
  exit 2
fi

# gaia-ui prepends ~/.gaia/bin (and a uv install dir) to the backend's PATH.
if ngrok_path="$(PATH="$HOME/.gaia/bin:$HOME/.local/bin:$PATH"; command -v ngrok)"; then
  log "refusing to start: found $ngrok_path. GAIA's tunnel login trusts loopback peers, and every client reaches this container's GAIA through a loopback forwarder."
  exit 1
fi

gaia_pid=""
forward_pid=""

alive() {
  [[ -n "$1" && -d "/proc/$1" ]]
}

stop_children() {
  local pid deadline
  for pid in "$gaia_pid" "$forward_pid"; do
    if alive "$pid"; then
      kill -TERM "$pid" || log "process $pid exited before it could be stopped"
    fi
  done
  deadline=$((SECONDS + stop_grace_seconds))
  while (( SECONDS < deadline )); do
    if ! alive "$gaia_pid" && ! alive "$forward_pid"; then
      return 0
    fi
    sleep 0.2
  done
  for pid in "$gaia_pid" "$forward_pid"; do
    if alive "$pid"; then
      log "process $pid ignored SIGTERM for ${stop_grace_seconds}s; sending SIGKILL"
      kill -KILL "$pid" || log "process $pid exited before SIGKILL"
    fi
  done
}

on_signal() {
  trap - TERM INT
  log "received SIG$1; stopping GAIA"
  stop_children
  exit "$2"
}

trap 'on_signal TERM 143' TERM
trap 'on_signal INT 130' INT

log "full mode: backend on 127.0.0.1:$backend_port, published on 0.0.0.0:$port by ods-gaia-forward"
gaia-ui --port "$backend_port" --no-open &
gaia_pid=$!
ods-gaia-forward "$port" "$backend_port" &
forward_pid=$!

exited_pid=""
status=0
wait -n -p exited_pid "$gaia_pid" "$forward_pid" || status=$?
if [[ "$exited_pid" == "$gaia_pid" ]]; then
  log "gaia-ui exited with status $status; stopping ods-gaia-forward"
else
  log "ods-gaia-forward exited with status $status; stopping gaia-ui"
fi
stop_children
# A server that stops on its own has failed, whatever its exit status.
if (( status == 0 )); then
  status=1
fi
exit "$status"
