#!/usr/bin/env bash
# Minimal stub for `docker` CLI — just enough for cluster_worker_agent.py.
# Container state is persisted in /tmp/docker-stub/.
set -u

STATE=/tmp/docker-stub
mkdir -p "$STATE"

case "${1:-}" in
  image)
    case "${2:-}" in
      inspect)
        # Pretend every image exists.
        exit 0
        ;;
    esac
    ;;
  stop|rm)
    name="${2:-}"
    if [[ -f "$STATE/$name.pid" ]]; then
      pid=$(cat "$STATE/$name.pid")
      kill -TERM "$pid" 2>/dev/null || true
      # Give it a moment to release the port.
      for _ in 1 2 3 4 5; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.1
      done
      kill -KILL "$pid" 2>/dev/null || true
    fi
    rm -f "$STATE/$name.pid" "$STATE/$name.running" 2>/dev/null
    exit 0
    ;;
  run)
    # Parse minimal args: -d, --name NAME, -p HOSTPORT:CONTPORT
    name=""
    hostport=""
    shift
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --name) name="$2"; shift 2 ;;
        -p) hostport="${2%%:*}"; shift 2 ;;
        -d|--restart|no|--gpus|all|--device|--group-add) shift ;;
        -*) shift ;;
        *) shift ;;
      esac
    done
    [[ -z "$name" ]] && name="stub-container"
    # Spawn a trivial TCP listener on the host port to simulate rpc-server.
    if [[ -n "$hostport" ]]; then
      (python3 -c "
import socket, signal, sys
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('0.0.0.0', $hostport)); s.listen(5)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
while True:
    try:
        c, _ = s.accept(); c.close()
    except Exception: pass
" >/tmp/docker-stub/$name.log 2>&1) &
      echo $! > "$STATE/$name.pid"
    fi
    echo "running" > "$STATE/$name.running"
    echo "stub-container-id-$name"
    exit 0
    ;;
  inspect)
    # Expect:  inspect -f {{.State.Running}} NAME
    name="${4:-}"
    if [[ -f "$STATE/$name.running" ]]; then
      echo "true"
      exit 0
    fi
    echo "false"
    exit 1
    ;;
esac

echo "docker-stub: unhandled args: $*" >&2
exit 0
