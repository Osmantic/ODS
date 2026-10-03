#!/bin/sh
set -eu

umask 077
[ "${PI_CODING_AGENT_DIR:-}" = "/tmp/agent" ] || {
  echo "Pixel Builder agent directory is invalid" >&2
  exit 78
}
[ "${PI_CONFIG_DIR:-}" = ".pixel-omp" ] || {
  echo "Pixel Builder configuration directory is invalid" >&2
  exit 78
}
[ "${HOME:-}" = "/tmp/home" ] || {
  echo "Pixel Builder home directory is invalid" >&2
  exit 78
}

# OMP 17.2.12 intentionally resolves task-agent definitions through its
# configuration root, independently of PI_CODING_AGENT_DIR runtime state.
install -d -m 0700 /tmp/home/.pixel-omp/agent/agents
install -m 0400 /opt/pixel/deploy/work-runner/builder-agent.md /tmp/home/.pixel-omp/agent/agents/task.md
exec /opt/omp "$@"
