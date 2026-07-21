#!/usr/bin/env bats
# ============================================================================
# BATS tests for installers/lib/bootstrap-model.sh
# ============================================================================
# Tests: bootstrap_needed()

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    # Stub tier_rank() — returns numeric rank for a given tier
    tier_rank() {
        case "$1" in
            0)         echo "0" ;;
            1)         echo "1" ;;
            2)         echo "2" ;;
            3)         echo "3" ;;
            4)         echo "4" ;;
            NV_ULTRA)  echo "5" ;;
            SH_LARGE)  echo "5" ;;
            SH_COMPACT) echo "3" ;;
            CLOUD)     echo "1" ;;
            *)         echo "1" ;;
        esac
    }
    export -f tier_rank

    # Set defaults for all expected variables
    export TIER=3
    export GGUF_FILE="Qwen3-30B-A3B-Q4_K_M.gguf"
    export INSTALL_DIR="$BATS_TEST_TMPDIR/ods"
    export NO_BOOTSTRAP="false"
    export OFFLINE_MODE="false"
    export ODS_MODE="local"

    # Create install dir (but NOT the model file)
    mkdir -p "$INSTALL_DIR/data/models"

    # Source the library under test
    source "$BATS_TEST_DIRNAME/../../installers/lib/bootstrap-model.sh"
}

# ── bootstrap_needed: returns true (0) when needed ─────────────────────────

@test "bootstrap_needed: returns 0 when all conditions met" {
    run bootstrap_needed
    assert_success
}

@test "bootstrap_needed: returns 0 for high tier without model on disk" {
    TIER=4
    GGUF_FILE="Qwen3-30B-A3B-Q4_K_M.gguf"
    run bootstrap_needed
    assert_success
}

@test "bootstrap_needed: returns 0 for NV_ULTRA tier" {
    TIER=NV_ULTRA
    run bootstrap_needed
    assert_success
}

# ── bootstrap_needed: returns false (1) when not needed ────────────────────

@test "bootstrap_needed: tier_rank=0 means not needed" {
    TIER=0
    run bootstrap_needed
    assert_failure
}

@test "bootstrap_needed: full model already on disk means not needed" {
    # Create the model file
    touch "$INSTALL_DIR/data/models/$GGUF_FILE"
    run bootstrap_needed
    assert_failure
}

@test "bootstrap_needed: NO_BOOTSTRAP=true means not needed" {
    NO_BOOTSTRAP="true"
    run bootstrap_needed
    assert_failure
}

@test "bootstrap_needed: OFFLINE_MODE=true means not needed" {
    OFFLINE_MODE="true"
    run bootstrap_needed
    assert_failure
}

@test "bootstrap_needed: ODS_MODE=cloud means not needed" {
    ODS_MODE="cloud"
    run bootstrap_needed
    assert_failure
}

# ── bootstrap constants ────────────────────────────────────────────────────

@test "BOOTSTRAP_GGUF_FILE: is set and non-empty" {
    [[ -n "$BOOTSTRAP_GGUF_FILE" ]]
}

@test "BOOTSTRAP_GGUF_URL: is set and points to huggingface" {
    [[ -n "$BOOTSTRAP_GGUF_URL" ]]
    [[ "$BOOTSTRAP_GGUF_URL" == *"huggingface.co"* ]]
}

@test "BOOTSTRAP_GGUF_SHA256: pins the fast-start model" {
    [[ "$BOOTSTRAP_GGUF_SHA256" =~ ^[a-f0-9]{64}$ ]]
}

@test "BOOTSTRAP_LLM_MODEL: is set and non-empty" {
    [[ -n "$BOOTSTRAP_LLM_MODEL" ]]
}

@test "BOOTSTRAP_MAX_CONTEXT: meets Hermes 64K floor" {
    [[ "$BOOTSTRAP_MAX_CONTEXT" =~ ^[0-9]+$ ]]
    [[ "$BOOTSTRAP_MAX_CONTEXT" -ge 64000 ]]
}
