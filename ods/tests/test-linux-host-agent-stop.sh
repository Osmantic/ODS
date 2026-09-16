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
SYSTEM_UNINSTALL="lib/system-uninstall.sh"
AGENT="bin/ods-host-agent.py"

grep -q '^TimeoutStopSec=15$' "$SERVICE" \
  || fail "ods-host-agent systemd unit must bound service stop time"
pass "systemd unit has bounded stop timeout"

# The system-scope cleanup no longer lives in ods-uninstall.sh: it loops over
# every owned unit in lib/system-uninstall.sh, and a unit that will not stop
# now retains the installation instead of being force-killed. Assert the
# properties that survived the move rather than one literal command line.
grep -Eq 'ods_uninstall_system_units[[:space:]]+"\$INSTALL_DIR"' "$UNINSTALL" \
  || fail "uninstall must delegate system unit cleanup to $SYSTEM_UNINSTALL"
grep -q 'System service uninstall helper is missing; installation retained' "$UNINSTALL" \
  || fail "uninstall must refuse to continue when the cleanup helper is missing"
pass "uninstall delegates system unit cleanup and fails closed without the helper"

grep -Eq 'run_sudo timeout [0-9]+s systemctl disable --now' "$SYSTEM_UNINSTALL" \
  || fail "system unit cleanup must bound systemctl disable --now for old/stuck units"
grep -q 'its files and installation were retained' "$SYSTEM_UNINSTALL" \
  || fail "system unit cleanup must retain the installation when a unit will not stop"
pass "system unit cleanup bounds the stop and retains state on failure"

grep -q 'def _request_server_shutdown' "$AGENT" \
  || fail "host-agent must expose async-safe shutdown helper"
grep -q 'target=server.shutdown' "$AGENT" \
  || fail "host-agent shutdown helper must call server.shutdown from a helper thread"
grep -q 'signal.SIGTERM.*_request_server_shutdown' "$AGENT" \
  || fail "host-agent SIGTERM handler must use async-safe shutdown helper"
pass "host-agent SIGTERM path is async-safe"
