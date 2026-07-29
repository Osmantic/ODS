#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

BOLD=""
NC=""
CYAN=""
GREEN=""

source "$PROJECT_DIR/lib/qrcode.sh"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

requested_url="http://example.test:1234/custom"
output=$(print_dashboard_qr "$requested_url")

grep -Fq "$requested_url" <<< "$output" \
    || fail "print_dashboard_qr did not include the explicit URL"

if grep -Eq 'http://[^[:space:]]+:3001' <<< "$output"; then
    fail "print_dashboard_qr replaced the explicit URL with a generated dashboard URL"
fi

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

# ── `ip route get` shapes ──────────────────────────────────────────────────
# The source address must be read from the `src` field by name. A route with
# no gateway omits `via <gw>`, which shifts every later column left by two.

route_shim() {
    # route_shim <route line>
    cat > "$tmpdir/ip" <<SH
#!/usr/bin/env bash
echo "$1"
SH
    chmod +x "$tmpdir/ip"
}

route_shim "1.1.1.1 via 192.168.1.1 dev eth0 src 192.168.1.50 uid 1000"
lan_ip=$(PATH="$tmpdir:$PATH" _ods_lan_ip)
[[ "$lan_ip" == "192.168.1.50" ]] \
    || fail "_ods_lan_ip misread a gateway route: got '$lan_ip'"

route_shim "1.1.1.1 dev eth0 src 172.28.112.1 uid 1000"
lan_ip=$(PATH="$tmpdir:$PATH" _ods_lan_ip)
[[ "$lan_ip" == "172.28.112.1" ]] \
    || fail "_ods_lan_ip misread an on-link (gateway-less) route: got '$lan_ip'"

route_shim "1.1.1.1 dev tun0 src 10.8.0.6 uid 1000"
default_output=$(PATH="$tmpdir:$PATH" print_dashboard_qr)
grep -Fq "http://10.8.0.6:3001" <<< "$default_output" \
    || fail "print_dashboard_qr did not use the on-link route's source address"

cat > "$tmpdir/ip" <<'SH'
#!/usr/bin/env bash
exit 1
SH

cat > "$tmpdir/ifconfig" <<'SH'
#!/usr/bin/env bash
cat <<'OUT'
lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
    inet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
    inet 10.42.0.99 netmask 0xffffff00 broadcast 10.42.0.255
OUT
SH

chmod +x "$tmpdir/ip" "$tmpdir/ifconfig"

lan_ip=$(PATH="$tmpdir:$PATH" _ods_lan_ip)
[[ "$lan_ip" == "10.42.0.99" ]] \
    || fail "_ods_lan_ip did not parse the macOS ifconfig fallback"

default_output=$(PATH="$tmpdir:$PATH" print_dashboard_qr)
grep -Fq "http://10.42.0.99:3001" <<< "$default_output" \
    || fail "print_dashboard_qr did not use the discovered LAN IP by default"

echo "[PASS] qrcode helper honors explicit URLs and macOS LAN detection"
