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

SYSTEM_UNINSTALL="lib/system-uninstall.sh"
grep -q 'for unit in ods-host-agent.service ods-mdns.service; do' "$SYSTEM_UNINSTALL" \
  || fail "uninstall must enumerate ods-host-agent and ods-mdns system units for custody-verified stop"
grep -q 'run_sudo timeout 30s systemctl disable --now "$unit"' "$SYSTEM_UNINSTALL" \
  || fail "uninstall must bound systemctl disable --now for owned host-agent system units"
grep -q 'its files and installation were retained' "$SYSTEM_UNINSTALL" \
  || fail "uninstall must retain the installation when a stuck host-agent unit cannot be stopped"
pass "uninstall has bounded, custody-verified stop with retention on failure"

grep -q 'def _request_server_shutdown' "$AGENT" \
  || fail "host-agent must expose async-safe shutdown helper"
grep -q 'target=server.shutdown' "$AGENT" \
  || fail "host-agent shutdown helper must call server.shutdown from a helper thread"
grep -q 'signal.SIGTERM.*_request_server_shutdown' "$AGENT" \
  || fail "host-agent SIGTERM handler must use async-safe shutdown helper"
pass "host-agent SIGTERM path is async-safe"
