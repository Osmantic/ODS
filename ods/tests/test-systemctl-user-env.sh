#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/bin"
cat > "$TMP/bin/systemctl" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
printf 'runtime=%s\n' "${XDG_RUNTIME_DIR:-}" > "$SYSTEMCTL_CAPTURE"
printf 'bus=%s\n' "${DBUS_SESSION_BUS_ADDRESS:-}" >> "$SYSTEMCTL_CAPTURE"
printf 'args=%s\n' "$*" >> "$SYSTEMCTL_CAPTURE"
STUB
chmod +x "$TMP/bin/systemctl"

run_probe() {
    local capture="$1"
    shift
    env -u XDG_RUNTIME_DIR -u DBUS_SESSION_BUS_ADDRESS \
        PATH="$TMP/bin:$PATH" SYSTEMCTL_CAPTURE="$capture" "$@"
}

capture_default="$TMP/default.env"
run_probe "$capture_default" bash -c \
    'source "$1"; ods_systemctl_user is-active opencode-web.service' _ \
    "$ROOT/installers/lib/constants.sh"

expected_runtime="/run/user/$(id -u)"
grep -Fxq "runtime=$expected_runtime" "$capture_default"
grep -Fxq "bus=unix:path=$expected_runtime/bus" "$capture_default"
grep -Fxq 'args=--user is-active opencode-web.service' "$capture_default"

capture_existing="$TMP/existing.env"
XDG_RUNTIME_DIR=/custom/runtime \
DBUS_SESSION_BUS_ADDRESS=unix:path=/custom/bus \
PATH="$TMP/bin:$PATH" SYSTEMCTL_CAPTURE="$capture_existing" \
    bash -c 'source "$1"; ods_systemctl_user daemon-reload' _ \
    "$ROOT/installers/lib/constants.sh"

grep -Fxq 'runtime=/custom/runtime' "$capture_existing"
grep -Fxq 'bus=unix:path=/custom/bus' "$capture_existing"
grep -Fxq 'args=--user daemon-reload' "$capture_existing"

capture_uninstall="$TMP/uninstall.env"
run_probe "$capture_uninstall" bash -c \
    'source <(sed -n "/^ods_uninstall_systemctl_user() {/,/^}/p" "$1"); ods_uninstall_systemctl_user disable --now opencode-web.service' _ \
    "$ROOT/ods-uninstall.sh"

grep -Fxq "runtime=$expected_runtime" "$capture_uninstall"
grep -Fxq "bus=unix:path=$expected_runtime/bus" "$capture_uninstall"
grep -Fxq 'args=--user disable --now opencode-web.service' "$capture_uninstall"

grep -Fq 'ods_systemctl_user enable --now "${timer}.timer"' \
    "$ROOT/installers/phases/10-amd-tuning.sh"
[[ "$(grep -Fc 'ods_systemctl_user is-active opencode-web' \
    "$ROOT/installers/phases/13-summary.sh")" -eq 2 ]]
! grep -Eq '^[[:space:]]*systemctl --user' \
    "$ROOT/installers/phases/10-amd-tuning.sh" \
    "$ROOT/installers/phases/13-summary.sh" \
    "$ROOT/ods-uninstall.sh"

printf '[PASS] unattended systemctl --user calls receive a reachable user-bus environment\n'
