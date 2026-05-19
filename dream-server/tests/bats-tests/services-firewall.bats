#!/usr/bin/env bats
# ============================================================================
# BATS tests for installers/phases/11-services.sh host-agent firewall helper.
# ============================================================================
# The full phase has top-level installer side effects, so these tests extract
# only _phase11_allow_host_agent_firewall and exercise it with stubbed commands.

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

PHASE11="$BATS_TEST_DIRNAME/../../installers/phases/11-services.sh"

setup() {
    STUB_BIN="$BATS_TEST_TMPDIR/stub-bin"
    COMMAND_LOG="$BATS_TEST_TMPDIR/commands.log"
    mkdir -p "$STUB_BIN"
    touch "$COMMAND_LOG"
    export STUB_BIN COMMAND_LOG
    export PATH="$STUB_BIN:$PATH"
    export DOCKER_CMD="docker"
    export DREAM_AGENT_PORT=7710
    export LOG_FILE="$BATS_TEST_TMPDIR/install.log"

    cat > "$STUB_BIN/uname" <<'STUB'
#!/usr/bin/env bash
echo Linux
STUB
    chmod +x "$STUB_BIN/uname"

    cat > "$STUB_BIN/docker" <<'STUB'
#!/usr/bin/env bash
if [[ "${1:-}" == "network" && "${2:-}" == "inspect" ]]; then
    printf '%s\n' "${DOCKER_NETWORK_SUBNETS:-10.89.0.0/24}"
    exit 0
fi
exit 1
STUB
    chmod +x "$STUB_BIN/docker"

    cat > "$STUB_BIN/systemctl" <<'STUB'
#!/usr/bin/env bash
if [[ "${1:-}" == "is-active" ]]; then
    shift
    [[ "${1:-}" == "--quiet" ]] && shift
    unit="${1:-}"
    for active in ${SYSTEMCTL_ACTIVE_UNITS:-}; do
        [[ "$unit" == "$active" ]] && exit 0
    done
    exit 3
fi
exit 0
STUB
    chmod +x "$STUB_BIN/systemctl"

    cat > "$STUB_BIN/ufw" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
    chmod +x "$STUB_BIN/ufw"

    cat > "$STUB_BIN/firewall-cmd" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
    chmod +x "$STUB_BIN/firewall-cmd"

    cat > "$STUB_BIN/sudo" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$COMMAND_LOG"
case "${1:-}" in
    ufw)
        if [[ "${2:-}" == "status" ]]; then
            exit 0
        fi
        echo "Rule added"
        exit 0
        ;;
    firewall-cmd)
        if [[ "${2:-}" == "--query-rich-rule="* ]]; then
            exit 1
        fi
        echo "success"
        exit 0
        ;;
esac
exit 0
STUB
    chmod +x "$STUB_BIN/sudo"
}

extract_firewall_helper() {
    local out="$1"
    awk '
        /^    _phase11_allow_host_agent_firewall\(\) \{/ { capture=1 }
        capture { print }
        capture && /^    \}/ { exit }
    ' "$PHASE11" > "$out"
}

@test "services firewall: UFW rule is scoped to detected dream-network subnet" {
    helper="$BATS_TEST_TMPDIR/helper.sh"
    extract_firewall_helper "$helper"

    run bash -c '
        source "'"$helper"'"
        ai_ok() { echo "OK: $1"; }
        ai_warn() { echo "WARN: $1"; }
        export SYSTEMCTL_ACTIVE_UNITS="ufw"
        export DOCKER_NETWORK_SUBNETS="10.89.0.0/24"
        _phase11_allow_host_agent_firewall dream-network
        cat "$COMMAND_LOG"
    '

    assert_success
    assert_output --partial "ufw allow from 10.89.0.0/24 to any port 7710 proto tcp comment dream-host-agent"
    refute_output --partial "172.16.0.0/12"
}

@test "services firewall: firewalld rich rule is scoped to detected dream-network subnet" {
    helper="$BATS_TEST_TMPDIR/helper.sh"
    extract_firewall_helper "$helper"

    run bash -c '
        source "'"$helper"'"
        ai_ok() { echo "OK: $1"; }
        ai_warn() { echo "WARN: $1"; }
        export SYSTEMCTL_ACTIVE_UNITS="firewalld"
        export DOCKER_NETWORK_SUBNETS="10.90.0.0/24"
        _phase11_allow_host_agent_firewall dream-network
        cat "$COMMAND_LOG"
    '

    assert_success
    assert_output --partial "source address=\"10.90.0.0/24\""
    assert_output --partial "port=\"7710\" accept"
    refute_output --partial "172.16.0.0/12"
}

@test "services firewall: explicit non-wildcard DREAM_AGENT_BIND skips auto rule" {
    helper="$BATS_TEST_TMPDIR/helper.sh"
    extract_firewall_helper "$helper"

    run bash -c '
        source "'"$helper"'"
        ai_ok() { echo "OK: $1"; }
        ai_warn() { echo "WARN: $1"; }
        export SYSTEMCTL_ACTIVE_UNITS="ufw"
        export DREAM_AGENT_BIND="127.0.0.1"
        _phase11_allow_host_agent_firewall dream-network
        cat "$COMMAND_LOG"
    '

    assert_success
    assert_output --partial "skipping automatic host-agent firewall rule"
    refute_output --partial "ufw allow"
}

@test "services firewall: installer no longer contains broad docker CIDR allow rule" {
    run grep -R "172\\.16\\.0\\.0/12" "$BATS_TEST_DIRNAME/../../installers" "$BATS_TEST_DIRNAME/../../scripts"
    assert_failure
}
