#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/scripts/ap-mode.sh"

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

# --- remove_iptables_rules must bound its -C/-D delete loop ---
# A rule that keeps checking present while -D fails used to spin forever
# and hang `down` (and `up`'s failure rollback).
# Note: sourcing ap-mode.sh above overwrote SCRIPT_DIR with its own
# scripts/ dir, so re-derive the absolute path for the subshell sources.
AP_MODE_SH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/ap-mode.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
STUB_BIN="$TMP_DIR/bin"
mkdir -p "$STUB_BIN"

export ODS_AP_INTERFACE=wlan0 ODS_AP_GATEWAY_IP=192.168.7.1

cat > "$STUB_BIN/iptables" <<'SH'
#!/usr/bin/env bash
# -C: report the tagged rule present; -D: fail every time; count -D calls.
case "$*" in
  *" -C "*) exit 0 ;;
  *" -D "*) echo x >> "$IPTABLES_D_COUNT_FILE"; exit 1 ;;
esac
exit 0
SH
chmod +x "$STUB_BIN/iptables"

D_COUNT="$TMP_DIR/d-count"; : > "$D_COUNT"
out="$TMP_DIR/remove.out"
set +e
# shellcheck disable=SC2016 # $1 is the bash -c positional arg, expanded inside
IPTABLES_D_COUNT_FILE="$D_COUNT" PATH="$STUB_BIN:$PATH" \
  timeout 15 bash -c 'source "$1"; remove_iptables_rules' _ \
  "$AP_MODE_SH" >"$out" 2>&1
rc=$?
set -e
[[ $rc -eq 0 ]] || fail "remove_iptables_rules hung or failed with a stuck rule (rc=$rc)"
d_calls=$(wc -l < "$D_COUNT" | tr -d ' ')
[[ "$d_calls" -le 20 ]] || fail "unbounded delete loop: $d_calls -D calls"
grep -q "still present" "$out" || fail "no warning when a rule cannot be deleted"

# The loop must still remove real duplicates: -C true twice then false.
cat > "$STUB_BIN/iptables" <<'SH'
#!/usr/bin/env bash
case "$*" in
  *" -C "*)
    reads=$(cat "$IPTABLES_C_COUNT_FILE" 2>/dev/null || echo 0)
    reads=$((reads + 1)); echo "$reads" > "$IPTABLES_C_COUNT_FILE"
    # Rule disappears after two successful -D calls (two dports => pattern
    # alternates; count deletions instead).
    dels=$(cat "$IPTABLES_D_COUNT_FILE" 2>/dev/null || echo 0)
    [[ $dels -lt 2 ]] && exit 0 || exit 1
    ;;
  *" -D "*)
    dels=$(cat "$IPTABLES_D_COUNT_FILE" 2>/dev/null || echo 0)
    echo $((dels + 1)) > "$IPTABLES_D_COUNT_FILE"
    exit 0
    ;;
esac
exit 0
SH
chmod +x "$STUB_BIN/iptables"

D_COUNT2="$TMP_DIR/d-count2"; : > "$D_COUNT2"
C_COUNT2="$TMP_DIR/c-count2"; : > "$C_COUNT2"
# shellcheck disable=SC2016 # $1 is the bash -c positional arg, expanded inside
IPTABLES_D_COUNT_FILE="$D_COUNT2" IPTABLES_C_COUNT_FILE="$C_COUNT2" \
  PATH="$STUB_BIN:$PATH" \
  timeout 15 bash -c 'source "$1"; remove_iptables_rules' _ \
  "$AP_MODE_SH" >/dev/null 2>&1 \
  || fail "remove_iptables_rules failed on a deletable duplicate rule"
d_deleted=$(tr -d ' ' < "$D_COUNT2")
[[ "$d_deleted" == "2" ]] || fail "expected 2 duplicate rules deleted, got $d_deleted"

# --- release_interface_from_nm must verify the resulting state ---
# nmcli `device set` returns non-zero both when NM never managed the iface
# and on real failure; only the post-state distinguishes them.
cat > "$STUB_BIN/nmcli" <<'SH'
#!/usr/bin/env bash
# `device set` fails (NM "busy"); `device show` reports $NM_STATE.
case "$*" in
  *"device show"*)
    [[ -n "${NM_STATE:-}" ]] && echo "GENERAL.STATE:${NM_STATE}"
    exit 0
    ;;
esac
exit 1
SH
chmod +x "$STUB_BIN/nmcli"

set +e
NM_STATE="30 (disconnected)" PATH="$STUB_BIN:$PATH" \
  bash -c 'source "$1"; release_interface_from_nm' _ \
  "$AP_MODE_SH" >/dev/null 2>&1
rc=$?
set -e
[[ $rc -ne 0 ]] || fail "release_interface_from_nm succeeded while NM still manages the iface"

set +e
NM_STATE="10 (unmanaged)" PATH="$STUB_BIN:$PATH" \
  bash -c 'source "$1"; release_interface_from_nm' _ \
  "$AP_MODE_SH" >/dev/null 2>&1
rc=$?
set -e
[[ $rc -eq 0 ]] || fail "release_interface_from_nm failed when the iface is already unmanaged"

# nmcli absent/failing on `device show` counts as unmanaged — nothing left
# to fight hostapd.
set +e
NM_STATE="" PATH="$STUB_BIN:$PATH" \
  bash -c 'source "$1"; release_interface_from_nm' _ \
  "$AP_MODE_SH" >/dev/null 2>&1
rc=$?
set -e
[[ $rc -eq 0 ]] || fail "release_interface_from_nm failed when NM cannot report the iface"

# --- reclaim_interface_for_nm warns but stays best-effort ---
out="$TMP_DIR/reclaim.out"
NM_STATE="10 (unmanaged)" PATH="$STUB_BIN:$PATH" \
  bash -c 'source "$1"; reclaim_interface_for_nm' _ \
  "$AP_MODE_SH" >"$out" 2>&1 \
  || fail "reclaim_interface_for_nm errored; cmd_down must stay best-effort"
grep -q "did not reclaim" "$out" || fail "failed reclaim produced no warning"

out="$TMP_DIR/reclaim-ok.out"
NM_STATE="30 (disconnected)" PATH="$STUB_BIN:$PATH" \
  bash -c 'source "$1"; reclaim_interface_for_nm' _ \
  "$AP_MODE_SH" >"$out" 2>&1 \
  || fail "reclaim_interface_for_nm errored on a successful reclaim"
if grep -q "did not reclaim" "$out"; then
  fail "successful reclaim emitted a spurious warning"
fi

printf 'AP mode helper checks passed\n'
