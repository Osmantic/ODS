#!/usr/bin/env bash
# Phase 11 starts the background full-model download as a transient systemd
# user unit. Without lingering, the user manager stops when the installer's
# login session ends and takes the unit with it, so the phase must enable
# linger before it starts the unit: directly, through sudo when that is
# available, or with a warning that names the manual command. A host whose
# user manager is unreachable uses the nohup fallback and leaves linger alone.
#
# Runs the real launch block from installers/phases/11-services.sh with
# systemd-run, systemctl and loginctl stubbed.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT_DIR/installers/phases/11-services.sh"

fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
pass() { printf '[PASS] %s\n' "$*"; }

# The `if command -v systemd-run ...; then ... fi` block of the launch.
block="$(awk '
    /^        if command -v systemd-run >\/dev\/null 2>&1 \\$/ { in_block = 1 }
    in_block { print }
    in_block && /^        fi$/ { exit }
' "$PHASE")"
[[ "$block" == *"systemd-run --user"* ]] || fail "could not find the systemd-run launch block in $PHASE"
[[ "$block" == *"enable-linger"* ]] || fail "the launch block never enables linger"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
BIN="$TMP_DIR/bin"
mkdir -p "$BIN"
CALLS="$TMP_DIR/calls.log"

# Stubs record every call in order. systemctl answers show-environment as the
# test asks, and reports a running main PID for the unit.
cat > "$BIN/systemd-run" <<'STUB'
#!/usr/bin/env bash
printf 'systemd-run %s\n' "$*" >> "$CALLS"
STUB
cat > "$BIN/systemctl" <<'STUB'
#!/usr/bin/env bash
printf 'systemctl %s\n' "$*" >> "$CALLS"
case " $* " in
    *" show-environment "*) [[ "${USER_MANAGER:-up}" == up ]] ;;
    *" --property=MainPID "*) printf '4242\n' ;;
esac
STUB
cat > "$BIN/loginctl" <<'STUB'
#!/usr/bin/env bash
printf 'loginctl %s\n' "$*" >> "$CALLS"
[[ "${LOGINCTL_OK:-yes}" == yes ]]
STUB
chmod +x "$BIN/systemd-run" "$BIN/systemctl" "$BIN/loginctl"

run_block() {
    : > "$CALLS"
    local runtime="$TMP_DIR/run-user"
    rm -rf "$runtime"
    mkdir -p "$runtime"
    # The block requires the user bus socket before it talks to systemd.
    python3 -c 'import socket, sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])' "$runtime/bus"
    (
        export CALLS PATH="$BIN:$PATH"
        ods_sudo_available() { [[ "${SUDO_OK:-no}" == yes ]]; }
        ods_sudo() { printf 'sudo %s\n' "$*" >> "$CALLS"; "$@"; }
        ai_warn() { printf 'warn %s\n' "$*" >> "$CALLS"; }
        _upgrade_unit=ods-model-upgrade.service
        _upgrade_log="$TMP_DIR/model-upgrade.log"
        _upgrade_pid=""
        _upgrade_systemd_started=false
        _upgrade_runtime_dir="$runtime"
        _upgrade_systemd_env=(env "XDG_RUNTIME_DIR=$runtime" "DBUS_SESSION_BUS_ADDRESS=unix:path=$runtime/bus")
        SCRIPT_DIR="$ROOT_DIR" INSTALL_DIR="$TMP_DIR/install"
        FULL_GGUF_FILE=full.gguf FULL_GGUF_URL=https://example.invalid/full.gguf
        FULL_GGUF_SHA256=0 FULL_LLM_MODEL=full FULL_MAX_CONTEXT=8192 BOOTSTRAP_GGUF_FILE=boot.gguf
        eval "$block"
    )
}

# Empty when the call never happened; the caller reports that, so a grep
# without a match must not end the test here under pipefail.
line_of() { grep -n -m1 -- "$1" "$CALLS" | cut -d: -f1 || true; }

# 1. loginctl enables linger for the owner, before the unit starts.
LOGINCTL_OK=yes SUDO_OK=no run_block
linger="$(line_of "loginctl enable-linger $(whoami)")"
start="$(line_of 'systemd-run --user')"
[[ -n "$linger" && -n "$start" && "$linger" -lt "$start" ]] \
    || fail "linger must be enabled before the download unit starts: $(cat "$CALLS")"
! grep -q '^warn ' "$CALLS" || fail "no warning expected when loginctl succeeds"
pass "linger is enabled before the background download unit starts"

# 2. Without polkit rights, sudo enables it when available.
LOGINCTL_OK=no SUDO_OK=yes run_block
grep -q "^sudo loginctl enable-linger $(whoami)$" "$CALLS" \
    || fail "linger must fall back to sudo when it is available: $(cat "$CALLS")"
pass "linger falls back to sudo"

# 3. Neither works: warn with the manual command, and still start the download.
LOGINCTL_OK=no SUDO_OK=no run_block
grep -q "^warn .*Run: loginctl enable-linger $(whoami)" "$CALLS" \
    || fail "a linger failure must warn with the manual command: $(cat "$CALLS")"
grep -q '^systemd-run --user' "$CALLS" || fail "a linger failure must not stop the download from starting"
pass "a linger failure warns and the download still starts"

# 4. No reachable user manager: the nohup fallback runs and linger is left alone.
USER_MANAGER=down run_block
! grep -q '^loginctl ' "$CALLS" || fail "linger must not change when the user manager is unreachable: $(cat "$CALLS")"
! grep -q '^systemd-run ' "$CALLS" || fail "the user unit must not start without a user manager"
pass "without a user manager, linger is left alone"
