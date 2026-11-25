#!/usr/bin/env bash
# The requirements check (phase 04) names who holds a busy port and, for the
# required Dashboard ports, names every conflict at once with a free port to
# rerun with:
#   - a process this account can see: its name and PID;
#   - a Docker container's published TCP port: the container's name, for
#     IPv4, IPv6, loopback and port-range bindings, also when only
#     docker-proxy, a netstat line or a Windows listener is visible; never a
#     container whose binding only ends in the same digits or is UDP;
#   - a listener of another user or root: says so, with the command among
#     this host's tools (ss, lsof, netstat) that names it;
#   - both Dashboard ports busy: both named, and a rerun example with a free
#     port for each that is not another ODS service's port, below 65536.
# Runs the real phase 04 functions with fake lsof, ss, netstat, ps, sudo and
# docker.
# shellcheck disable=SC2034,SC2329  # variables and stubs read by the sourced phase
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE04="$ROOT_DIR/installers/phases/04-requirements.sh"
PASS=0
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
pass() { printf '[PASS] %s\n' "$*"; PASS=$((PASS + 1)); }

# The port functions, up to the line that runs the Dashboard check, and the
# loop that reports the optional services' ports.
functions="$(sed -n '/^_port_check_warned=false$/,/^# Fail before \.env regeneration/p' "$PHASE04")"
[[ "$functions" == *'_phase04_check_dashboard_ports() {'* ]] || fail "phase 04's port functions were not found"
optional_loop="$(sed -n '/^for port in \$PORTS_TO_CHECK; do$/,/^done$/p' "$PHASE04")"
[[ "$optional_loop" == *'is in use by'* ]] || fail "phase 04's optional port loop was not found"

# probe [VAR=value...] -- COMMAND...: run COMMAND with the port functions and
# fakes. FAKE_LSOF (PORT:PID), FAKE_SS (ports ss lists), FAKE_SS_USERS (its
# users column), FAKE_NETSTAT (ports netstat lists), FAKE_DOCKER_PS
# (NAME|PORTS rows) and FAKE_WINDOWS (ports a Windows program holds)
# describe the host.
probe() (
    while [[ "$1" != -- ]]; do export "${1?}"; shift; done
    shift
    warn() { printf 'WARN %s\n' "$*"; }
    log() { :; }
    external_llm_env_value() { return 1; }
    lsof() {
        [[ -n "${FAKE_LSOF:-}" ]] || return 1
        local port="${FAKE_LSOF%%:*}" pid="${FAKE_LSOF#*:}"
        [[ " $* " == *" :$port "* ]] || return 1
        [[ "$1" != -t ]] || printf '%s\n' "$pid"
    }
    ss() {
        local port
        for port in ${FAKE_SS:-}; do
            printf 'LISTEN 0 511 0.0.0.0:%s 0.0.0.0:*%s\n' "$port" "${FAKE_SS_USERS:+ $FAKE_SS_USERS}"
        done
    }
    netstat() {
        local port
        for port in ${FAKE_NETSTAT:-}; do
            printf 'tcp 0 0 0.0.0.0:%s 0.0.0.0:* LISTEN -\n' "$port"
        done
    }
    ps() { case "$2" in 4242) echo node ;; 777) echo docker-proxy ;; *) return 1 ;; esac; }
    sudo() { return 1; }
    docker() {
        case "$1 ${2:-}" in
            'ps --format') printf '%b' "${FAKE_DOCKER_PS:-}" ;;
            'ps --filter') ;;
            'container inspect') return 1 ;;
            *) echo "unexpected docker $*" >&2; return 88 ;;
        esac
    }
    ods_windows_host_port_in_use() { [[ " ${FAKE_WINDOWS:-} " == *" $1 "* ]]; }
    declare -A SERVICE_PORTS=([open-webui]=3000 [dashboard-api]=3002 [searxng]=8888)
    [[ -z "${EXTRA_SERVICE_PORT:-}" ]] || SERVICE_PORTS[extra]="$EXTRA_SERVICE_PORT"
    INSTALL_DIR=/nonexistent-ods-install
    eval "$functions"
    "$@"
)

owner() { probe "$@" -- eval 'check_port_conflict "$PORT" false && _phase04_port_owner "$PORT"'; }
unseen="a program this account cannot see (sudo ss -ltnp 'sport = :3011' names it)"

[[ "$(owner PORT=3011 FAKE_LSOF=3011:4242)" == 'node (PID 4242)' ]] || fail "a visible process must be named with its PID"
[[ "$(owner PORT=3011 FAKE_SS=3011)" == "$unseen" ]] || fail "an unseen listener must say so: $(owner PORT=3011 FAKE_SS=3011)"
[[ "$(owner PORT=3011 FAKE_LSOF=3011:5555)" == 'PID 5555' ]] || fail "a process that has exited keeps its PID"
[[ "$(owner PORT=3011 FAKE_WINDOWS=3011)" == 'Windows host process' ]] || fail "a Windows listener keeps its name"
[[ "$(owner PORT=3011 FAKE_NETSTAT=3011)" == "$unseen" ]] || fail "a netstat listener without a program is unseen"
# The finder named for an unseen owner is one this host has.
finder() (
    eval "$functions"
    PORT_CONFLICT_PROC="" PORT_CONFLICT_PID=""
    local tool
    for tool in "$@"; do eval "$tool() { :; }"; done
    PATH=/nonexistent-ods-bin _phase04_port_owner 3011
)
[[ "$(finder ss lsof)" == "$unseen" \
    && "$(finder lsof)" == 'a program this account cannot see (sudo lsof -i :3011 -sTCP:LISTEN names it)' \
    && "$(finder)" == 'a program this account cannot see (sudo netstat -tlnp | grep :3011 names it)' ]] \
    || fail "the finder must be a tool the host has: $(finder lsof) / $(finder)"
pass "a visible process is named with its PID; an unseen listener says so and how this host finds it"

container() { owner PORT=3011 FAKE_SS=3011 "FAKE_DOCKER_PS=$1"; }
for row in 'grafana|0.0.0.0:3011->3000/tcp, :::3011->3000/tcp' 'grafana|127.0.0.1:3011->3000/tcp' \
           'grafana|[::]:3011->3000/tcp' 'grafana|0.0.0.0:3000-3015->3000-3015/tcp'; do
    [[ "$(container "$row\n")" == 'Docker container grafana' ]] || fail "binding '$row' must name the container"
done
for row in 'other|127.0.0.1:13011->80/tcp' 'other|0.0.0.0:3011->3011/udp' 'other|3011/tcp' \
           'other|0.0.0.0:3012-3020->3012-3020/tcp' 'other|'; do
    [[ "$(container "$row\n")" == "$unseen" ]] || fail "binding '$row' must not name a container for 3011"
done
grafana='grafana|0.0.0.0:3011->3000/tcp\n'
[[ "$(owner PORT=3011 FAKE_SS=3011 'FAKE_SS_USERS=users:(("docker-proxy",pid=777,fd=4))' "FAKE_DOCKER_PS=$grafana")" \
        == 'Docker container grafana' ]] || fail "docker-proxy must give way to the container's name"
[[ "$(owner PORT=3011 FAKE_NETSTAT=3011 "FAKE_DOCKER_PS=$grafana")" == 'Docker container grafana' ]] \
    || fail "a netstat-only listener must be named by its container"
[[ "$(owner PORT=3011 FAKE_WINDOWS=3011 "FAKE_DOCKER_PS=$grafana")" == 'Docker container grafana' ]] \
    || fail "Docker Desktop's published port must be named by its container"
[[ "$(owner PORT=3011 FAKE_LSOF=3011:4242 "FAKE_DOCKER_PS=$grafana")" == 'node (PID 4242)' ]] \
    || fail "a named process must not be replaced by a container"
pass "a Docker container is named for its published TCP port and range, through docker-proxy, netstat or Windows"

[[ "$(probe FAKE_LSOF=3000:4242 PORTS_TO_CHECK=3000 REQUIREMENTS_MET=true -- eval "$optional_loop")" \
        == 'WARN Port 3000 is in use by node (PID 4242)' ]] || fail "an optional service's busy port must name its owner"
pass "an optional service's busy port names its owner"

dashboard() { probe "$@" -- _phase04_check_dashboard_ports; }
set +e
both="$(dashboard 'FAKE_SS=3001 3011')"; both_rc=$?
remote="$(dashboard FAKE_SS=3011 EXTRA_SERVICE_PORT=3021)"; remote_rc=$?
full="$(dashboard "FAKE_SS=3011 $(seq -s ' ' 3021 10 3101)")"; full_rc=$?
free="$(dashboard FAKE_SS=8080)"; free_rc=$?
top="$(probe 'FAKE_SS=65510 65520 65530' -- _phase04_free_port_hint 65500)"; top_rc=$?
set -e
[[ "$both_rc" == 1 && "$both" == "WARN Required Dashboard port 3001 (DASHBOARD_PORT) is in use by a program this account cannot see (sudo ss -ltnp 'sport = :3001' names it).
WARN Required Dashboard port 3011 (DASHBOARD_REMOTE_PORT) is in use by $unseen.
WARN Choose a free port for DASHBOARD_PORT and DASHBOARD_REMOTE_PORT in the environment or installed .env, then rerun the installer, for example with:
WARN   DASHBOARD_PORT=3021 DASHBOARD_REMOTE_PORT=3031" ]] \
    || fail "both busy Dashboard ports must be named with a distinct free port each (rc=$both_rc): $both"
[[ "$remote_rc" == 1 && "$remote" == *$'\nWARN   DASHBOARD_REMOTE_PORT=3031' && "$remote" != *'(DASHBOARD_PORT)'* ]] \
    || fail "the example must skip another ODS service's port (rc=$remote_rc): $remote"
[[ "$full_rc" == 1 && "$full" == *'then rerun the installer.' && "$full" != *'for example'* ]] \
    || fail "no example without a free nearby port (rc=$full_rc): $full"
[[ "$free_rc" == 0 && -z "$free" ]] || fail "free Dashboard ports must pass quietly (rc=$free_rc): $free"
[[ "$top_rc" == 1 && -z "$top" ]] || fail "an example port must stay below 65536 (rc=$top_rc): $top"
pass "busy Dashboard ports are all named at once, with a free port for each that is not another ODS service's"

printf '\nResult: %s passed, 0 failed\n' "$PASS"
