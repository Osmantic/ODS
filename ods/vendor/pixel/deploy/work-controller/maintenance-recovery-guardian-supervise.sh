#!/usr/bin/env bash
set -euo pipefail
# Pixel maintenance recovery guardian: supervised systemd-user launch path.
#
# This script is a minimal fixed-path wrapper around the testable Node
# supervisor module. All authority-bearing logic (secure input resolution,
# closed prior-unit parsing, custody-serialized install/remove, transactional
# rollback, and closed systemd result interpretation) lives in
# maintenance-recovery-guardian-supervisor.mjs; the shell never parses JSON,
# never interprets systemd output, and never derives an authority path from
# ambient HOME/XDG/PATH.
#
# The supervisor module itself validates the exact trusted Node executable
# (/usr/bin/node) and its parent-chain ownership/mode exactly as strictly as it
# validates systemctl before rendering or mutating anything.
#
# Usage:
#   maintenance-recovery-guardian-supervise.sh --review  --config PRIVATE_JSON
#   maintenance-recovery-guardian-supervise.sh --render  --config PRIVATE_JSON
#   maintenance-recovery-guardian-supervise.sh --validate --config PRIVATE_JSON
#   maintenance-recovery-guardian-supervise.sh --install  --config PRIVATE_JSON --confirm-install-sha256 SHA256
#   maintenance-recovery-guardian-supervise.sh --remove   --config PRIVATE_JSON --confirm-remove-sha256 SHA256
#
# The validate mode runs `systemd-analyze verify` on the concrete rendered unit.

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "maintenance-recovery-guardian-supervise: systemd-user supervision is Linux-only; skipping on $(uname -s)" >&2
  exit 0
fi

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
SUPERVISOR="$ROOT/deploy/work-controller/maintenance-recovery-guardian-supervisor.mjs"
[[ -f "$SUPERVISOR" ]] || { echo "maintenance-recovery-guardian-supervise: supervisor module not found: $SUPERVISOR" >&2; exit 1; }

# Exact trusted Node executable for the supported deployment contract. The
# supervisor validates /usr/bin/node and its parent chain as strictly as
# systemctl; no ambient PATH is consulted and no override is honored.
NODE_BIN="/usr/bin/node"
[[ -x "$NODE_BIN" ]] || { echo "maintenance-recovery-guardian-supervise: trusted Node executable not found: $NODE_BIN" >&2; exit 1; }

exec "$NODE_BIN" "$SUPERVISOR" "$@"
