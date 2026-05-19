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

lan_ip=$(PATH="$tmpdir:$PATH" _dream_lan_ip)
[[ "$lan_ip" == "10.42.0.99" ]] \
    || fail "_dream_lan_ip did not parse the macOS ifconfig fallback"

default_output=$(PATH="$tmpdir:$PATH" print_dashboard_qr)
grep -Fq "http://10.42.0.99:3001" <<< "$default_output" \
    || fail "print_dashboard_qr did not use the discovered LAN IP by default"

echo "[PASS] qrcode helper honors explicit URLs and macOS LAN detection"
