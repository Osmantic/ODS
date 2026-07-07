#!/usr/bin/env bats
# ============================================================================
# BATS tests for the FIREWALL_CHECK in scripts/linux-install-preflight.sh
# ============================================================================
# Drives the script as a black box with a stubbed PATH so we can simulate the
# four combinations of ufw / firewalld presence and active state. We target the
# script form (not the inline phase block in 01-preflight.sh) because it has
# the same dual-path logic and is invocable without sourcing the install
# orchestrator.
#
# Strategy:
#   - $STUB_BIN holds shell stubs for ufw / firewall-cmd / systemctl.
#   - Real /usr/bin tools (python3, jq, df, awk, mktemp, ...) stay reachable
#     because we PREpend $STUB_BIN to $PATH rather than replacing it.
#   - We invoke the script with --json and parse the FIREWALL_CHECK entry from
#     the report's checks[] array using jq — exact match against the message
#     emitted by the production code.

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

PREFLIGHT_SCRIPT="$BATS_TEST_DIRNAME/../../scripts/linux-install-preflight.sh"

setup() {
    STUB_BIN="$BATS_TEST_TMPDIR/stub-bin"
    mkdir -p "$STUB_BIN"
    export PATH="$STUB_BIN:$PATH"

    # Default: a real systemctl might exist on the host. Override with a stub
    # whose behaviour each test sets via $SYSTEMCTL_ACTIVE_UNITS.
    cat > "$STUB_BIN/systemctl" <<'STUB'
#!/usr/bin/env bash
# Stub: returns 0 only when the queried unit is in $SYSTEMCTL_ACTIVE_UNITS
# (space-separated). Recognises "is-active --quiet <unit>".
if [[ "${1:-}" == "is-active" ]]; then
    shift
    [[ "${1:-}" == "--quiet" ]] && shift
    unit="${1:-}"
    for u in ${SYSTEMCTL_ACTIVE_UNITS:-}; do
        [[ "$u" == "$unit" ]] && exit 0
    done
    exit 3
fi
exit 0
STUB
    chmod +x "$STUB_BIN/systemctl"
}

teardown() {
    rm -rf "$STUB_BIN"
}

# Helper: install a binary stub by name (no-op, exits 0).
make_stub() {
    local name="$1"
    cat > "$STUB_BIN/$name" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
    chmod +x "$STUB_BIN/$name"
}

# Helper: extract the FIREWALL_CHECK entry from the script's --json output.
# The script may exit non-zero (because docker / disk checks fail under the
# stubbed PATH); we only care that JSON was emitted to stdout.
firewall_check_field() {
    local field="$1"
    echo "$output" | jq -r --arg f "$field" '.checks[] | select(.id=="FIREWALL_CHECK") | .[$f]'
}

@test "FIREWALL_CHECK: pass when neither ufw nor firewalld is installed" {
    export SYSTEMCTL_ACTIVE_UNITS=""
    run bash "$PREFLIGHT_SCRIPT" --json
    [[ -n "$output" ]]
    assert_equal "$(firewall_check_field status)" "pass"
    assert_equal "$(firewall_check_field message)" "No restrictive host firewall detected"
}

@test "FIREWALL_CHECK: pass when ufw is installed but inactive" {
    make_stub ufw
    export SYSTEMCTL_ACTIVE_UNITS=""   # ufw not active
    run bash "$PREFLIGHT_SCRIPT" --json
    [[ -n "$output" ]]
    assert_equal "$(firewall_check_field status)" "pass"
}

@test "FIREWALL_CHECK: warn when ufw is installed and active (mentions UFW)" {
    make_stub ufw
    export SYSTEMCTL_ACTIVE_UNITS="ufw"
    run bash "$PREFLIGHT_SCRIPT" --json
    [[ -n "$output" ]]
    assert_equal "$(firewall_check_field status)" "warn"
    local msg
    msg="$(firewall_check_field message)"
    [[ "$msg" == *"UFW"* ]] || { echo "expected UFW in message, got: $msg" >&2; return 1; }
}

@test "FIREWALL_CHECK: warn when firewalld is installed and active (mentions firewalld)" {
    make_stub firewall-cmd
    export SYSTEMCTL_ACTIVE_UNITS="firewalld"
    run bash "$PREFLIGHT_SCRIPT" --json
    [[ -n "$output" ]]
    assert_equal "$(firewall_check_field status)" "warn"
    local msg
    msg="$(firewall_check_field message)"
    [[ "$msg" == *"firewalld"* ]] || { echo "expected firewalld in message, got: $msg" >&2; return 1; }
}

@test "FIREWALL_CHECK: warn when both are active — ufw branch wins (first detected)" {
    # The production code uses if/elif, so when both stubs exist and both
    # units are active, the UFW branch is taken. Lock that in.
    make_stub ufw
    make_stub firewall-cmd
    export SYSTEMCTL_ACTIVE_UNITS="ufw firewalld"
    run bash "$PREFLIGHT_SCRIPT" --json
    [[ -n "$output" ]]
    assert_equal "$(firewall_check_field status)" "warn"
    local msg
    msg="$(firewall_check_field message)"
    [[ "$msg" == *"UFW"* ]] || { echo "expected UFW (first-detected) in message, got: $msg" >&2; return 1; }
}
