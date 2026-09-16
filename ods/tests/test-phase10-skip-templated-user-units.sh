#!/usr/bin/env bash
# Phase 10 (AMD tuning) copies scripts/systemd/*.{service,timer} into the
# user systemd dir for the maintenance timers. System-scope units
# (ods-host-agent, ods-ap-mode, ods-mdns) are templates with __PLACEHOLDER__
# tokens that phase 07 renders into /etc/systemd/system; copying them raw
# drops a broken, unrendered unit into the user scope (and it survives
# uninstall). The old skip list named only ods-host-agent and ods-ap-mode,
# so ods-mdns.service leaked. This extracts the real copy block from the
# phase and checks which units it installs.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="${ODS_PHASE10_UNDER_TEST:-$ROOT_DIR/installers/phases/10-amd-tuning.sh}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

[[ -f "$PHASE" ]] || fail "missing $PHASE"

# Extract the real "copy user-level systemd units" block (the `if [[ -d
# "$INSTALL_DIR/scripts/systemd" ]]; then ... fi`) from the phase, so the test
# exercises the shipped code rather than a copy of it.
block="$(awk '
    /if \[\[ -d "\$INSTALL_DIR\/scripts\/systemd" \]\]; then/ {grab=1}
    grab {print}
    grab && /^    fi$/ {exit}
' "$PHASE")"
[[ -n "$block" ]] || fail "could not extract the systemd-copy block from $PHASE"

# Fixture: a scripts/systemd dir with one placeholder (system-scope) unit and
# two plain user units, mirroring the real shipped set.
INSTALL_DIR="$TMP_DIR/install"
SYSTEMD_USER_DIR="$TMP_DIR/user-systemd"
mkdir -p "$INSTALL_DIR/scripts/systemd" "$SYSTEMD_USER_DIR"
cat > "$INSTALL_DIR/scripts/systemd/ods-mdns.service" <<'EOF'
[Service]
User=__INSTALL_USER__
ExecStart=__PYTHON3__ __INSTALL_DIR__/bin/ods-mdns.py
EOF
printf '[Timer]\nOnCalendar=daily\n' > "$INSTALL_DIR/scripts/systemd/memory-shepherd-workspace.timer"
printf '[Service]\nExecStart=/bin/true\n' > "$INSTALL_DIR/scripts/systemd/openclaw-session-cleanup.service"

export INSTALL_DIR SYSTEMD_USER_DIR
bash -c "$block"

[[ ! -e "$SYSTEMD_USER_DIR/ods-mdns.service" ]] \
    || fail "ods-mdns.service (a __PLACEHOLDER__ system unit) was copied into the user systemd dir"
pass "templated system unit ods-mdns.service is not copied into the user scope"

[[ -f "$SYSTEMD_USER_DIR/memory-shepherd-workspace.timer" ]] \
    || fail "memory-shepherd-workspace.timer (a plain user unit) was not copied"
[[ -f "$SYSTEMD_USER_DIR/openclaw-session-cleanup.service" ]] \
    || fail "openclaw-session-cleanup.service (a plain user unit) was not copied"
pass "plain user units are still copied"

# No unrendered placeholder may reach the user scope.
if grep -rql '__[A-Z0-9_]\{1,\}__' "$SYSTEMD_USER_DIR" 2>/dev/null; then
    fail "an unrendered __PLACEHOLDER__ unit reached the user systemd dir: $(grep -rl '__[A-Z0-9_]\{1,\}__' "$SYSTEMD_USER_DIR")"
fi
pass "no unrendered placeholder unit reached the user systemd dir"
