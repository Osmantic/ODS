#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

fail() {
  echo "[FAIL] $*" >&2
  exit 1
}

pass() {
  echo "[OK] $*"
}

SERVICE="scripts/systemd/ods-host-agent.service"
UNINSTALL="ods-uninstall.sh"
AGENT="bin/ods-host-agent.py"

grep -q '^TimeoutStopSec=15$' "$SERVICE" \
  || fail "ods-host-agent systemd unit must bound service stop time"
pass "systemd unit has bounded stop timeout"

grep -q 'timeout 20s sudo systemctl disable --now ods-host-agent.service' "$UNINSTALL" \
  || fail "uninstall must bound systemctl disable --now for old/stuck host-agent services"
grep -q 'systemctl kill -s SIGKILL ods-host-agent.service' "$UNINSTALL" \
  || fail "uninstall must force-kill a stuck host-agent service after bounded stop"
pass "uninstall has bounded stop plus force-kill fallback"

grep -q 'def _request_server_shutdown' "$AGENT" \
  || fail "host-agent must expose async-safe shutdown helper"
grep -q 'target=server.shutdown' "$AGENT" \
  || fail "host-agent shutdown helper must call server.shutdown from a helper thread"
grep -q 'signal.SIGTERM.*_request_server_shutdown' "$AGENT" \
  || fail "host-agent SIGTERM handler must use async-safe shutdown helper"
pass "host-agent SIGTERM path is async-safe"
