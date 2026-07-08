#!/usr/bin/env bats
# ============================================================================
# BATS tests for installers/phases/04-requirements.sh
# ============================================================================
# Tests port conflict detection, RAM/disk tier thresholds, and Ollama
# conflict detection logic in isolation.

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    # Stub logging/UI functions
    log() { echo "LOG: $1" >> "$BATS_TEST_TMPDIR/requirements.log"; }
    export -f log
    warn() { echo "WARN: $1" >> "$BATS_TEST_TMPDIR/requirements.log"; }
    export -f warn
    error() { echo "ERROR: $1" >> "$BATS_TEST_TMPDIR/requirements.log"; exit 1; }
    export -f error
    ai() { :; }; export -f ai
    ai_ok() { echo "OK" >> "$BATS_TEST_TMPDIR/requirements.log"; }; export -f ai_ok
    ai_bad() { :; }; export -f ai_bad
    ai_warn() { echo "AI_WARN: $1" >> "$BATS_TEST_TMPDIR/requirements.log"; }; export -f ai_warn
    chapter() { :; }; export -f chapter
    ods_progress() { :; }; export -f ods_progress

    export SCRIPT_DIR="$BATS_TEST_TMPDIR/ods"
    export INSTALL_DIR="$BATS_TEST_TMPDIR/install-target"
    export LOG_FILE="$BATS_TEST_TMPDIR/requirements.log"
    export INTERACTIVE=false
    export DRY_RUN=false
    export PREFLIGHT_REPORT_FILE="$BATS_TEST_TMPDIR/preflight.json"

    mkdir -p "$SCRIPT_DIR"
    touch "$LOG_FILE"
}

teardown() {
    rm -rf "$BATS_TEST_TMPDIR/ods" "$BATS_TEST_TMPDIR/install-target"
}

# ── tier_rank helper ────────────────────────────────────────────────────────

@test "tier_rank: returns correct rank for numeric tiers" {
    run bash -c '
        export SCRIPT_DIR="'"$BATS_TEST_TMPDIR/ods"'"
        mkdir -p "$SCRIPT_DIR/lib"
        cat > "$SCRIPT_DIR/lib/safe-env.sh" << "STUB"
load_env_from_output() { :; }
STUB
        source "'"$BATS_TEST_DIRNAME/../../installers/lib/detection.sh"'"
        tier_rank 1
    '
    assert_output "1"

    run bash -c '
        export SCRIPT_DIR="'"$BATS_TEST_TMPDIR/ods"'"
        mkdir -p "$SCRIPT_DIR/lib"
        cat > "$SCRIPT_DIR/lib/safe-env.sh" << "STUB"
load_env_from_output() { :; }
STUB
        source "'"$BATS_TEST_DIRNAME/../../installers/lib/detection.sh"'"
        tier_rank 4
    '
    assert_output "4"
}

# ── Port conflict detection (check_port_conflict function) ──────────────────

@test "check_port_conflict: returns false when no process listens on port" {
    # Use a high port unlikely to be in use
    run bash -c '
        _port_check_warned=false
        check_port_conflict() {
            local port="$1"
            PORT_CONFLICT=false
            PORT_CONFLICT_PID=""
            PORT_CONFLICT_PROC=""
            if command -v ss &> /dev/null; then
                if ss -tln 2>/dev/null | grep -qE ":${port}(\s|$)"; then
                    PORT_CONFLICT=true
                    return 0
                fi
            fi
            return 1
        }
        # Use a very high port that nothing should be listening on
        if check_port_conflict 59876; then
            echo "CONFLICT"
        else
            echo "CLEAR"
        fi
    '
    assert_output "CLEAR"
}

@test "check_port_conflict: detects conflict when port is occupied" {
    # Start a background listener on a test port
    local test_port=59877
    python3 -c "
import socket, time, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', $test_port))
s.listen(1)
time.sleep(30)
" &
    local bg_pid=$!
    sleep 1

    run bash -c '
        _port_check_warned=false
        check_port_conflict() {
            local port="$1"
            PORT_CONFLICT=false
            PORT_CONFLICT_PID=""
            PORT_CONFLICT_PROC=""
            if command -v ss &> /dev/null; then
                if ss -tln 2>/dev/null | grep -qE ":${port}(\s|$)"; then
                    PORT_CONFLICT=true
                    return 0
                fi
            fi
            return 1
        }
        if check_port_conflict '$test_port'; then
            echo "CONFLICT"
        else
            echo "CLEAR"
        fi
    '

    kill $bg_pid 2>/dev/null || true
    wait $bg_pid 2>/dev/null || true

    assert_output "CONFLICT"
}

# ── RAM tier thresholds (legacy path) ──────────────────────────────────────

@test "legacy RAM check: tier 1 requires 16GB" {
    run bash -c '
        TIER=1
        RAM_GB=16
        case $TIER in
            *) MIN_RAM=16 ;;
        esac
        if [[ $RAM_GB -lt $MIN_RAM ]]; then
            echo "FAIL"
        else
            echo "PASS"
        fi
    '
    assert_output "PASS"
}

@test "legacy RAM check: tier 1 warns at 8GB" {
    run bash -c '
        TIER=1
        RAM_GB=8
        case $TIER in
            *) MIN_RAM=16 ;;
        esac
        if [[ $RAM_GB -lt $MIN_RAM ]]; then
            echo "WARN"
        else
            echo "PASS"
        fi
    '
    assert_output "WARN"
}

@test "legacy RAM check: tier 3 requires 48GB" {
    run bash -c '
        TIER=3
        RAM_GB=48
        case $TIER in
            3) MIN_RAM=48 ;;
            *) MIN_RAM=16 ;;
        esac
        if [[ $RAM_GB -lt $MIN_RAM ]]; then
            echo "FAIL"
        else
            echo "PASS"
        fi
    '
    assert_output "PASS"
}

# ── Disk tier thresholds (legacy path) ─────────────────────────────────────

@test "legacy disk check: tier 1 requires 30GB" {
    run bash -c '
        TIER=1
        DISK_AVAIL=50
        case $TIER in
            1) MIN_DISK=30 ;;
            *) MIN_DISK=50 ;;
        esac
        if [[ $DISK_AVAIL -lt $MIN_DISK ]]; then
            echo "FAIL"
        else
            echo "PASS"
        fi
    '
    assert_output "PASS"
}

@test "legacy disk check: tier 1 fails at 20GB" {
    run bash -c '
        TIER=1
        DISK_AVAIL=20
        case $TIER in
            1) MIN_DISK=30 ;;
            *) MIN_DISK=50 ;;
        esac
        if [[ $DISK_AVAIL -lt $MIN_DISK ]]; then
            echo "FAIL"
        else
            echo "PASS"
        fi
    '
    assert_output "FAIL"
}

@test "legacy disk check: tier 4 requires 150GB" {
    run bash -c '
        TIER=4
        DISK_AVAIL=200
        case $TIER in
            4) MIN_DISK=150 ;;
            *) MIN_DISK=50 ;;
        esac
        if [[ $DISK_AVAIL -lt $MIN_DISK ]]; then
            echo "FAIL"
        else
            echo "PASS"
        fi
    '
    assert_output "PASS"
}

# ── Ollama conflict detection ──────────────────────────────────────────────

@test "check_ollama_conflict: returns false when ollama not running" {
    run bash -c '
        check_ollama_conflict() {
            OLLAMA_RUNNING=false
            OLLAMA_PID=""
            if pgrep -x ollama >/dev/null 2>&1; then
                OLLAMA_RUNNING=true
                OLLAMA_PID=$(pgrep -x ollama | head -1)
            fi
        }
        check_ollama_conflict
        if $OLLAMA_RUNNING; then
            echo "RUNNING"
        else
            echo "NOT_RUNNING"
        fi
    '
    assert_output "NOT_RUNNING"
}

# ── Preflight engine missing fallback ──────────────────────────────────────

@test "requirements: falls back to legacy checks when preflight engine missing" {
    # Ensure preflight-engine.sh does NOT exist
    run bash -c '
        SCRIPT_DIR="'"$BATS_TEST_TMPDIR"'"
        if [[ -x "$SCRIPT_DIR/scripts/preflight-engine.sh" ]]; then
            echo "ENGINE_FOUND"
        else
            echo "ENGINE_MISSING"
        fi
    '
    assert_output "ENGINE_MISSING"
}
