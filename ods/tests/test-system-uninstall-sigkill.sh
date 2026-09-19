#!/usr/bin/env bash
# Regression: when `systemctl disable --now` times out on a wedged service,
# `timeout` kills the systemctl client — not the service. The unit stayed
# active, the post-stop state check failed, and the uninstaller aborted with
# files retained on every retry. The stop path must escalate to
# `systemctl kill --signal=SIGKILL`, matching the orphan-PID reaper's
# TERM→KILL two-pass in ods-uninstall.sh.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB="$ROOT_DIR/lib/system-uninstall.sh"

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fakebin="$tmp/bin"
systemd_dir="$tmp/systemd"
mkdir -p "$fakebin" "$systemd_dir" "$tmp/install"

unit_file="$systemd_dir/ods-host-agent.service"
printf '[Unit]\nDescription=ODS host agent\n' > "$unit_file"

state_file="$tmp/active-state"
printf 'active\n' > "$state_file"

cat > "$fakebin/systemctl" <<EOF
#!/usr/bin/env bash
echo "\$*" >> "$tmp/systemctl.log"
case " \$* " in
    *" --property=FragmentPath "*) printf '%s\n' "$unit_file" ;;
    *" --property=DropInPaths "*) printf '\n' ;;
    *" --property=ActiveState "*) cat "$state_file" ;;
    *" disable --now "*) exit 124 ;;                    # wedged: client timeout
    *" kill --signal=SIGKILL "*) printf 'inactive\n' > "$state_file" ;;
esac
exit 0
EOF
cat > "$fakebin/python3" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat > "$fakebin/timeout" <<'EOF'
#!/usr/bin/env bash
shift  # drop the duration argument
exec "$@"
EOF
cat > "$fakebin/sleep" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$fakebin/"*

PATH="$fakebin:$PATH" bash -c '
    set -euo pipefail
    run_sudo() { "$@"; }
    prepare_sudo_credential() { :; }
    log_error() { echo "[ERROR] $*" >&2; }
    ODS_UNINSTALL_SYSTEMD_DIR="$1"
    . "$2"
    ods_uninstall_system_units "$3" "$HOME"
' _ "$systemd_dir" "$LIB" "$tmp/install" || fail "uninstall aborted despite SIGKILL escalation"

grep -q 'kill --signal=SIGKILL ods-host-agent.service' "$tmp/systemctl.log" \
    || fail "wedged service did not get SIGKILL escalation: $(cat "$tmp/systemctl.log")"
grep -q 'daemon-reload' "$tmp/systemctl.log" || fail "daemon-reload not called"
[[ ! -f "$unit_file" ]] || fail "unit file was retained after successful stop"
pass "wedged service escalates to SIGKILL and unit file is removed"

echo "All system-uninstall SIGKILL checks passed."
