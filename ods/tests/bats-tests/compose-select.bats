#!/usr/bin/env bats
# ============================================================================
# BATS tests for installers/lib/compose-select.sh
# ============================================================================
# Tests: resolve_compose_config() with various TIER / GPU_BACKEND combos
#
# Strategy: Set SCRIPT_DIR to a temp directory, create mock compose files
# there, and verify COMPOSE_FLAGS / COMPOSE_FILE are set correctly.

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    # Stub logging functions
    log() { :; }; export -f log
    warn() { :; }; export -f warn

    # Set up a fake SCRIPT_DIR with compose files
    export SCRIPT_DIR="$BATS_TEST_TMPDIR/ods"
    mkdir -p "$SCRIPT_DIR"

    # Create a no-op safe-env.sh (compose-select.sh sources it)
    mkdir -p "$SCRIPT_DIR/lib"
    cat > "$SCRIPT_DIR/lib/safe-env.sh" << 'STUB'
load_env_from_output() { :; }
STUB

    # Create mock compose files that resolve_compose_config checks for
    touch "$SCRIPT_DIR/docker-compose.yml"
    touch "$SCRIPT_DIR/docker-compose.base.yml"
    touch "$SCRIPT_DIR/docker-compose.nvidia.yml"
    touch "$SCRIPT_DIR/docker-compose.cpu.yml"
    touch "$SCRIPT_DIR/docker-compose.amd.yml"
    touch "$SCRIPT_DIR/docker-compose.apple.yml"

    # Do NOT create resolve-compose-stack.sh (we don't want to run the
    # external script resolver — just test the in-function logic)

    # Clear profile overlays by default
    unset CAP_COMPOSE_OVERLAYS

    export LOG_FILE="$BATS_TEST_TMPDIR/compose-test.log"

    # Source the library under test
    source "$BATS_TEST_DIRNAME/../../installers/lib/compose-select.sh"
}

# ── NV_ULTRA tier ───────────────────────────────────────────────────────────

@test "resolve_compose_config: NV_ULTRA selects base + nvidia overlay" {
    TIER=NV_ULTRA
    GPU_BACKEND=nvidia
    resolve_compose_config
    assert_equal "$COMPOSE_FLAGS" "-f docker-compose.base.yml -f docker-compose.nvidia.yml"
    assert_equal "$COMPOSE_FILE" "docker-compose.nvidia.yml"
}

# ── CLOUD tier ──────────────────────────────────────────────────────────────

@test "resolve_compose_config: CLOUD selects base only" {
    TIER=CLOUD
    GPU_BACKEND=cpu
    resolve_compose_config
    assert_equal "$COMPOSE_FLAGS" "-f docker-compose.base.yml"
    assert_equal "$COMPOSE_FILE" "docker-compose.base.yml"
}

# ── Strix Halo tiers ───────────────────────────────────────────────────────

@test "resolve_compose_config: SH_LARGE selects base + amd overlay" {
    TIER=SH_LARGE
    GPU_BACKEND=amd
    resolve_compose_config
    assert_equal "$COMPOSE_FLAGS" "-f docker-compose.base.yml -f docker-compose.amd.yml"
    assert_equal "$COMPOSE_FILE" "docker-compose.amd.yml"
}

@test "resolve_compose_config: SH_COMPACT selects base + amd overlay" {
    TIER=SH_COMPACT
    GPU_BACKEND=amd
    resolve_compose_config
    assert_equal "$COMPOSE_FLAGS" "-f docker-compose.base.yml -f docker-compose.amd.yml"
    assert_equal "$COMPOSE_FILE" "docker-compose.amd.yml"
}

# ── CPU backend (no GPU) ────────────────────────────────────────────────────

@test "resolve_compose_config: CPU backend selects base + cpu overlay" {
    TIER=T1
    GPU_BACKEND=cpu
    resolve_compose_config
    assert_equal "$COMPOSE_FLAGS" "-f docker-compose.base.yml -f docker-compose.cpu.yml"
    assert_equal "$COMPOSE_FILE" "docker-compose.cpu.yml"
}

# ── Numeric tiers (default NVIDIA path) ────────────────────────────────────

@test "resolve_compose_config: CPU backend overrides Strix Halo tier" {
    TIER=SH_LARGE
    GPU_BACKEND=cpu
    resolve_compose_config
    assert_equal "$COMPOSE_FLAGS" "-f docker-compose.base.yml -f docker-compose.cpu.yml"
    assert_equal "$COMPOSE_FILE" "docker-compose.cpu.yml"
}

@test "resolve_compose_config: numeric tier selects base + nvidia" {
    TIER=3
    GPU_BACKEND=nvidia
    resolve_compose_config
    assert_equal "$COMPOSE_FLAGS" "-f docker-compose.base.yml -f docker-compose.nvidia.yml"
    assert_equal "$COMPOSE_FILE" "docker-compose.nvidia.yml"
}

# ── Fallback when compose files are missing ─────────────────────────────────

@test "resolve_compose_config: falls back to docker-compose.yml when base missing" {
    rm -f "$SCRIPT_DIR/docker-compose.base.yml"
    rm -f "$SCRIPT_DIR/docker-compose.nvidia.yml"

    TIER=3
    GPU_BACKEND=nvidia
    resolve_compose_config
    assert_equal "$COMPOSE_FLAGS" "-f docker-compose.yml"
}

# ── Override file inclusion ─────────────────────────────────────────────────

@test "resolve_compose_config: includes override file when present" {
    touch "$SCRIPT_DIR/docker-compose.override.yml"

    TIER=CLOUD
    GPU_BACKEND=cpu
    resolve_compose_config
    [[ "$COMPOSE_FLAGS" == *"docker-compose.override.yml"* ]]
}

@test "resolve_compose_config: does not include override when absent" {
    # Ensure override does NOT exist
    rm -f "$SCRIPT_DIR/docker-compose.override.yml"

    TIER=CLOUD
    GPU_BACKEND=cpu
    resolve_compose_config
    [[ "$COMPOSE_FLAGS" != *"override"* ]]
}

# ── Capability profile overlays ─────────────────────────────────────────────

@test "resolve_compose_config: CAP_COMPOSE_OVERLAYS overrides default selection" {
    # Create the overlay files
    touch "$SCRIPT_DIR/docker-compose.base.yml"
    touch "$SCRIPT_DIR/docker-compose.apple.yml"

    export CAP_COMPOSE_OVERLAYS="docker-compose.base.yml,docker-compose.apple.yml"
    TIER=3
    GPU_BACKEND=apple

    resolve_compose_config
    assert_equal "$COMPOSE_FLAGS" "-f docker-compose.base.yml -f docker-compose.apple.yml"
    assert_equal "$COMPOSE_FILE" "docker-compose.apple.yml"
}

@test "resolve_compose_config: falls back when CAP_COMPOSE_OVERLAYS references missing file" {
    export CAP_COMPOSE_OVERLAYS="docker-compose.base.yml,docker-compose.nonexistent.yml"
    TIER=3
    GPU_BACKEND=nvidia

    resolve_compose_config
    # Should fall back to default selection since overlay file doesn't exist
    assert_equal "$COMPOSE_FLAGS" "-f docker-compose.base.yml -f docker-compose.nvidia.yml"
}
