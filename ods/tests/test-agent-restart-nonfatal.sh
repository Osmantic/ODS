#!/usr/bin/env bash
# Exercise the real session-fallback function, with no host process/network use.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${ODS_CLI_UNDER_TEST:-$ROOT_DIR/ods-cli}"
PHASE_TARGET="${ODS_PHASE07_UNDER_TEST:-$ROOT_DIR/installers/phases/07-devtools.sh}"
FIXTURE="$(mktemp -d)"
trap 'rm -rf "$FIXTURE"' EXIT

for scenario in missing-python missing-script unhealthy; do
    mkdir -p "$FIXTURE/$scenario/data" "$FIXTURE/$scenario/bin"
    if [[ "$scenario" == unhealthy ]]; then
        touch "$FIXTURE/$scenario/bin/ods-host-agent.py"
    fi
    if ! output=$(
        INSTALL_DIR="$FIXTURE/$scenario"
        ODS_AGENT_FORCE_SESSION=true
        check_install() { :; }
        load_env() { :; }
        log() { printf '%s\n' "$*"; }
        log_error() { printf '%s\n' "$*" >&2; }
        error() { log_error "$*"; exit 1; }
        warn() { log_error "$*"; }
        success() { log "$*"; }
        _agent_probe_host() { echo 127.0.0.1; }
        curl() { return 1; }
        nohup() { return 1; }
        kill() { return 1; }
        sleep() { :; }
        command() {
            if [[ "$scenario" == missing-python && "$*" == '-v python3' ]]; then return 1; fi
            builtin command "$@"
        }
        source <(awk '/^cmd_agent\(\) \{/{copy=1} copy{print} copy && /^}$/{exit}' "$TARGET")
        cmd_agent restart || warn 'Host agent restart failed (non-fatal)'
        echo 'Update complete'
    ); then
        echo "[FAIL] $scenario exited the guarded update" >&2
        exit 1
    fi
    [[ "$output" == *'Update complete'* ]] || exit 1
    echo "[PASS] $scenario returns control to the guarded update"
done

# Exercise phase 07 through the real CLI lifecycle with simulated health/PIDs.
# No service, network request or host process is started, probed or signalled.
awk '/^cmd_agent\(\) \{/{copy=1} copy{print} copy && /^}$/{exit}' "$TARGET" > "$FIXTURE/cmd-agent.sh"
awk '/^_ods_start_session_host_agent\(\) \{/{copy=1} copy{print} copy && /^}$/{exit}' \
    "$PHASE_TARGET" > "$FIXTURE/start-session.sh"
cat > "$FIXTURE/python-ready" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$FIXTURE/python-ready"
cat > "$FIXTURE/fixture-cli" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf 'invoke:%s force:%s bind:%s port:%s\n' "$*" "${ODS_AGENT_FORCE_SESSION:-}" \
    "$ODS_AGENT_BIND" "$ODS_AGENT_PORT" >> "$TEST_AGENT_TRACE"
[[ "$TEST_AGENT_CASE" != cancelled ]] || exit 130
check_install() { :; }
load_env() { :; }
success() { printf '%s\n' "$*"; }
warn() { printf '%s\n' "$*" >&2; }
log_error() { warn "$*"; }
_agent_probe_host() { printf '%s\n' "$ODS_AGENT_BIND"; }
curl() {
    printf 'probe:%s\n' "$*" >> "$TEST_AGENT_TRACE"
    [[ "$TEST_AGENT_CASE" != unhealthy && "$(cat "$TEST_AGENT_STATE")" != stopped ]] || return 1
    printf '{"status":"ok","version":"fixture"}\n'
}
ps() { printf 'python3 %s/bin/ods-host-agent.py\n' "$INSTALL_DIR"; }
kill() {
    [[ "$1" != -0 ]] || return 0
    printf 'signal:%s\n' "$*" >> "$TEST_AGENT_TRACE"
    printf stopped > "$TEST_AGENT_STATE"
}
nohup() {
    printf 'launch:%s\n' "$*" >> "$TEST_AGENT_TRACE"
    printf new > "$TEST_AGENT_STATE"
}
sleep() {
    printf 'sleep:%s\n' "$*" >> "$TEST_AGENT_TRACE"
    # Let the background shell stub write its receipt; never use product waits.
    /bin/sleep 0.01
}
source "$TEST_AGENT_FUNCTION"
[[ "$1" == agent ]] && shift
cmd_agent "$@"
SH
chmod +x "$FIXTURE/fixture-cli"

for scenario in old-healthy fresh unhealthy cancelled; do
    install="$FIXTURE/installer-$scenario"
    mkdir -p "$install/bin" "$install/data"
    touch "$install/bin/ods-host-agent.py"
    cp "$FIXTURE/fixture-cli" "$install/ods-cli"
    printf stopped > "$install/state"
    : > "$install/trace"
    if [[ "$scenario" == old-healthy ]]; then
        printf old > "$install/state"
        printf '424242\n' > "$install/data/ods-host-agent.pid"
    fi
    result=0
    (
        export INSTALL_DIR="$install" TEST_AGENT_CASE="$scenario"
        export TEST_AGENT_STATE="$install/state" TEST_AGENT_TRACE="$install/trace"
        export TEST_AGENT_FUNCTION="$FIXTURE/cmd-agent.sh"
        export ODS_AGENT_BIND=127.31.4.9 ODS_AGENT_PORT=17710
        AGENT_PYTHON="$FIXTURE/python-ready"
        LOG_FILE="$install/agent.log"
        ai() { printf '%s\n' "$*"; }
        ai_ok() { printf 'ready:%s\n' "$*"; }
        ai_warn() { printf 'warning:%s\n' "$*"; }
        source "$FIXTURE/start-session.sh"
        _ods_start_session_host_agent
    ) > "$install/output" 2>&1 || result=$?
    if [[ "$scenario" == old-healthy || "$scenario" == fresh ]]; then
        if [[ "$result" != 0 || "$(cat "$install/state")" != new ]]; then
            echo "[FAIL] phase 07 $scenario did not load a new healthy session agent" >&2
            exit 1
        fi
        grep -q '^ready:' "$install/output"
        [[ "$(grep -c '^launch:' "$install/trace")" == 1 ]]
        grep -q 'http://127.31.4.9:17710/health' "$install/trace"
        if [[ "$scenario" == old-healthy ]]; then
            grep -qx 'signal:424242' "$install/trace"
        else
            ! grep -q '^signal:' "$install/trace"
        fi
    else
        [[ "$result" == 1 ]]
        ! grep -q '^ready:' "$install/output"
        if [[ "$scenario" == unhealthy ]]; then
            [[ "$(grep -c '^sleep:0.5$' "$install/trace")" == 20 ]]
            [[ "$(grep -c '^launch:' "$install/trace")" == 1 ]]
        else
            ! grep -q '^launch:' "$install/trace"
        fi
    fi
    grep -qx 'invoke:agent restart force:true bind:127.31.4.9 port:17710' "$install/trace"
    [[ "$(grep -c '^invoke:' "$install/trace")" == 1 ]]
    echo "[PASS] phase 07 session $scenario preserves explicit bind and bounded lifecycle"
done
