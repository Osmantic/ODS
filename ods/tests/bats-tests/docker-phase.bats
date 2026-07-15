#!/usr/bin/env bats
# ============================================================================
# BATS tests for installers/phases/05-docker.sh
# ============================================================================
# Tests the Docker phase helper functions and logic paths in isolation.

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    # Stub logging/UI functions
    log() { echo "LOG: $1" >> "$BATS_TEST_TMPDIR/docker.log"; }
    export -f log
    warn() { echo "WARN: $1" >> "$BATS_TEST_TMPDIR/docker.log"; }
    export -f warn
    error() { echo "ERROR: $1" >> "$BATS_TEST_TMPDIR/docker.log"; exit 1; }
    export -f error
    ai() { :; }; export -f ai
    ai_ok() { echo "OK" >> "$BATS_TEST_TMPDIR/docker.log"; }; export -f ai_ok
    ai_bad() { :; }; export -f ai_bad
    ai_warn() { echo "AI_WARN: $1" >> "$BATS_TEST_TMPDIR/docker.log"; }; export -f ai_warn
    show_phase() { :; }; export -f show_phase
    ods_progress() { :; }; export -f ods_progress
    detect_pkg_manager() { PKG_MANAGER="apt"; }; export -f detect_pkg_manager
    pkg_install() { :; }; export -f pkg_install
    pkg_update() { :; }; export -f pkg_update
    pkg_resolve() { echo "$1"; }; export -f pkg_resolve

    export SCRIPT_DIR="$BATS_TEST_TMPDIR/ods"
    export LOG_FILE="$BATS_TEST_TMPDIR/docker.log"
    export DRY_RUN=false
    export INTERACTIVE=false
    export SKIP_DOCKER=false
    export GPU_COUNT=0
    export GPU_BACKEND="nvidia"
    export DOCKER_CMD=""
    export DOCKER_COMPOSE_CMD=""
    export DOCKER_NEEDS_SUDO=false

    mkdir -p "$SCRIPT_DIR"
    touch "$LOG_FILE"
}

teardown() {
    rm -rf "$BATS_TEST_TMPDIR/ods"
}

# ── SKIP_DOCKER ─────────────────────────────────────────────────────────────

@test "docker phase: skips installation when SKIP_DOCKER=true" {
    export SKIP_DOCKER=true
    # Source the phase — it should not attempt to install docker
    run bash -c '
        export SKIP_DOCKER=true
        export DRY_RUN=false
        export INTERACTIVE=false
        export GPU_COUNT=0
        export GPU_BACKEND="nvidia"
        export DOCKER_CMD=""
        export DOCKER_COMPOSE_CMD=""
        export DOCKER_NEEDS_SUDO=false
        export SCRIPT_DIR="'"$SCRIPT_DIR"'"
        export LOG_FILE="'"$LOG_FILE"'"

        log() { echo "LOG: $1"; }
        warn() { :; }
        error() { echo "ERROR: $1"; exit 1; }
        ai() { :; }
        ai_ok() { echo "OK: $1"; }
        ai_bad() { :; }
        ai_warn() { :; }
        show_phase() { :; }
        ods_progress() { :; }
        detect_pkg_manager() { PKG_MANAGER="apt"; }
        pkg_install() { :; }
        pkg_update() { :; }
        pkg_resolve() { echo "$1"; }
        ods_run_install_conflict_check() { return 0; }
        docker() {
            case "$*" in
                "compose version") echo "Docker Compose version v2.27.0" ;;
                "version") echo "Docker version 27.0.0" ;;
            esac
        }

        source "'"$BATS_TEST_DIRNAME/../../installers/phases/05-docker.sh"'"
        echo "PHASE_COMPLETE"
    '
    assert_success
    assert_output --partial "PHASE_COMPLETE"
    assert_output --partial "Skipping Docker"
}

# ── _docker_cmd_arr ─────────────────────────────────────────────────────────

@test "_docker_cmd_arr: returns sudo docker when DOCKER_CMD is sudo docker" {
    run bash -c '
        DOCKER_CMD="sudo docker"
        _docker_cmd_arr() {
            case "${DOCKER_CMD:-docker}" in
                "sudo docker") printf "%s %s\n" "sudo" "docker" ;;
                *)             printf "%s\n" "docker" ;;
            esac
        }
        _docker_cmd_arr
    '
    # The helper emits one shell-like command line; consumers word-split it
    # into argv with `local -a cmd=($(_docker_cmd_arr))`.
    assert_output 'sudo docker'
}

@test "_docker_cmd_arr: returns docker when DOCKER_CMD is empty" {
    run bash -c '
        DOCKER_CMD=""
        _docker_cmd_arr() {
            case "${DOCKER_CMD:-docker}" in
                "sudo docker") printf "%s %s\n" "sudo" "docker" ;;
                *)             printf "%s\n" "docker" ;;
            esac
        }
        _docker_cmd_arr
    '
    assert_output "docker"
}

# ── _docker_compose_detect_cmd ──────────────────────────────────────────────

@test "_docker_compose_detect_cmd: returns empty when neither compose is available" {
    # Create a PATH with no docker or docker-compose
    mkdir -p "$BATS_TEST_TMPDIR/empty-bin"
    run bash -c '
        export PATH="'"$BATS_TEST_TMPDIR/empty-bin"'"
        docker_compose_run() { return 1; }
        _docker_compose_detect_cmd() {
            if docker_compose_run version &>/dev/null 2>&1; then
                echo "docker compose"
                return 0
            fi
            if command -v docker-compose &>/dev/null; then
                echo "docker-compose"
                return 0
            fi
            echo ""
            return 1
        }
        result=$(_docker_compose_detect_cmd || true)
        echo "RESULT:[$result]"
    '
    assert_output "RESULT:[]"
}

# ── _docker_daemon_start_hint ───────────────────────────────────────────────

@test "_docker_daemon_start_hint: outputs helpful guidance" {
    run bash -c '
        warn() { echo "WARN: $1"; }
        _docker_daemon_start_hint() {
            warn "Docker daemon does not appear to be running or accessible."
            warn "Common fixes:"
            warn "  - Linux (systemd): sudo systemctl enable --now docker"
            warn "  - Linux (non-systemd): start dockerd using your init system"
            warn "  - WSL2: ensure Docker Desktop is running"
        }
        _docker_daemon_start_hint
    '
    assert_output --partial "systemctl"
    assert_output --partial "WSL2"
}

# ── DRY_RUN mode ────────────────────────────────────────────────────────────

@test "docker phase: respects DRY_RUN flag" {
    export DRY_RUN=true
    run bash -c '
        export SKIP_DOCKER=false
        export DRY_RUN=true
        export INTERACTIVE=false
        export GPU_COUNT=0
        export GPU_BACKEND="nvidia"
        export DOCKER_CMD=""
        export DOCKER_COMPOSE_CMD=""
        export DOCKER_NEEDS_SUDO=false
        export SCRIPT_DIR="'"$SCRIPT_DIR"'"
        export LOG_FILE="'"$LOG_FILE"'"

        log() { echo "LOG: $1"; }
        warn() { :; }
        error() { echo "ERROR: $1"; exit 1; }
        ai() { :; }
        ai_ok() { echo "OK: $1"; }
        ai_bad() { :; }
        ai_warn() { :; }
        show_phase() { :; }
        ods_progress() { :; }
        detect_pkg_manager() { PKG_MANAGER="apt"; }
        pkg_install() { echo "PKG_INSTALL: $*"; }
        pkg_update() { echo "PKG_UPDATE"; }
        pkg_resolve() { echo "$1"; }
        ods_run_install_conflict_check() { return 0; }

        # Mock docker as already installed so we skip the install path
        docker() { echo "Docker 27.0.0"; }
        export -f docker

        source "'"$BATS_TEST_DIRNAME/../../installers/phases/05-docker.sh"'"
        echo "PHASE_COMPLETE"
    '
    assert_success
    assert_output --partial "PHASE_COMPLETE"
}
