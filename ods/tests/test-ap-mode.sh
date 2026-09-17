#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck disable=SC1091
source "${ROOT_DIR}/scripts/ap-mode.sh"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_eq() {
  local actual="$1"
  local expected="$2"
  local label="$3"
  [[ "$actual" == "$expected" ]] || fail "${label}: expected ${expected}, got ${actual}"
}

assert_fails() {
  local label="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    fail "${label}: expected command to fail"
  fi
}

assert_eq "$(_netmask_to_prefix 255.255.255.0)" "24" "netmask /24"
assert_eq "$(_netmask_to_prefix 255.255.254.0)" "23" "netmask /23"
assert_eq "$(_netmask_to_prefix 255.255.255.128)" "25" "netmask /25"
assert_fails "non-contiguous netmask" _netmask_to_prefix 255.0.255.0
assert_fails "too few netmask octets" _netmask_to_prefix 255.255.0

ODS_AP_PASSWORD="changeme-set-per-device"
assert_fails "placeholder AP password" require_password

ODS_AP_PASSWORD="1234567"
assert_fails "short AP password" require_password

ODS_AP_PASSWORD="unique-device-pass"
require_password >/dev/null

# Execute the public `up` command with a controlled command boundary. Failure
# after NetworkManager releases the interface must reclaim it and remove every
# partial runtime artifact before returning non-zero.
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT
fake_bin="$work_dir/bin"
run_dir="$work_dir/run"
command_log="$work_dir/commands.log"
mkdir -p "$fake_bin" "$run_dir"

for binary in hostapd dnsmasq pgrep pkill; do
  printf '#!/usr/bin/env bash\nexit 1\n' > "$fake_bin/$binary"
  chmod +x "$fake_bin/$binary"
done
cat > "$fake_bin/id" <<'SH'
#!/usr/bin/env bash
[[ "${1:-}" == "-u" ]] && { echo 0; exit 0; }
[[ "${1:-}" == "-un" ]] && { echo root; exit 0; }
exit 1
SH
cat > "$fake_bin/uname" <<'SH'
#!/usr/bin/env bash
echo Linux
SH
cat > "$fake_bin/iw" <<'SH'
#!/usr/bin/env bash
printf 'Supported interface modes:\n\t * AP\n'
SH
cat > "$fake_bin/nmcli" <<'SH'
#!/usr/bin/env bash
printf 'nmcli %s\n' "$*" >> "$ODS_AP_TEST_LOG"
SH
cat > "$fake_bin/iptables" <<'SH'
#!/usr/bin/env bash
printf 'iptables %s\n' "$*" >> "$ODS_AP_TEST_LOG"
exit 1
SH
cat > "$fake_bin/ip" <<'SH'
#!/usr/bin/env bash
printf 'ip %s\n' "$*" >> "$ODS_AP_TEST_LOG"
[[ "$*" == *"addr add"* ]] && exit 42
exit 0
SH
chmod +x "$fake_bin"/*

set +e
up_output="$({
  PATH="$fake_bin:$PATH" \
  ODS_AP_TEST_LOG="$command_log" \
  ODS_AP_RUN_DIR="$run_dir" \
  ODS_AP_CONF_DIR="$work_dir/conf" \
  ODS_AP_PASSWORD="unique-device-pass" \
  bash "${ROOT_DIR}/scripts/ap-mode.sh" up
} 2>&1)"
up_rc=$?
set -e

[[ "$up_rc" -ne 0 ]] || fail "failed interface configuration reported success"
[[ "$up_output" == *"rolling back interface and firewall state"* ]] \
  || fail "failed activation did not report rollback"
grep -Fq 'nmcli device set wlan0 managed no' "$command_log" \
  || fail "activation never released the interface"
grep -Fq 'nmcli device set wlan0 managed yes' "$command_log" \
  || fail "rollback did not reclaim the interface"
[[ ! -e "$run_dir/state.json" && ! -e "$run_dir/hostapd.conf" && ! -e "$run_dir/dnsmasq.conf" ]] \
  || fail "rollback left partial AP runtime files"

printf 'AP mode helper checks passed\n'
