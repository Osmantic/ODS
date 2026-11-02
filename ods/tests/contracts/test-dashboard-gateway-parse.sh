#!/usr/bin/env bash
# Feeds fixture /proc/net/route tables to the dashboard entrypoint's gateway
# parser (the LOOPBACK_PEER block) and checks the trusted peer it derives.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENTRYPOINT="$ROOT/extensions/services/dashboard/entrypoint.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# The parser runs from "LOOPBACK_PEER=$(awk" to the esac that validates it.
sed -n '/^LOOPBACK_PEER=\$(awk/,/^esac$/p' "$ENTRYPOINT" > "$WORK/parse.sh"
grep -q 'ODS_NET_ROUTE_FILE' "$WORK/parse.sh"
printf 'printf "%%s" "$LOOPBACK_PEER"\n' >> "$WORK/parse.sh"

HEADER='Iface	Destination	Gateway 	Flags	RefCnt	Use	Metric	Mask		MTU	Window	IRTT'
fail=0
check() {
    local name="$1" expected="$2" table="$3" got
    printf '%s\n%s' "$HEADER" "$table" > "$WORK/route"
    got="$(ODS_NET_ROUTE_FILE="$WORK/route" sh "$WORK/parse.sh")"
    if [[ "$got" == "$expected" ]]; then
        echo "PASS: $name -> '${got}'"
    else
        echo "FAIL: $name: expected '$expected', got '$got'"
        fail=1
    fi
}

check "default route" "172.18.0.1" \
'eth0	00000000	010012AC	0003	0	0	0	00000000	0	0	0
eth0	000012AC	00000000	0001	0	0	0	0000FFFF	0	0	0'
check "no default route" "" \
'eth0	000012AC	00000000	0001	0	0	0	0000FFFF	0	0	0'
check "multiple routes, default last" "10.89.0.1" \
'eth0	0000590A	00000000	0001	0	0	0	00FFFFFF	0	0	0
eth1	000013AC	00000000	0001	0	0	0	0000FFFF	0	0	0
eth0	00000000	0100590A	0003	0	0	0	00000000	0	0	0
eth1	00000000	010013AC	0003	0	0	100	00000000	0	0	0'
check "default without gateway" "" \
'eth0	00000000	00000000	0001	0	0	0	00000000	0	0	0'
check "empty table" "" ''

exit "$fail"
