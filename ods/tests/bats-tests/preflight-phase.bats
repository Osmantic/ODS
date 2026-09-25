#!/usr/bin/env bats
# ============================================================================
# BATS tests for installers/phases/01-preflight.sh
# ============================================================================
# Tests the preflight phase script by sourcing it in a controlled environment
# with mocked commands and filesystem.

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    # Stub logging/UI functions
    log() { echo "LOG: $1" >> "$BATS_TEST_TMPDIR/install.log"; }
    export -f log
    warn() { echo "WARN: $1" >> "$BATS_TEST_TMPDIR/install.log"; }
    export -f warn
    error() { echo "ERROR: $1" >> "$BATS_TEST_TMPDIR/install.log"; exit 1; }
    export -f error
    ai() { :; }; export -f ai
    ai_ok() { echo "OK" >> "$BATS_TEST_TMPDIR/install.log"; }; export -f ai_ok
    ai_bad() { :; }; export -f ai_bad
    signal() { :; }; export -f signal
    show_phase() { :; }; export -f show_phase
    show_stranger_boot() { :; }; export -f show_stranger_boot
    ods_progress() { :; }; export -f ods_progress

    # Set up test environment
    export SCRIPT_DIR="$BATS_TEST_TMPDIR/ods"
    export INSTALL_DIR="$BATS_TEST_TMPDIR/install-target"
    export LOG_FILE="$BATS_TEST_TMPDIR/install.log"
    export INTERACTIVE=false
    export DRY_RUN=false
    export PKG_MANAGER="apt"
    export VERSION="2.3.0"

    mkdir -p "$SCRIPT_DIR"
    touch "$LOG_FILE"

    # Create minimal os-release
    mkdir -p "$BATS_TEST_TMPDIR/etc"
    cat > "$BATS_TEST_TMPDIR/etc/os-release" << 'EOF'
PRETTY_NAME="Test Linux 1.0"
ID=ubuntu
EOF
}

teardown() {
    rm -rf "$BATS_TEST_TMPDIR/ods" "$BATS_TEST_TMPDIR/install-target"
}

install_preflight_tool_stubs() {
    mkdir -p "$BATS_TEST_TMPDIR/bin"
    cat > "$BATS_TEST_TMPDIR/bin/curl" << 'MOCK'
#!/bin/bash
if [[ " $* " == *" --version "* ]]; then
    echo "curl 8.0.0"
    exit 0
fi
case "${*: -1}" in
    https://github.com) printf '200' ;;
    https://registry-1.docker.io/v2/) printf '401' ;;
    *) exit 7 ;;
esac
MOCK
    cat > "$BATS_TEST_TMPDIR/bin/jq" << 'MOCK'
#!/bin/bash
echo "jq-1.7"
MOCK
    chmod +x "$BATS_TEST_TMPDIR/bin/curl" "$BATS_TEST_TMPDIR/bin/jq"
    export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
}

# ── Root check ──────────────────────────────────────────────────────────────

@test "preflight: fails when run as root" {
    local patched_phase="$BATS_TEST_TMPDIR/01-preflight-root-test.sh"
    sed 's/\[\[ \$EUID -eq 0 \]\]/[[ ${TEST_EUID:-$EUID} -eq 0 ]]/' \
        "$BATS_TEST_DIRNAME/../../installers/phases/01-preflight.sh" > "$patched_phase"

    run bash -c '
        export SCRIPT_DIR="'"$SCRIPT_DIR"'"
        export INSTALL_DIR="'"$INSTALL_DIR"'"
        export LOG_FILE="'"$LOG_FILE"'"
        export INTERACTIVE=false
        export DRY_RUN=false
        export PKG_MANAGER="apt"
        export VERSION="2.3.0"
        export TEST_EUID=0

        log() { :; }
        warn() { :; }
        error() { echo "ROOT_ERROR"; exit 1; }
        ai() { :; }
        ai_ok() { :; }
        ai_bad() { :; }
        signal() { :; }
        show_phase() { :; }
        show_stranger_boot() { :; }
        ods_progress() { :; }

        source "'"$patched_phase"'"
    '
    assert_failure
    assert_output --partial "ROOT_ERROR"
}

# ── OS check ────────────────────────────────────────────────────────────────

@test "preflight: fails when /etc/os-release is missing" {
    run bash -c '
        error() { echo "OS_ERROR"; exit 1; }
        if [[ ! -f "/nonexistent/os-release" ]]; then
            error "Unsupported OS."
        fi
    '
    assert_failure
    assert_output --partial "OS_ERROR"
}

@test "preflight: reads OS info from /etc/os-release" {
    # Create a fake os-release and test sourcing
    run bash -c '
        source "'"$BATS_TEST_TMPDIR/etc/os-release"'"
        echo "$PRETTY_NAME"
    '
    assert_success
    assert_output "Test Linux 1.0"
}

# ── Required tools ──────────────────────────────────────────────────────────

@test "preflight: fails when curl is missing" {
    # Create a PATH without curl
    mkdir -p "$BATS_TEST_TMPDIR/no-curl-bin"
    run bash -c '
        export PATH="'"$BATS_TEST_TMPDIR/no-curl-bin"'"
        PKG_MANAGER="apt"
        error() { echo "CURL_ERROR: $1"; exit 1; }
        if ! command -v curl &> /dev/null; then
            case "$PKG_MANAGER" in
                *) error "curl is required but not installed. Install with: sudo apt install curl" ;;
            esac
        fi
    '
    assert_failure
    assert_output --partial "CURL_ERROR"
}

# ── Source file check ───────────────────────────────────────────────────────

@test "preflight: fails when no compose files exist" {
    run bash -c '
        SCRIPT_DIR="'"$BATS_TEST_TMPDIR"'"
        error() { echo "COMPOSE_ERROR"; exit 1; }
        if [[ ! -f "$SCRIPT_DIR/docker-compose.yml" ]] && [[ ! -f "$SCRIPT_DIR/docker-compose.base.yml" ]]; then
            error "No compose files found."
        fi
    '
    assert_failure
    assert_output --partial "COMPOSE_ERROR"
}

@test "preflight: passes when compose files exist" {
    touch "$SCRIPT_DIR/docker-compose.base.yml"
    install_preflight_tool_stubs

    # Source the phase script (it will exit on error, so we capture)
    run bash -c '
        export SCRIPT_DIR="'"$SCRIPT_DIR"'"
        export INSTALL_DIR="'"$INSTALL_DIR"'"
        export LOG_FILE="'"$LOG_FILE"'"
        export INTERACTIVE=false
        export DRY_RUN=false
        export PKG_MANAGER="apt"
        export VERSION="2.3.0"
        export ODS_ALLOW_LEGACY_PARALLEL=1

        log() { :; }
        warn() { :; }
        error() { echo "PHASE_ERROR: $1"; exit 1; }
        ai() { :; }
        ai_ok() { :; }
        signal() { :; }
        show_phase() { :; }
        show_stranger_boot() { :; }
        ods_progress() { :; }

        source "'"$BATS_TEST_DIRNAME/../../installers/phases/01-preflight.sh"'"
        echo "PHASE_COMPLETE"
    '
    assert_success
    assert_output --partial "PHASE_COMPLETE"
}

# ── Existing installation detection ─────────────────────────────────────────

@test "preflight: detects existing installation" {
    touch "$SCRIPT_DIR/docker-compose.base.yml"
    mkdir -p "$INSTALL_DIR"

    # Stub successful required-network probes and required tools.
    install_preflight_tool_stubs

    run bash -c '
        export SCRIPT_DIR="'"$SCRIPT_DIR"'"
        export INSTALL_DIR="'"$INSTALL_DIR"'"
        export LOG_FILE="'"$LOG_FILE"'"
        export INTERACTIVE=false
        export DRY_RUN=false
        export PKG_MANAGER="apt"
        export VERSION="2.3.0"

        log() { echo "LOG: $1"; }
        warn() { :; }
        error() { echo "ERROR: $1"; exit 1; }
        ai() { :; }
        ai_ok() { :; }
        signal() { echo "SIGNAL: $1"; }
        show_phase() { :; }
        show_stranger_boot() { :; }
        ods_progress() { :; }

        source "'"$BATS_TEST_DIRNAME/../../installers/phases/01-preflight.sh"'"
    '

    # Should log about existing installation
    assert_output --partial "Existing installation"
}

# ── Optional tools warning ──────────────────────────────────────────────────

@test "preflight: warns about missing optional tools" {
    touch "$SCRIPT_DIR/docker-compose.base.yml"
    # Stub successful required-network probes, then shadow command -v rsync so
    # the optional-tool warning path remains deterministic.
    install_preflight_tool_stubs

    run bash -c '
        export SCRIPT_DIR="'"$SCRIPT_DIR"'"
        export INSTALL_DIR="'"$INSTALL_DIR"'"
        export LOG_FILE="'"$LOG_FILE"'"
        export INTERACTIVE=false
        export DRY_RUN=false
        export PKG_MANAGER="apt"
        export VERSION="2.3.0"
        export PATH="'"$BATS_TEST_TMPDIR/bin:$PATH"'"

        log() { :; }
        warn() { echo "WARN: $1"; }
        error() { echo "ERROR: $1"; exit 1; }
        ai() { :; }
        ai_ok() { :; }
        signal() { :; }
        show_phase() { :; }
        show_stranger_boot() { :; }
        ods_progress() { :; }
        command() {
            if [[ "$1" == "-v" && "$2" == "rsync" ]]; then
                return 1
            fi
            builtin command "$@"
        }

        source "'"$BATS_TEST_DIRNAME/../../installers/phases/01-preflight.sh"'"
    '

    assert_output --partial "rsync"
}
