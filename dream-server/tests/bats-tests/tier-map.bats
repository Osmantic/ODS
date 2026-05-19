#!/usr/bin/env bats
# ============================================================================
# BATS tests for installers/lib/tier-map.sh
# ============================================================================
# Tests: resolve_tier_config(), tier_to_model()

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    # Stub logging functions that tier-map.sh expects
    error() { echo "ERROR: $*" >&2; return 1; }
    export -f error
    log() { :; }
    export -f log

    # Source the library under test
    source "$BATS_TEST_DIRNAME/../../installers/lib/tier-map.sh"
}

teardown() {
    unset MODEL_PROFILE
    unset HOST_ARCH
}

# ── resolve_tier_config ─────────────────────────────────────────────────────

@test "resolve_tier_config: default profile keeps tier 1 on Qwen" {
    TIER=1
    resolve_tier_config
    assert_equal "$TIER_NAME" "Entry Level"
    assert_equal "$MODEL_PROFILE_EFFECTIVE" "qwen"
    assert_equal "$LLM_MODEL" "qwen3.5-9b"
    assert_equal "$GGUF_FILE" "Qwen3.5-9B-Q4_K_M.gguf"
    assert_equal "$MAX_CONTEXT" "16384"
}

@test "resolve_tier_config: default profile keeps tier 2 on Qwen" {
    TIER=2
    resolve_tier_config
    assert_equal "$TIER_NAME" "Prosumer"
    assert_equal "$MODEL_PROFILE_EFFECTIVE" "qwen"
    assert_equal "$LLM_MODEL" "qwen3.5-9b"
    assert_equal "$MAX_CONTEXT" "32768"
}

@test "resolve_tier_config: default profile keeps tier 3 on Qwen" {
    TIER=3
    resolve_tier_config
    assert_equal "$TIER_NAME" "Pro"
    assert_equal "$MODEL_PROFILE_EFFECTIVE" "qwen"
    assert_equal "$LLM_MODEL" "qwen3-30b-a3b"
    assert_equal "$GGUF_FILE" "Qwen3-30B-A3B-Q4_K_M.gguf"
    assert_equal "$MAX_CONTEXT" "32768"
}

@test "resolve_tier_config: default profile keeps tier 4 on Qwen" {
    TIER=4
    resolve_tier_config
    assert_equal "$TIER_NAME" "Enterprise"
    assert_equal "$MODEL_PROFILE_EFFECTIVE" "qwen"
    assert_equal "$LLM_MODEL" "qwen3-30b-a3b"
    assert_equal "$GGUF_FILE" "Qwen3-30B-A3B-Q4_K_M.gguf"
    assert_equal "$MAX_CONTEXT" "131072"
}

@test "resolve_tier_config: default profile keeps NV_ULTRA on Qwen Coder Next" {
    TIER=NV_ULTRA
    resolve_tier_config
    assert_equal "$TIER_NAME" "NVIDIA Ultra (90GB+)"
    assert_equal "$MODEL_PROFILE_EFFECTIVE" "qwen"
    assert_equal "$LLM_MODEL" "qwen3-coder-next"
    assert_equal "$GGUF_FILE" "qwen3-coder-next-Q4_K_M.gguf"
    assert_equal "$MAX_CONTEXT" "131072"
}

@test "resolve_tier_config: arm64 NV_ULTRA substitutes A3B MoE for coder-next" {
    TIER=NV_ULTRA
    HOST_ARCH=arm64
    resolve_tier_config
    assert_equal "$TIER_NAME" "NVIDIA Ultra (90GB+, aarch64 — A3B substitution)"
    assert_equal "$MODEL_PROFILE_EFFECTIVE" "qwen"
    assert_equal "$LLM_MODEL" "qwen3.6-35b-a3b"
    assert_equal "$GGUF_FILE" "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
    assert_equal "$MAX_CONTEXT" "131072"
}

@test "resolve_tier_config: default profile keeps SH_LARGE on Qwen Coder Next" {
    TIER=SH_LARGE
    resolve_tier_config
    assert_equal "$TIER_NAME" "Strix Halo 90+"
    assert_equal "$MODEL_PROFILE_EFFECTIVE" "qwen"
    assert_equal "$LLM_MODEL" "qwen3-coder-next"
    assert_equal "$MAX_CONTEXT" "131072"
}

@test "resolve_tier_config: default profile keeps SH_COMPACT on Qwen 30B A3B" {
    TIER=SH_COMPACT
    resolve_tier_config
    assert_equal "$TIER_NAME" "Strix Halo Compact"
    assert_equal "$MODEL_PROFILE_EFFECTIVE" "qwen"
    assert_equal "$LLM_MODEL" "qwen3-30b-a3b"
    assert_equal "$MAX_CONTEXT" "131072"
}

@test "resolve_tier_config: CLOUD sets claude model with 200k context" {
    TIER=CLOUD
    resolve_tier_config
    assert_equal "$TIER_NAME" "Cloud (API)"
    assert_equal "$LLM_MODEL" "anthropic/claude-sonnet-4-5-20250514"
    assert_equal "$GGUF_FILE" ""
    assert_equal "$GGUF_URL" ""
    assert_equal "$MAX_CONTEXT" "200000"
}

@test "resolve_tier_config: invalid tier returns error" {
    TIER=INVALID
    run resolve_tier_config
    assert_failure
    assert_output --partial "ERROR: Invalid tier: INVALID"
}

# ── tier_to_model ────────────────────────────────────────────────────────────

@test "tier_to_model: default profile keeps numeric tiers on Qwen" {
    run tier_to_model 1
    assert_output "qwen3.5-9b"

    run tier_to_model 2
    assert_output "qwen3.5-9b"

    run tier_to_model 3
    assert_output "qwen3-30b-a3b"

    run tier_to_model 4
    assert_output "qwen3-30b-a3b"
}

@test "tier_to_model: default profile maps T-prefix aliases correctly" {
    run tier_to_model T1
    assert_output "qwen3.5-9b"

    run tier_to_model T2
    assert_output "qwen3.5-9b"

    run tier_to_model T3
    assert_output "qwen3-30b-a3b"

    run tier_to_model T4
    assert_output "qwen3-30b-a3b"
}

@test "tier_to_model: default profile maps special tiers correctly" {
    run tier_to_model CLOUD
    assert_output "anthropic/claude-sonnet-4-5-20250514"

    run tier_to_model NV_ULTRA
    assert_output "qwen3-coder-next"

    run tier_to_model SH_LARGE
    assert_output "qwen3-coder-next"

    run tier_to_model SH_COMPACT
    assert_output "qwen3-30b-a3b"

    run tier_to_model SH
    assert_output "qwen3-30b-a3b"
}

@test "tier_to_model: arm64 NV_ULTRA maps to A3B MoE substitution" {
    HOST_ARCH=arm64
    run tier_to_model NV_ULTRA
    assert_output "qwen3.6-35b-a3b"
}

@test "tier_to_model: invalid tier returns empty string" {
    run tier_to_model INVALID
    assert_output ""

    run tier_to_model 99
    assert_output ""

    run tier_to_model ""
    assert_output ""
}

@test "resolve_tier_config: gemma4 profile maps tier 2 to Gemma 4 E4B" {
    export MODEL_PROFILE=gemma4
    TIER=2
    resolve_tier_config
    assert_equal "$LLM_MODEL" "gemma-4-e4b-it"
    assert_equal "$GGUF_FILE" "gemma-4-E4B-it-Q4_K_M.gguf"
    assert_equal "$MAX_CONTEXT" "32768"
    assert_equal "$LLAMA_SERVER_IMAGE" "ghcr.io/ggml-org/llama.cpp:server-cuda-b9014"
    assert_equal "$LLAMA_CPP_RELEASE_TAG_OVERRIDE" "b9014"
}

@test "resolve_tier_config: qwen profile preserves current tier 2 mapping" {
    export MODEL_PROFILE=qwen
    TIER=2
    resolve_tier_config
    assert_equal "$MODEL_PROFILE_EFFECTIVE" "qwen"
    assert_equal "$LLM_MODEL" "qwen3.5-9b"
    assert_equal "$GGUF_FILE" "Qwen3.5-9B-Q4_K_M.gguf"
    assert_equal "$MAX_CONTEXT" "32768"
}

@test "resolve_tier_config: unset profile keeps the legacy qwen default" {
    unset MODEL_PROFILE
    TIER=2
    resolve_tier_config
    assert_equal "$MODEL_PROFILE_EFFECTIVE" "qwen"
    assert_equal "$LLM_MODEL" "qwen3.5-9b"
}

@test "resolve_tier_config: auto profile keeps qwen on tier 0" {
    export MODEL_PROFILE=auto
    TIER=0
    resolve_tier_config
    assert_equal "$MODEL_PROFILE_EFFECTIVE" "qwen"
    assert_equal "$LLM_MODEL" "qwen3.5-2b"
}

@test "resolve_tier_config: auto profile prefers Gemma on tier 3" {
    export MODEL_PROFILE=auto
    TIER=3
    resolve_tier_config
    assert_equal "$MODEL_PROFILE_EFFECTIVE" "gemma4"
    assert_equal "$LLM_MODEL" "gemma-4-26b-a4b-it"
    assert_equal "$MAX_CONTEXT" "16384"
}

@test "resolve_tier_config: auto profile prefers Gemma on tier 4 with safer context" {
    export MODEL_PROFILE=auto
    TIER=4
    resolve_tier_config
    assert_equal "$MODEL_PROFILE_EFFECTIVE" "gemma4"
    assert_equal "$LLM_MODEL" "gemma-4-31b-it"
    assert_equal "$MAX_CONTEXT" "65536"
}

@test "resolve_tier_config: auto profile prefers Gemma on SH_COMPACT with safer context" {
    export MODEL_PROFILE=auto
    TIER=SH_COMPACT
    resolve_tier_config
    assert_equal "$MODEL_PROFILE_EFFECTIVE" "gemma4"
    assert_equal "$LLM_MODEL" "gemma-4-26b-a4b-it"
    assert_equal "$MAX_CONTEXT" "65536"
}

@test "tier_to_model: gemma4 profile maps tiers correctly" {
    run tier_to_model 1 gemma4
    assert_output "gemma-4-e2b-it"

    run tier_to_model 2 gemma4
    assert_output "gemma-4-e4b-it"

    run tier_to_model 4 gemma4
    assert_output "gemma-4-31b-it"
}

@test "tier_to_model: qwen profile still maps tiers to the current defaults" {
    run tier_to_model 1 qwen
    assert_output "qwen3.5-9b"

    run tier_to_model 3 qwen
    assert_output "qwen3-30b-a3b"

    run tier_to_model NV_ULTRA qwen
    assert_output "qwen3-coder-next"

    HOST_ARCH=arm64
    run tier_to_model NV_ULTRA qwen
    assert_output "qwen3.6-35b-a3b"
}
