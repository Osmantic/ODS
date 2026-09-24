#!/usr/bin/env bash
# Regression: ods_uninstall_systemctl_user must reach the invoking user's
# systemd manager even when the uninstaller runs under sudo. `id -u` reports 0
# under sudo, so XDG_RUNTIME_DIR=/run/user/0 talks to root's manager while the
# ODS user units (ods-host-agent, ods-model-upgrade) live in SUDO_UID's manager
# — silently surviving uninstall with their unit files already removed.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() { echo "[FAIL] $*" >&2; exit 1; }

# Extract only the helper under test.
eval "$(awk '/^ods_uninstall_systemctl_user\(\)/ {p=1} p; /^}/ && p {exit}' "$ROOT_DIR/ods-uninstall.sh")"

# Fake systemctl records the environment the helper computed.
mkdir -p "$TMP_DIR/bin"
cat > "$TMP_DIR/bin/systemctl" <<'STUB'
#!/usr/bin/env bash
printf 'XDG=%s\nDBUS=%s\nARGS=%s\n' "${XDG_RUNTIME_DIR:-}" "${DBUS_SESSION_BUS_ADDRESS:-}" "$*"
STUB
chmod +x "$TMP_DIR/bin/systemctl"
export PATH="$TMP_DIR/bin:$PATH"

FAKE_UID=1000
id() { if [[ "${1:-}" == "-u" ]]; then printf '%s\n' "$FAKE_UID"; else command id "$@"; fi; }

assert_bus() {
    local want_xdg="$1" label="$2"
    local out
    out="$(ods_uninstall_systemctl_user status example.service)"
    [[ "$out" == *"XDG=$want_xdg"* ]] || fail "$label: expected $want_xdg, got: $out"
    [[ "$out" == *"DBUS=unix:path=$want_xdg/bus"* ]] || fail "$label: bus address not derived from $want_xdg: $out"
    [[ "$out" == *"ARGS=--user status example.service"* ]] || fail "$label: arguments not forwarded: $out"
    echo "[PASS] $label"
}

unset XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS SUDO_UID 2>/dev/null || true

# Non-root invocation keeps targeting the caller's own manager.
FAKE_UID=1000
assert_bus "/run/user/1000" "plain user invocation targets uid 1000"

# Root shell without sudo context (direct root login) keeps uid 0.
FAKE_UID=0
assert_bus "/run/user/0" "root shell without SUDO_UID targets uid 0"

# `sudo ./ods-uninstall.sh`: id -u is 0 but the units belong to SUDO_UID.
FAKE_UID=0
export SUDO_UID=1000
assert_bus "/run/user/1000" "sudo invocation targets the invoking user's manager"

# Degenerate SUDO_UID values never escape the numeric /run/user path.
export SUDO_UID="0"
assert_bus "/run/user/0" "SUDO_UID=0 stays on root's manager"
export SUDO_UID='0;rm -rf /'
assert_bus "/run/user/0" "non-numeric SUDO_UID is ignored"
unset SUDO_UID

# An explicit operator-provided XDG_RUNTIME_DIR still wins.
FAKE_UID=0
export XDG_RUNTIME_DIR="/run/user/4242"
assert_bus "/run/user/4242" "explicit XDG_RUNTIME_DIR is honored under sudo"
unset XDG_RUNTIME_DIR

echo "All user-manager owner checks passed!"
