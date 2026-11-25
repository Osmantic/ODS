#!/usr/bin/env bash
# Live check on a running ODS installation (GHSA-4rpc-g4mc-jm9c): only LiteLLM
# and dashboard-api can reach the remote-provider egress and its SSH tunnel,
# and the egress refuses callers that do not present the LiteLLM gateway key.
# It needs no provider credentials and changes nothing: it inspects networks,
# runs one throwaway container on ods-network and makes read-only requests.
#
#   bash tests/test-remote-provider-isolation-live.sh [install-dir]
set -euo pipefail

INSTALL_DIR="${1:-${ODS_INSTALL_DIR:-$HOME/ods}}"
# The Alpine image ODS already pins for its preflight probes.
PROBE_IMAGE="${ODS_ISOLATION_PROBE_IMAGE:-alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6}"
OUTBOUND_HOST="${ODS_ISOLATION_OUTBOUND_HOST:-github.com}"
EGRESS=ods-remote-provider-egress
TUNNEL=ods-remote-provider-ssh-tunnel
DASHBOARD_API=ods-dashboard-api
LITELLM=ods-litellm

failures=0
pass() { printf 'PASS %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); }
check() {
    local label="$1"
    shift
    if "$@"; then pass "$label"; else fail "$label"; fi
}

members() {
    docker network inspect "$1" --format '{{range .Containers}}{{.Name}}{{"\n"}}{{end}}' | sed '/^$/d' | sort
}
running() {
    [[ "$(docker inspect --format '{{.State.Running}}' "$1" 2>/dev/null)" == true ]]
}
on_network() {
    docker inspect --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}' "$1" \
        | grep -qx "$2"
}
not_on_network() { ! on_network "$1" "$2"; }
# A container on ods-network, as every extension is.
unreachable_from_ods_network() {
    ! docker run --rm --network ods-network --cap-drop ALL --security-opt no-new-privileges \
        "$PROBE_IMAGE" wget -q -T 5 -O /dev/null "$1" >/dev/null 2>&1
}
listed() { [[ -n "$1" ]] && grep -qw -- "$1" <<<"$2"; }

[[ -f "$INSTALL_DIR/.env" ]] || { printf 'No ODS installation at %s\n' "$INSTALL_DIR" >&2; exit 2; }
gateway_key="$(sed -n 's/^LITELLM_KEY=//p' "$INSTALL_DIR/.env" | tail -1 | tr -d '"'"'")"
[[ -n "$gateway_key" ]] || { printf 'LITELLM_KEY is missing from %s/.env\n' "$INSTALL_DIR" >&2; exit 2; }

# 1. Network shape.
check "ods-remote-provider is internal" \
    test "$(docker network inspect ods-remote-provider --format '{{.Internal}}')" = true
check "ods-remote-provider-outbound is not internal" \
    test "$(docker network inspect ods-remote-provider-outbound --format '{{.Internal}}')" = false
shared="$(members ods-remote-provider)"
for required in "$EGRESS" "$TUNNEL" "$DASHBOARD_API"; do
    check "$required joins ods-remote-provider" grep -qx "$required" <<<"$shared"
done
unexpected="$(grep -vx -e "$EGRESS" -e "$TUNNEL" -e "$DASHBOARD_API" -e "$LITELLM" <<<"$shared" || true)"
check "nothing else joins ods-remote-provider${unexpected:+ (found: ${unexpected//$'\n'/ })}" test -z "$unexpected"
check "only the egress and the tunnel join ods-remote-provider-outbound" \
    test "$(members ods-remote-provider-outbound | tr '\n' ' ')" = "$EGRESS $TUNNEL "
for service in "$EGRESS" "$TUNNEL"; do
    check "$service is not on ods-network" not_on_network "$service" ods-network
done

# 2. A container on ods-network, as any extension is, cannot reach either one.
check "an ods-network container cannot reach the egress" \
    unreachable_from_ods_network http://remote-provider-egress:8091/health
check "an ods-network container cannot reach the SSH tunnel" \
    unreachable_from_ods_network http://remote-provider-ssh-tunnel:18090/health

# 3. dashboard-api reaches the egress, and the egress enforces the gateway key.
status_from_dashboard_api() {
    # The key arrives on stdin so it never appears in a process list.
    printf '%s\n' "${2:-}" | docker exec -i "$DASHBOARD_API" sh -c '
        read -r key
        set -- -s -o /dev/null -w "%{http_code}" -m 10
        if [ -n "$key" ]; then set -- "$@" -H "Authorization: Bearer $key"; fi
        case "$0" in
            health) exec curl "$@" http://remote-provider-egress:8091/health ;;
            *) exec curl "$@" -X POST -H "Content-Type: application/json" -d "{}" \
                   http://remote-provider-egress:8091/v1/chat/completions ;;
        esac' "$1"
}
check "dashboard-api reads egress health" test "$(status_from_dashboard_api health)" = 200
check "the egress refuses a request without the gateway key (401)" \
    test "$(status_from_dashboard_api forward)" = 401
check "the egress refuses a wrong key (401)" \
    test "$(status_from_dashboard_api forward not-the-gateway-key)" = 401
with_key="$(status_from_dashboard_api forward "$gateway_key")"
check "the gateway key passes caller authentication (HTTP $with_key, not 401)" \
    test "$with_key" != 401 -a "$with_key" != 000

# 4. dashboard-api keeps ods-network as its default route (it finds the host
#    agent through that gateway), and the egress still reaches the internet.
ods_gateway="$(docker network inspect ods-network --format '{{range .IPAM.Config}}{{.Gateway}} {{end}}')"
default_gateway="$(docker exec "$DASHBOARD_API" python3 -c '
import socket, struct
for line in open("/proc/net/route").read().splitlines()[1:]:
    fields = line.split()
    if fields[1] == "00000000":
        print(socket.inet_ntoa(struct.pack("<L", int(fields[2], 16))))
')"
check "dashboard-api's default route is ods-network (${default_gateway:-none})" \
    listed "$default_gateway" "$ods_gateway"
check "the egress reaches $OUTBOUND_HOST:443 over its outbound network" \
    docker exec "$EGRESS" python -c "import socket; socket.create_connection(('$OUTBOUND_HOST', 443), 10).close()"

# 5. LiteLLM, when installed, reaches the egress.
if running "$LITELLM"; then
    check "LiteLLM reaches egress health" docker exec "$LITELLM" python3 -c \
        "import urllib.request; urllib.request.urlopen('http://remote-provider-egress:8091/health', timeout=10)"
    check "LiteLLM is still on ods-network" on_network "$LITELLM" ods-network
else
    printf 'SKIP LiteLLM is not running\n'
fi

printf '%d check(s) failed\n' "$failures"
exit $((failures > 0))
