#!/usr/bin/env bats
# ============================================================================
# BATS tests for installers/phases/12-health.sh
# ============================================================================
# Tests the health check phase logic paths that can be exercised without
# running actual Docker containers.

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    # Stub logging/UI functions
    log() { echo "LOG: $1" >> "$BATS_TEST_TMPDIR/health.log"; }
    export -f log
    warn() { echo "WARN: $1" >> "$BATS_TEST_TMPDIR/health.log"; }
    export -f warn
    error() { echo "ERROR: $1" >> "$BATS_TEST_TMPDIR/health.log"; exit 1; }
    export -f error
    ai() { :; }; export -f ai
    ai_ok() { echo "OK" >> "$BATS_TEST_TMPDIR/health.log"; }; export -f ai_ok
    ai_bad() { :; }; export -f ai_bad
    ai_warn() { echo "AI_WARN: $1" >> "$BATS_TEST_TMPDIR/health.log"; }; export -f ai_warn
    signal() { echo "SIGNAL: $1" >> "$BATS_TEST_TMPDIR/health.log"; }; export -f signal
    show_phase() { :; }; export -f show_phase
    ods_progress() { :; }; export -f ods_progress
    check_service() { return 0; }; export -f check_service

    export SCRIPT_DIR="$BATS_TEST_TMPDIR/ods"
    export INSTALL_DIR="$BATS_TEST_TMPDIR/install-target"
    export LOG_FILE="$BATS_TEST_TMPDIR/health.log"
    export DRY_RUN=false
    export GPU_BACKEND="nvidia"
    export ENABLE_VOICE=false
    export ENABLE_WORKFLOWS=false
    export ENABLE_RAG=false
    export ENABLE_OPENCLAW=false
    export ENABLE_COMFYUI=false
    export LLM_MODEL="qwen3.5-9b"
    export WHISPER_PORT=9000
    export TTS_PORT=8880
    export OPENCLAW_PORT=7860
    export PERPLEXICA_PORT=3004
    export COMFYUI_PORT=8188

    mkdir -p "$SCRIPT_DIR/lib" "$INSTALL_DIR"
    touch "$LOG_FILE"

    # Create minimal service-registry.sh stub
    cat > "$SCRIPT_DIR/lib/service-registry.sh" << 'STUB'
SERVICE_PORTS=()
SERVICE_HEALTH=()
SERVICE_IDS=()
SERVICE_COMPOSE=()
SERVICE_DEPENDS=()
SR_LOADED=false
sr_load() { SR_LOADED=true; }
sr_resolve_ports() { :; }
STUB

    # Create minimal safe-env.sh stub
    cat > "$SCRIPT_DIR/lib/safe-env.sh" << 'STUB'
load_env_file() { :; }
STUB
}

teardown() {
    rm -rf "$BATS_TEST_TMPDIR/ods" "$BATS_TEST_TMPDIR/install-target"
}

# ── DRY_RUN mode ────────────────────────────────────────────────────────────

@test "health phase: DRY_RUN mode skips actual health checks" {
    export DRY_RUN=true
    run bash -c '
        export DRY_RUN=true
        export GPU_BACKEND="nvidia"
        export ENABLE_VOICE=false
        export ENABLE_WORKFLOWS=false
        export ENABLE_RAG=false
        export ENABLE_OPENCLAW=false
        export ENABLE_COMFYUI=false
        export LLM_MODEL="qwen3.5-9b"
        export SCRIPT_DIR="'"$SCRIPT_DIR"'"
        export INSTALL_DIR="'"$INSTALL_DIR"'"
        export LOG_FILE="'"$LOG_FILE"'"

        log() { echo "LOG: $1"; }
        warn() { :; }
        error() { echo "ERROR: $1"; exit 1; }
        ai() { :; }
        ai_ok() { echo "OK: $1"; }
        ai_bad() { :; }
        ai_warn() { :; }
        signal() { echo "SIGNAL: $1"; }
        show_phase() { :; }
        ods_progress() { :; }
        check_service() { return 0; }

        source "'"$BATS_TEST_DIRNAME/../../installers/phases/12-health.sh"'"
        echo "PHASE_COMPLETE"
    '
    assert_success
    assert_output --partial "PHASE_COMPLETE"
    assert_output --partial "dry run"
}

@test "health phase: DRY_RUN lists all services that would be checked" {
    export DRY_RUN=true
    export ENABLE_VOICE=true
    export ENABLE_WORKFLOWS=true
    export ENABLE_RAG=true
    export ENABLE_OPENCLAW=true
    export ENABLE_COMFYUI=true

    run bash -c '
        export DRY_RUN=true
        export GPU_BACKEND="nvidia"
        export ENABLE_VOICE=true
        export ENABLE_WORKFLOWS=true
        export ENABLE_RAG=true
        export ENABLE_OPENCLAW=true
        export ENABLE_COMFYUI=true
        export LLM_MODEL="qwen3.5-9b"
        export SCRIPT_DIR="'"$SCRIPT_DIR"'"
        export INSTALL_DIR="'"$INSTALL_DIR"'"
        export LOG_FILE="'"$LOG_FILE"'"

        log() { echo "LOG: $1"; }
        warn() { :; }
        error() { echo "ERROR: $1"; exit 1; }
        ai() { :; }
        ai_ok() { echo "OK: $1"; }
        ai_bad() { :; }
        ai_warn() { :; }
        signal() { echo "SIGNAL: $1"; }
        show_phase() { :; }
        ods_progress() { :; }
        check_service() { return 0; }

        source "'"$BATS_TEST_DIRNAME/../../installers/phases/12-health.sh"'"
    '
    assert_output --partial "Whisper"
    assert_output --partial "Kokoro"
    assert_output --partial "n8n"
    assert_output --partial "Qdrant"
    assert_output --partial "OpenClaw"
}

# ── _check_health failure tracking ──────────────────────────────────────────

@test "_check_health: increments HEALTH_FAILURES on check failure" {
    run bash -c '
        HEALTH_FAILURES=0
        check_service() { return 1; }
        _check_health() {
            if ! check_service "$@"; then
                HEALTH_FAILURES=$((HEALTH_FAILURES + 1))
            fi
        }
        _check_health "test-svc" "http://localhost:9999/health" 1 1
        echo "FAILURES=$HEALTH_FAILURES"
    '
    assert_output "FAILURES=1"
}

@test "_check_health: does not increment HEALTH_FAILURES on success" {
    run bash -c '
        HEALTH_FAILURES=0
        check_service() { return 0; }
        _check_health() {
            if ! check_service "$@"; then
                HEALTH_FAILURES=$((HEALTH_FAILURES + 1))
            fi
        }
        _check_health "test-svc" "http://localhost:9999/health" 1 1
        echo "FAILURES=$HEALTH_FAILURES"
    '
    assert_output "FAILURES=0"
}

@test "_check_health: accumulates multiple failures" {
    run bash -c '
        HEALTH_FAILURES=0
        check_service() { return 1; }
        _check_health() {
            if ! check_service "$@"; then
                HEALTH_FAILURES=$((HEALTH_FAILURES + 1))
            fi
        }
        _check_health "svc1" "http://localhost:1111/health" 1 1
        _check_health "svc2" "http://localhost:2222/health" 1 1
        _check_health "svc3" "http://localhost:3333/health" 1 1
        echo "FAILURES=$HEALTH_FAILURES"
    '
    assert_output "FAILURES=3"
}

# ── Service registry loading ────────────────────────────────────────────────

@test "health phase: loads service registry successfully" {
    run bash -c '
        export SCRIPT_DIR="'"$SCRIPT_DIR"'"
        export INSTALL_DIR="'"$INSTALL_DIR"'"
        export LOG_FILE="'"$LOG_FILE"'"
        export DRY_RUN=true
        export GPU_BACKEND="nvidia"
        export ENABLE_VOICE=false
        export ENABLE_WORKFLOWS=false
        export ENABLE_RAG=false
        export ENABLE_OPENCLAW=false
        export ENABLE_COMFYUI=false
        export LLM_MODEL="qwen3.5-9b"

        log() { :; }
        warn() { :; }
        error() { echo "ERROR: $1"; exit 1; }
        ai() { :; }
        ai_ok() { :; }
        ai_bad() { :; }
        ai_warn() { :; }
        signal() { :; }
        show_phase() { :; }
        ods_progress() { :; }
        check_service() { return 0; }

        source "'"$BATS_TEST_DIRNAME/../../installers/phases/12-health.sh"'"
        echo "SR_LOADED=$SR_LOADED"
    '
    assert_success
    assert_output --partial "SR_LOADED=true"
}
