#!/usr/bin/env bash
# Exercise the real cmd_agent body with isolated files and deterministic mocks.
# No Docker, launchd/systemd, network, Python agent or process signal is invoked.
set -euo pipefail

if (( BASH_VERSINFO[0] < 4 )); then
    for candidate_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ ! -x "$candidate_bash" ]] || exec "$candidate_bash" "$0" "$@"
    done
    printf '[SKIP] Agent lifecycle tests require Bash 4+\n'
    exit 0
fi

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ods_dir="$(cd "$test_dir/.." && pwd)"
fixture_root="$(mktemp -d "${TMPDIR:-/tmp}/ods-agent-lifecycle.XXXXXX")"
trap 'rm -rf "$fixture_root"' EXIT

# Keep the production body intact, including its nested ownership/health helpers.
awk '/^cmd_agent\(\) \{/ {capture=1} capture {print} capture && /^}/ {exit}' \
    "$ods_dir/ods-cli" > "$fixture_root/cmd-agent.sh"
[[ -s "$fixture_root/cmd-agent.sh" ]] || { echo 'FAIL: cmd_agent extraction is empty' >&2; exit 1; }

cat > "$fixture_root/run-case.sh" <<'RUNNER'
#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR="$CASE_DIR/install with spaces"
ODS_AGENT_PORT=7710
ODS_AGENT_FORCE_SESSION=false
[[ "$TEST_PLATFORM" != session ]] || ODS_AGENT_FORCE_SESSION=true
export INSTALL_DIR ODS_AGENT_PORT ODS_AGENT_FORCE_SESSION
mkdir -p "$INSTALL_DIR/data" "$INSTALL_DIR/bin" "$HOME/Library/LaunchAgents"
printf '# fixture only; never executed\n' > "$INSTALL_DIR/bin/ods-host-agent.py"
printf 'running\n' > "$CASE_DIR/state"
printf '0\n' > "$CASE_DIR/probes"
: > "$CASE_DIR/commands"
unit_script="$INSTALL_DIR/bin/ods-host-agent.py"
unit_directory="$INSTALL_DIR"
unit_binary=/usr/bin/python3
unit_extra=""
if [[ "$TEST_PRIVATE_PYTHON" == true ]]; then
    unit_binary="$INSTALL_DIR/.venv/host-agent/bin/python"
fi
if [[ "$TEST_FOREIGN" == true ]]; then
    unit_script="$CASE_DIR/unrelated-install/bin/ods-host-agent.py"
    unit_directory="$CASE_DIR/unrelated-install"
fi
if [[ "$TEST_REDIRECT" == true ]]; then
    unit_extra="--install-dir=$CASE_DIR/unrelated-install"
fi
export unit_script unit_directory unit_binary unit_extra
cat > "$HOME/Library/LaunchAgents/com.ods.host-agent.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.ods.host-agent</string>
<key>ProgramArguments</key><array><string>${unit_binary}</string><string>${unit_script}</string>
$(if [[ -n "$unit_extra" ]]; then printf '<string>%s</string>' "$unit_extra"; fi)
</array>
<key>WorkingDirectory</key><string>${unit_directory}</string>
</dict></plist>
PLIST
if [[ "$TEST_STALE_PID" == true ]]; then
    printf '424242\n' > "$INSTALL_DIR/data/ods-host-agent.pid"
fi

record() { printf '%s\n' "$*" >> "$CASE_DIR/commands"; }
unexpected() { record "UNEXPECTED $*"; return 98; }
check_install() { :; }
load_env() { :; }
_agent_probe_host() { printf '127.0.0.1'; }
success() { printf 'SUCCESS: %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*"; }
log_error() { printf 'ERROR: %s\n' "$*"; }
sleep() { :; }
disown() { :; }
uname() {
    case "$TEST_PLATFORM" in launchd) printf 'Darwin\n';; *) printf 'Linux\n';; esac
}
sudo() {
    [[ "${1:-}" != -n ]] || shift
    [[ "${1:-}" == systemctl ]] || { unexpected "sudo $*"; return; }
    systemctl "${@:2}"
}
systemctl() {
    [[ "$TEST_PLATFORM" == systemd ]] || return 1
    record "systemctl $*"
    case "${1:-}" in
        cat)
            printf '[Service]\nExecStart=%s "%s" --require-ods-network %s\nWorkingDirectory=%s\n' "$unit_binary" "$unit_script" "$unit_extra" "$unit_directory"
            ;;
        show)
            case "$*" in
                *ExecStart*) printf '{ path=%s ; argv[]=%s %s --require-ods-network %s ; ignore_errors=no ; }\n' "$unit_binary" "$unit_binary" "$unit_script" "$unit_extra" ;;
                *WorkingDirectory*) printf '%s\n' "$unit_directory" ;;
                *MainPID*) printf '424244\n' ;;
                *) unexpected "systemctl $*" ;;
            esac
            ;;
        stop)
            [[ "$TEST_STOP_FAIL" != true ]] || return 7
            printf 'stopped\n' > "$CASE_DIR/state"
            ;;
        start)
            [[ "$TEST_START_FAIL" != true ]] || return 8
            printf 'running\n' > "$CASE_DIR/state"
            ;;
        *) unexpected "systemctl $*" ;;
    esac
}
launchctl() {
    record "launchctl $*"
    case "${1:-}" in
        print)
            [[ "$(< "$CASE_DIR/state")" == running ]] || return 1
            printf 'gui/1000/com.ods.host-agent = {\n path = %s\n program = %s\n arguments = {\n %s\n %s\n %s\n }\n working directory = %s\n pid = 424244\n}\n' \
                "$HOME/Library/LaunchAgents/com.ods.host-agent.plist" "$unit_binary" "$unit_binary" "$unit_script" "$unit_extra" "$unit_directory"
            ;;
        bootout)
            [[ "$TEST_STOP_FAIL" != true ]] || return 7
            printf 'stopped\n' > "$CASE_DIR/state"
            ;;
        bootstrap)
            [[ "$TEST_START_FAIL" != true ]] || return 8
            printf 'running\n' > "$CASE_DIR/state"
            ;;
        *) unexpected "launchctl $*" ;;
    esac
}
curl() {
    local probes
    probes=$(< "$CASE_DIR/probes")
    probes=$((probes + 1))
    printf '%s\n' "$probes" > "$CASE_DIR/probes"
    record 'curl health'
    case "$TEST_HEALTH" in
        always) ;;
        never) return 7 ;;
        after_session_start) (( probes >= 4 )) || return 7 ;;
        state) [[ "$(< "$CASE_DIR/state")" == running ]] || return 7 ;;
        *) unexpected "health mode $TEST_HEALTH"; return ;;
    esac
    printf '{"status":"ok","version":"fixture"}\n'
}
kill() {
    record "kill $*"
    # The stale fixture PID is absent. All signals are intercepted here.
    [[ "${1:-}" == -0 ]] || { unexpected "process signal $*"; return; }
    return 1
}
ps() { unexpected "ps $*"; }
nohup() {
    record "nohup $*"
    # A background shell executes only this mock, never the supplied command.
    [[ "$TEST_PLATFORM" == session && "$TEST_STALE_PID" == true ]] || unexpected 'nohup without session fixture'
}
source "$FUNCTION_FILE"
result=0
cmd_agent "$TEST_ACTION" || result=$?
# Join the harmless mocked nohup shell before examining its recorded call.
wait
printf 'RESULT=%s\n' "$result"
exit "$result"
RUNNER

passed=0
failed=0
run_case() {
    local name="$1" platform="$2" action="$3" expected="$4" health="$5"
    local start_fail="$6" stop_fail="$7" foreign="$8" stale_pid="$9"
    local redirect="${10:-false}" private_python="${11:-false}"
    local case_dir="$fixture_root/$name" rc=0 mismatch=""
    mkdir -p "$case_dir/home"
    # Only the child process sees this isolated home; the caller's HOME is untouched.
    env HOME="$case_dir/home" CASE_DIR="$case_dir" FUNCTION_FILE="$fixture_root/cmd-agent.sh" \
        TEST_PLATFORM="$platform" TEST_ACTION="$action" TEST_HEALTH="$health" \
        TEST_START_FAIL="$start_fail" TEST_STOP_FAIL="$stop_fail" \
        TEST_FOREIGN="$foreign" TEST_STALE_PID="$stale_pid" \
        TEST_REDIRECT="$redirect" TEST_PRIVATE_PYTHON="$private_python" \
        "$BASH" "$fixture_root/run-case.sh" > "$case_dir/output" 2>&1 || rc=$?
    if [[ "$expected" == success && "$rc" -ne 0 ]] || [[ "$expected" == failure && "$rc" -eq 0 ]]; then
        mismatch="expected $expected, got exit $rc"
    fi
    if grep -q '^UNEXPECTED ' "$case_dir/commands"; then
        mismatch="unexpected mocked command"
    fi
    if [[ "$foreign" == true || "$redirect" == true ]] && grep -Eq '^(systemctl (stop|start)|launchctl (bootout|bootstrap)) ' "$case_dir/commands"; then
        mismatch="foreign unit was mutated"
    fi
    if [[ "$name" == old_health_persists ]] && grep -Eq '^systemctl start ' "$case_dir/commands"; then
        mismatch="new start attempted while old health persists"
    fi
    if [[ "$name" == session_no_pid_healthy ]] && grep -Eq '^(nohup |kill )' "$case_dir/commands"; then
        mismatch="unowned responding session was signalled or replaced"
    fi
    if [[ "$stale_pid" == true ]]; then
        if [[ -e "$case_dir/install with spaces/data/ods-host-agent.pid" ]] || ! grep -q '^nohup ' "$case_dir/commands"; then
            mismatch="dead PID was not removed and recovered through the mocked start"
        fi
    fi
    if [[ "$name" == *_new_unhealthy || "$name" == *_healthy_restart ]]; then
        if ! grep -Eq '^(systemctl start |launchctl bootstrap )' "$case_dir/commands"; then
            mismatch="case did not reach managed start"
        fi
    fi
    if [[ -n "$mismatch" ]]; then
        printf 'FAIL %-30s %s\n' "$name" "$mismatch" >&2
        cat "$case_dir/output" "$case_dir/commands" >&2
        failed=$((failed + 1))
    else
        printf 'PASS %s\n' "$name"
        passed=$((passed + 1))
    fi
}

run_case systemd_start_failure systemd start failure never true false false false
run_case systemd_stop_failure systemd stop failure always false true false false
run_case launchd_start_failure launchd start failure never true false false false
run_case launchd_stop_failure launchd stop failure always false true false false
run_case unhealthy_status systemd status failure never false false false false
run_case foreign_systemd systemd restart failure state false false true false
run_case foreign_launchd launchd restart failure state false false true false
run_case old_health_persists systemd restart failure always false false false false
run_case session_no_pid_healthy session restart failure always false false false false
run_case dead_session_pid_recovers session restart success after_session_start false false false true
run_case systemd_new_unhealthy systemd restart failure never false false false false
run_case launchd_new_unhealthy launchd restart failure never false false false false
run_case systemd_healthy_restart systemd restart success state false false false false
run_case launchd_healthy_restart launchd restart success state false false false false
run_case launchd_private_venv_healthy_restart launchd restart success state false false false false false true
run_case systemd_redirected_install systemd restart failure state false false false false true
run_case launchd_redirected_install launchd restart failure state false false false false true

printf 'Agent lifecycle: %s passed, %s failed\n' "$passed" "$failed"
(( failed == 0 ))
