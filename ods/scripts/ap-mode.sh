#!/usr/bin/env bash
# ap-mode.sh — bring up / tear down ODS's first-boot AP.
#
# Mode of operation:
#   up      Bring up the AP (configure interface, start hostapd + dnsmasq,
#           install iptables rules). DESTRUCTIVE: takes the configured
#           wireless interface off NetworkManager.
#   down    Stop hostapd + dnsmasq, remove iptables rules, hand the
#           interface back to NetworkManager. Idempotent.
#   status  Print a JSON status snapshot. Read-only, safe to run.
#
# OPT-IN ONLY. The systemd unit shipped alongside this script is
# disabled by default. The first-boot wizard in PR-11 will toggle it
# when ODS_AP_MODE=true is set in .env AND the device isn't yet
# configured. Until then, every invocation has to be explicit.
#
# This script is Linux-only and assumes:
#   * hostapd, dnsmasq, iptables installed and on $PATH
#   * NetworkManager available (we use nmcli to release / reclaim the iface)
#   * The wireless interface supports AP mode (`iw list | grep -A4 "Supported interface modes" | grep AP`)
#   * Run as root (or under a service unit with the right capabilities)
#
# Config: read from /etc/ods/ap-mode.conf if it exists, otherwise
# falls back to sane defaults documented in docs/AP-MODE.md.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_DIR="${ODS_AP_CONF_DIR:-/etc/ods}"
RUN_DIR="${ODS_AP_RUN_DIR:-/run/ods-ap-mode}"
STATE_FILE="${RUN_DIR}/state.json"
HOSTAPD_CONF="${RUN_DIR}/hostapd.conf"
DNSMASQ_CONF="${RUN_DIR}/dnsmasq.conf"
HOSTAPD_PID="${RUN_DIR}/hostapd.pid"
DNSMASQ_PID="${RUN_DIR}/dnsmasq.pid"

# Defaults — override in /etc/ods/ap-mode.conf
ODS_AP_SSID="${ODS_AP_SSID:-ODS-Setup}"
ODS_AP_PASSWORD="${ODS_AP_PASSWORD:-}"
ODS_AP_INTERFACE="${ODS_AP_INTERFACE:-wlan0}"
ODS_AP_GATEWAY_IP="${ODS_AP_GATEWAY_IP:-192.168.7.1}"
# ODS_AP_PREFIX is CIDR prefix length used by `ip addr add`.
# ODS_AP_NETMASK stays accepted (as dotted-decimal) for back-compat;
# bring_up_interface converts it to a prefix when ODS_AP_PREFIX is
# unset. PREFIX is left empty here on purpose so an operator who only
# sets ODS_AP_NETMASK in /etc/ods/ap-mode.conf gets the prefix
# derived from THEIR netmask rather than the script default short-
# circuiting them to /24.
ODS_AP_PREFIX="${ODS_AP_PREFIX:-}"
ODS_AP_NETMASK="${ODS_AP_NETMASK:-255.255.255.0}"
ODS_AP_DHCP_RANGE="${ODS_AP_DHCP_RANGE:-192.168.7.10,192.168.7.50,1h}"
ODS_AP_CHANNEL="${ODS_AP_CHANNEL:-6}"

# Load operator overrides if present. Sourced — be deliberate about what
# you put in there.
if [[ -f "${CONF_DIR}/ap-mode.conf" ]]; then
  # shellcheck disable=SC1091
  source "${CONF_DIR}/ap-mode.conf"
fi

log() { printf '[ap-mode] %s\n' "$*" >&2; }
err() { printf '[ap-mode] ERROR: %s\n' "$*" >&2; }

# Convert a dotted-decimal netmask (255.255.255.0) to a CIDR prefix
# length (24). `ip addr add` requires the prefix form. If conversion
# fails we exit with an error rather than guessing.
_netmask_to_prefix() {
  local mask="$1"
  local count=0 octet bits seen_zero=0
  local -a octets
  IFS='.' read -r -a octets <<< "$mask"
  if (( ${#octets[@]} != 4 )); then
    err "invalid netmask '$mask' (expected four octets)"
    return 1
  fi
  for octet in "${octets[@]}"; do
    case "$octet" in
      255) bits=8 ;;
      254) bits=7 ;;
      252) bits=6 ;;
      248) bits=5 ;;
      240) bits=4 ;;
      224) bits=3 ;;
      192) bits=2 ;;
      128) bits=1 ;;
      0)   bits=0 ;;
      *)   err "invalid netmask octet '$octet' in '$mask'"; return 1 ;;
    esac
    if (( seen_zero && bits > 0 )); then
      err "invalid non-contiguous netmask '$mask'"
      return 1
    fi
    if (( bits < 8 )); then
      seen_zero=1
    fi
    count=$((count + bits))
  done
  printf '%s' "$count"
}

# --- Preflight ----------------------------------------------------------------

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    err "must run as root (currently $(id -un))"
    return 1
  fi
}

require_linux() {
  if [[ "$(uname -s)" != "Linux" ]]; then
    err "AP mode only supported on Linux (this is $(uname -s))"
    return 1
  fi
}

require_binaries() {
  local missing=()
  for bin in hostapd dnsmasq iptables ip nmcli; do
    if ! command -v "$bin" >/dev/null 2>&1; then
      missing+=("$bin")
    fi
  done
  if (( ${#missing[@]} > 0 )); then
    err "missing required binaries: ${missing[*]}"
    err "install with: apt install hostapd dnsmasq iptables network-manager"
    return 1
  fi
}

require_password() {
  # Open APs are tolerated but called out — for first-boot AP we
  # *strongly* recommend setting a per-device password so the unit
  # doesn't accept random clients during the wizard window.
  if [[ -z "${ODS_AP_PASSWORD}" ]]; then
    log "WARNING: ODS_AP_PASSWORD unset — bringing up an open AP."
    log "  Set ODS_AP_PASSWORD in ${CONF_DIR}/ap-mode.conf to require auth."
  elif [[ "${ODS_AP_PASSWORD,,}" == "changeme-set-per-device" ]]; then
    err "ODS_AP_PASSWORD still has the example placeholder value"
    err "set a unique per-device AP password in ${CONF_DIR}/ap-mode.conf"
    return 1
  elif [[ ${#ODS_AP_PASSWORD} -lt 8 ]]; then
    err "ODS_AP_PASSWORD must be at least 8 characters (WPA2 minimum)"
    return 1
  fi
}

interface_supports_ap() {
  # Best-effort check via iw. Not all drivers report capabilities cleanly;
  # we warn rather than fail-hard if iw isn't available.
  if ! command -v iw >/dev/null 2>&1; then
    log "WARNING: 'iw' not available — skipping AP-capability check"
    return 0
  fi
  if ! iw list 2>/dev/null | grep -A20 "Supported interface modes" | grep -q "\* AP"; then
    err "interface does not advertise AP mode in 'iw list' output"
    err "this driver may not support hostapd"
    return 1
  fi
}

# --- Bring up -----------------------------------------------------------------

write_hostapd_conf() {
  cat > "${HOSTAPD_CONF}" <<HEREDOC
# Auto-generated by ap-mode.sh — do not edit by hand.
interface=${ODS_AP_INTERFACE}
driver=nl80211
ssid=${ODS_AP_SSID}
hw_mode=g
channel=${ODS_AP_CHANNEL}
auth_algs=1
wmm_enabled=1
HEREDOC

  if [[ -n "${ODS_AP_PASSWORD}" ]]; then
    cat >> "${HOSTAPD_CONF}" <<HEREDOC
wpa=2
wpa_key_mgmt=WPA-PSK
wpa_pairwise=CCMP
rsn_pairwise=CCMP
wpa_passphrase=${ODS_AP_PASSWORD}
HEREDOC
  fi
  chmod 0600 "${HOSTAPD_CONF}"
}

write_dnsmasq_conf() {
  # Listen only on the AP interface. address=/#/<gateway> resolves every
  # hostname to the gateway IP — the classic captive-portal trick, no
  # separate DNS responder needed.
  cat > "${DNSMASQ_CONF}" <<HEREDOC
# Auto-generated by ap-mode.sh — do not edit by hand.
interface=${ODS_AP_INTERFACE}
bind-interfaces
except-interface=lo
listen-address=${ODS_AP_GATEWAY_IP}
dhcp-range=${ODS_AP_DHCP_RANGE}
dhcp-option=3,${ODS_AP_GATEWAY_IP}
dhcp-option=6,${ODS_AP_GATEWAY_IP}
# Captive-portal DNS: every name resolves to the device's setup IP.
address=/#/${ODS_AP_GATEWAY_IP}
# Don't read /etc/resolv.conf upstream — we ARE the resolver.
no-resolv
no-hosts
log-facility=${RUN_DIR}/dnsmasq.log
HEREDOC
  chmod 0600 "${DNSMASQ_CONF}"
}

install_iptables_rules() {
  # Redirect HTTP / HTTPS originating on the AP interface to the gateway.
  # This + the wildcard DNS = captive portal. We tag rules with a ODS
  # comment so teardown can remove only what we added.
  #
  # Idempotency: `iptables -A` always appends, even if an identical rule
  # already exists. Re-running `ap-mode.sh up` after a partial teardown
  # would otherwise stack duplicate PREROUTING rules. Use `-C` to check
  # first so each `up` adds at most one of each rule.
  if ! iptables -t nat -C PREROUTING -i "${ODS_AP_INTERFACE}" -p tcp --dport 80 \
      -j DNAT --to-destination "${ODS_AP_GATEWAY_IP}:80" -m comment --comment "ods-ap-mode" 2>/dev/null; then
    iptables -t nat -A PREROUTING -i "${ODS_AP_INTERFACE}" -p tcp --dport 80 \
      -j DNAT --to-destination "${ODS_AP_GATEWAY_IP}:80" -m comment --comment "ods-ap-mode"
  fi
  if ! iptables -t nat -C PREROUTING -i "${ODS_AP_INTERFACE}" -p tcp --dport 443 \
      -j DNAT --to-destination "${ODS_AP_GATEWAY_IP}:443" -m comment --comment "ods-ap-mode" 2>/dev/null; then
    iptables -t nat -A PREROUTING -i "${ODS_AP_INTERFACE}" -p tcp --dport 443 \
      -j DNAT --to-destination "${ODS_AP_GATEWAY_IP}:443" -m comment --comment "ods-ap-mode"
  fi
}

remove_iptables_rules() {
  # Drop any PREROUTING rule tagged with our comment. Loop in case there
  # are multiple (e.g. a previous up didn't fully tear down).
  while iptables -t nat -C PREROUTING -i "${ODS_AP_INTERFACE}" -p tcp --dport 80 \
      -j DNAT --to-destination "${ODS_AP_GATEWAY_IP}:80" -m comment --comment "ods-ap-mode" 2>/dev/null; do
    iptables -t nat -D PREROUTING -i "${ODS_AP_INTERFACE}" -p tcp --dport 80 \
      -j DNAT --to-destination "${ODS_AP_GATEWAY_IP}:80" -m comment --comment "ods-ap-mode" || true
  done
  while iptables -t nat -C PREROUTING -i "${ODS_AP_INTERFACE}" -p tcp --dport 443 \
      -j DNAT --to-destination "${ODS_AP_GATEWAY_IP}:443" -m comment --comment "ods-ap-mode" 2>/dev/null; do
    iptables -t nat -D PREROUTING -i "${ODS_AP_INTERFACE}" -p tcp --dport 443 \
      -j DNAT --to-destination "${ODS_AP_GATEWAY_IP}:443" -m comment --comment "ods-ap-mode" || true
  done
}

release_interface_from_nm() {
  # Tell NetworkManager to stop managing the AP interface. Otherwise it
  # fights hostapd over wlan0. nmcli returns non-zero if NM doesn't
  # currently manage the iface — that's fine.
  nmcli device set "${ODS_AP_INTERFACE}" managed no 2>/dev/null || true
}

reclaim_interface_for_nm() {
  nmcli device set "${ODS_AP_INTERFACE}" managed yes 2>/dev/null || true
}

bring_up_interface() {
  # `ip addr add` wants a CIDR prefix length (e.g. 192.168.7.1/24), NOT
  # a dotted-decimal netmask (192.168.7.1/255.255.255.0). The dotted form
  # silently fails on most modern iproute2 builds. We use ODS_AP_PREFIX
  # when set; otherwise we convert ODS_AP_NETMASK to a prefix at
  # bring-up time so back-compat with operator configs that set NETMASK
  # is preserved.
  local prefix="${ODS_AP_PREFIX}"
  if [[ -z "${prefix}" ]]; then
    prefix="$(_netmask_to_prefix "${ODS_AP_NETMASK}")" || return 1
  fi
  ip link set dev "${ODS_AP_INTERFACE}" up
  ip addr flush dev "${ODS_AP_INTERFACE}"
  ip addr add "${ODS_AP_GATEWAY_IP}/${prefix}" dev "${ODS_AP_INTERFACE}"
}

write_state() {
  local status="$1"
  cat > "${STATE_FILE}" <<HEREDOC
{
  "status": "${status}",
  "ssid": "${ODS_AP_SSID}",
  "interface": "${ODS_AP_INTERFACE}",
  "gateway_ip": "${ODS_AP_GATEWAY_IP}",
  "since": "$(date -Iseconds)"
}
HEREDOC
  chmod 0644 "${STATE_FILE}"
}

cmd_up() {
  require_linux
  require_root
  require_binaries
  require_password
  interface_supports_ap

  mkdir -p "${RUN_DIR}"

  # Snapshot the original state so we can recover on teardown.
  log "bringing up AP on ${ODS_AP_INTERFACE} as '${ODS_AP_SSID}'"
  release_interface_from_nm
  bring_up_interface

  write_hostapd_conf
  write_dnsmasq_conf
  install_iptables_rules

  # Start daemons directly and track them with PID files so teardown can
  # cleanly stop only the processes started for AP mode.
  if ! pgrep -f "hostapd .*${HOSTAPD_CONF}" >/dev/null; then
    hostapd -B -P "${HOSTAPD_PID}" "${HOSTAPD_CONF}" \
      || { err "hostapd failed to start — check journalctl"; cmd_down; return 1; }
  fi
  if ! pgrep -f "dnsmasq.*${DNSMASQ_CONF}" >/dev/null; then
    dnsmasq -C "${DNSMASQ_CONF}" --pid-file="${DNSMASQ_PID}" \
      || { err "dnsmasq failed to start — check ${RUN_DIR}/dnsmasq.log"; cmd_down; return 1; }
  fi

  write_state "active"
  log "AP up: SSID=${ODS_AP_SSID} gateway=${ODS_AP_GATEWAY_IP}"
  log "  any hostname resolves to ${ODS_AP_GATEWAY_IP} (captive portal)"
  log "  HTTP/HTTPS on ${ODS_AP_INTERFACE} redirected to the gateway"
}

cmd_down() {
  # Idempotent. Never errors out — best-effort cleanup so we don't leave
  # the system in a half-configured state.
  require_linux
  require_root

  log "tearing down AP on ${ODS_AP_INTERFACE}"

  if [[ -f "${HOSTAPD_PID}" ]]; then
    kill "$(cat "${HOSTAPD_PID}")" 2>/dev/null || true
    rm -f "${HOSTAPD_PID}"
  fi
  pkill -f "hostapd .*${HOSTAPD_CONF}" 2>/dev/null || true

  if [[ -f "${DNSMASQ_PID}" ]]; then
    kill "$(cat "${DNSMASQ_PID}")" 2>/dev/null || true
    rm -f "${DNSMASQ_PID}"
  fi
  pkill -f "dnsmasq.*${DNSMASQ_CONF}" 2>/dev/null || true

  remove_iptables_rules
  ip addr flush dev "${ODS_AP_INTERFACE}" 2>/dev/null || true
  reclaim_interface_for_nm

  rm -f "${STATE_FILE}" "${HOSTAPD_CONF}" "${DNSMASQ_CONF}"
  log "AP down; ${ODS_AP_INTERFACE} returned to NetworkManager"
}

cmd_status() {
  # No root required — purely read-only. Outputs JSON for the host-agent
  # to forward to the dashboard / wizard.
  if [[ -f "${STATE_FILE}" ]]; then
    cat "${STATE_FILE}"
  else
    printf '{"status":"inactive"}\n'
  fi
}

main() {
  case "${1:-status}" in
    up|start)       cmd_up ;;
    down|stop)      cmd_down ;;
    status)         cmd_status ;;
    -h|--help|help)
      cat <<HEREDOC
Usage: ap-mode.sh {up|down|status}

  up      Bring up the AP. DESTRUCTIVE — takes ${ODS_AP_INTERFACE}
          off NetworkManager and applies iptables NAT rules.
  down    Tear down the AP and restore the prior state. Idempotent.
  status  Print JSON status (read-only).

Config is loaded from ${CONF_DIR}/ap-mode.conf if present.
See docs/AP-MODE.md for the full setting list.
HEREDOC
      ;;
    *)
      err "unknown command: $1 (try: up | down | status)"
      exit 2
      ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
