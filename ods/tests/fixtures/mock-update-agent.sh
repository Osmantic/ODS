#!/usr/bin/env bash
# Shared Linux lifecycle fixture. All service and network operations are mocked.
install_mock_update_agent() {
    local mock_bin="$1" mock_install="$2"
    export TEST_AGENT_INSTALL="$mock_install"
    export TEST_AGENT_REAL_UNAME
    TEST_AGENT_REAL_UNAME="$(command -v uname)"
    printf stopped > "$mock_install/.fixture-agent-state"
    cat > "$mock_bin/systemctl" <<'SH'
#!/usr/bin/env bash
case "$1" in
    cat) exit 0 ;;
    show)
        case "$*" in
            *WorkingDirectory*) printf '%s\n' "$TEST_AGENT_INSTALL" ;;
            *ExecStart*) printf '{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 %s/bin/ods-host-agent.py ; ignore_errors=no ; }\n' "$TEST_AGENT_INSTALL" ;;
            *) exit 1 ;;
        esac ;;
    stop) printf stopped > "$TEST_AGENT_INSTALL/.fixture-agent-state" ;;
    start)
        [[ "${TEST_HOST_AGENT_FAIL:-}" != true ]] || exit 1
        printf running > "$TEST_AGENT_INSTALL/.fixture-agent-state" ;;
    *) exit 1 ;;
esac
SH
    cat > "$mock_bin/sudo" <<'SH'
#!/usr/bin/env bash
[[ "$1" == systemctl ]] || exit 1
shift
exec systemctl "$@"
SH
    cat > "$mock_bin/curl" <<'SH'
#!/usr/bin/env bash
if [[ "$*" == *':7710/health'* && "$(cat "$TEST_AGENT_INSTALL/.fixture-agent-state")" == running ]]; then
    printf '{"status":"ok","version":"fixture"}\n'
    exit 0
fi
exit 7
SH
    cat > "$mock_bin/uname" <<'SH'
#!/usr/bin/env bash
# Select this fixture's simulated systemd platform even on macOS test hosts.
if [[ "$*" == -s ]]; then printf 'Linux\n'; else exec "$TEST_AGENT_REAL_UNAME" "$@"; fi
SH
    chmod +x "$mock_bin/systemctl" "$mock_bin/sudo" "$mock_bin/curl" "$mock_bin/uname"
}
