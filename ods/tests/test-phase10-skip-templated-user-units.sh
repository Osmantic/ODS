#!/usr/bin/env bash
# Phase 10 (AMD tuning) used to copy scripts/systemd/*.{service,timer} into
# the user systemd dir and enable maintenance timers. Copying raw templated
# system units (ods-host-agent, ods-ap-mode, ods-mdns) leaked broken units
# into the user scope, and every timer it enabled served only the removed
# legacy OpenClaw extension. The phase now installs no user units at all; on
# an upgrade it retires the legacy session-cleanup units an older install
# copied there, but only while they still carry the shipped definition, and
# leaves memory-shepherd timers as the owner configured them. It still enables
# linger, which phase 11's background model download needs after logout.
# This extracts the real retirement block from the phase and runs it.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="${ODS_PHASE10_UNDER_TEST:-$ROOT_DIR/installers/phases/10-amd-tuning.sh}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

[[ -f "$PHASE" ]] || fail "missing $PHASE"

# No unit is copied from scripts/systemd and no timer is enabled any more, so
# a templated system unit can never reach the user scope.
if grep -Fq 'scripts/systemd' "$PHASE"; then
    fail "phase 10 must not copy units from scripts/systemd into the user scope"
fi
if grep -Eq 'ods_systemctl_user[[:space:]]+enable|systemctl[[:space:]]+--user[[:space:]]+enable' "$PHASE"; then
    fail "phase 10 must not enable user maintenance timers"
fi
pass "phase 10 installs no user units, templated or otherwise"

[[ ! -e "$ROOT_DIR/scripts/systemd/openclaw-session-cleanup.service" \
    && ! -e "$ROOT_DIR/scripts/systemd/openclaw-session-cleanup.timer" ]] \
    || fail "the legacy session-cleanup units must not ship"
pass "the legacy session-cleanup units are not shipped"

# Phase 11 starts the background full-model download as a transient user unit
# (systemd-run --user); without linger it stops when the installing session
# ends. The AMD path enabled linger before this removal and must keep doing so.
amd_branch="$(awk '/^elif \[\[ "\$GPU_BACKEND" == "amd" \]\] && ! \$DRY_RUN; then$/ {grab=1} grab {print}' "$PHASE")"
grep -Fq 'loginctl enable-linger "$(whoami)"' <<<"$amd_branch" \
    || fail "phase 10 must keep enabling linger on AMD installs"
grep -Fq '_phase10_privileged loginctl enable-linger "$(whoami)"' <<<"$amd_branch" \
    || fail "phase 10 must retry linger with privileges"
pass "phase 10 keeps enabling linger for the background model download"

block="$(awk '
    /_phase10_user_units="\$HOME\/\.config\/systemd\/user"/ {grab=1}
    grab {print}
    grab && /^    unset _phase10_user_units / {exit}
' "$PHASE")"
[[ -n "$block" ]] || fail "could not extract the legacy unit retirement block from $PHASE"

run_block() {
    local home="$1" calls="$2"
    (
        HOME="$home"
        LOG_FILE="$TMP_DIR/phase10.log"
        ods_systemctl_user() { printf '%s\n' "$*" >>"$calls"; }
        log() { :; }
        ai_ok() { :; }
        eval "$block"
    )
}

write_shipped_units() {
    local units="$1" install_root="$2"
    printf '[Unit]\nDescription=OpenClaw Session Cleanup\nAfter=network.target\n\n[Service]\nType=oneshot\nExecStart=%%h/%s/scripts/session-cleanup.sh\n' \
        "$install_root" > "$units/openclaw-session-cleanup.service"
    printf '[Unit]\nDescription=OpenClaw Session Cleanup Timer\n\n[Timer]\nOnBootSec=30s\nOnUnitActiveSec=60s\n\n[Install]\nWantedBy=timers.target\n' \
        > "$units/openclaw-session-cleanup.timer"
}

# Upgrade: the shipped units and their enablement link are retired;
# memory-shepherd timers stay.
for install_root in ods dream-server; do
    upgrade_home="$TMP_DIR/upgrade-$install_root"
    units="$upgrade_home/.config/systemd/user"
    mkdir -p "$units/timers.target.wants"
    write_shipped_units "$units" "$install_root"
    ln -s ../openclaw-session-cleanup.timer "$units/timers.target.wants/openclaw-session-cleanup.timer"
    for unit in memory-shepherd-workspace.timer memory-shepherd-workspace.service; do
        printf '[Unit]\nDescription=fixture\n' > "$units/$unit"
    done
    calls="$TMP_DIR/upgrade-$install_root-calls"
    run_block "$upgrade_home" "$calls"
    [[ ! -e "$units/openclaw-session-cleanup.timer" && ! -e "$units/openclaw-session-cleanup.service" ]] \
        || fail "upgrade ($install_root) kept the legacy session-cleanup units"
    [[ ! -e "$units/timers.target.wants/openclaw-session-cleanup.timer" \
        && ! -L "$units/timers.target.wants/openclaw-session-cleanup.timer" ]] \
        || fail "upgrade ($install_root) left the timer's enablement link"
    [[ -f "$units/memory-shepherd-workspace.timer" && -f "$units/memory-shepherd-workspace.service" ]] \
        || fail "upgrade must leave memory-shepherd units as configured"
    grep -Fxq 'disable --now openclaw-session-cleanup.timer' "$calls" \
        || fail "upgrade ($install_root) did not stop the legacy session-cleanup timer"
    grep -Fxq 'daemon-reload' "$calls" || fail "upgrade did not reload the user manager"
done
pass "upgrade retires the shipped session-cleanup units and keeps memory-shepherd timers"

# Units an owner rewrote under the same names are not ODS's to remove.
foreign_home="$TMP_DIR/foreign-home"
units="$foreign_home/.config/systemd/user"
mkdir -p "$units"
printf '[Unit]\nDescription=OpenClaw Session Cleanup\n\n[Service]\nType=oneshot\nExecStart=%%h/bin/clean-pixel-sessions\n' \
    > "$units/openclaw-session-cleanup.service"
printf '[Unit]\nDescription=Owner session cleanup\n\n[Timer]\nOnBootSec=1h\n' \
    > "$units/openclaw-session-cleanup.timer"
calls="$TMP_DIR/foreign-calls"
run_block "$foreign_home" "$calls"
[[ -f "$units/openclaw-session-cleanup.service" && -f "$units/openclaw-session-cleanup.timer" ]] \
    || fail "upgrade removed session-cleanup units the owner rewrote"
[[ ! -s "$calls" ]] || fail "upgrade touched the user manager for foreign units: $(cat "$calls")"
pass "upgrade keeps session-cleanup units the owner rewrote"

# Fresh install: nothing to retire, no user-manager calls.
fresh_home="$TMP_DIR/fresh-home"
mkdir -p "$fresh_home"
calls="$TMP_DIR/fresh-calls"
run_block "$fresh_home" "$calls"
[[ ! -s "$calls" ]] || fail "fresh install touched the user systemd manager: $(cat "$calls")"
[[ ! -e "$fresh_home/.config/systemd/user" ]] || fail "fresh install created a user systemd dir"
pass "fresh install leaves the user systemd scope untouched"
