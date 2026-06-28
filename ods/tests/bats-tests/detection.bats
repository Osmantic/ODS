#!/usr/bin/env bats
# ============================================================================
# BATS tests for installers/lib/detection.sh
# ============================================================================
# Tests: normalize_profile_tier(), tier_rank()
# Also tests detect_gpu() with mocked nvidia-smi on Linux (skipped on macOS
# because detect_gpu uses GNU grep -oP for VRAM parsing).

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    # Stub logging/UI functions that detection.sh expects
    log() { :; }; export -f log
    warn() { :; }; export -f warn
    ai() { :; }; export -f ai
    ai_ok() { :; }; export -f ai_ok
    ai_warn() { :; }; export -f ai_warn
    ai_bad() { :; }; export -f ai_bad

    # detection.sh conditionally sources safe-env.sh via SCRIPT_DIR.
    # Point SCRIPT_DIR to a temp dir and provide a no-op safe-env.sh stub.
    export SCRIPT_DIR="$BATS_TEST_TMPDIR/ods"
    mkdir -p "$SCRIPT_DIR/lib"
    cat > "$SCRIPT_DIR/lib/safe-env.sh" << 'STUB'
load_env_from_output() { :; }
STUB

    # Source the library under test
    source "$BATS_TEST_DIRNAME/../../installers/lib/detection.sh"
}

# ── normalize_profile_tier ──────────────────────────────────────────────────

@test "normalize_profile_tier: T1 normalizes to 1" {
    run normalize_profile_tier T1
    assert_output "1"
}

@test "normalize_profile_tier: T2 normalizes to 2" {
    run normalize_profile_tier T2
    assert_output "2"
}

@test "normalize_profile_tier: T3 normalizes to 3" {
    run normalize_profile_tier T3
    assert_output "3"
}

@test "normalize_profile_tier: T4 normalizes to 4" {
    run normalize_profile_tier T4
    assert_output "4"
}

@test "normalize_profile_tier: special tiers pass through unchanged" {
    run normalize_profile_tier NV_ULTRA
    assert_output "NV_ULTRA"

    run normalize_profile_tier SH_LARGE
    assert_output "SH_LARGE"

    run normalize_profile_tier SH_COMPACT
    assert_output "SH_COMPACT"
}

@test "normalize_profile_tier: invalid input returns empty string" {
    run normalize_profile_tier INVALID
    assert_output ""

    run normalize_profile_tier ""
    assert_output ""

    run normalize_profile_tier 99
    assert_output ""
}

# ── tier_rank ───────────────────────────────────────────────────────────────

@test "tier_rank: NV_ULTRA and SH_LARGE are rank 5 (highest)" {
    run tier_rank NV_ULTRA
    assert_output "5"

    run tier_rank SH_LARGE
    assert_output "5"
}

@test "tier_rank: tier 4 is rank 4" {
    run tier_rank 4
    assert_output "4"
}

@test "tier_rank: SH_COMPACT and tier 3 are rank 3" {
    run tier_rank SH_COMPACT
    assert_output "3"

    run tier_rank 3
    assert_output "3"
}

@test "tier_rank: tier 2 is rank 2" {
    run tier_rank 2
    assert_output "2"
}

@test "tier_rank: tier 1 and unknown are rank 1 (lowest)" {
    run tier_rank 1
    assert_output "1"

    run tier_rank CLOUD
    assert_output "1"

    run tier_rank UNKNOWN
    assert_output "1"
}

@test "tier_rank: ordering is consistent (higher tier = higher rank)" {
    local rank1 rank2 rank3 rank4 rank5
    rank1=$(tier_rank 1)
    rank2=$(tier_rank 2)
    rank3=$(tier_rank 3)
    rank4=$(tier_rank 4)
    rank5=$(tier_rank NV_ULTRA)

    [[ $rank1 -lt $rank2 ]]
    [[ $rank2 -lt $rank3 ]]
    [[ $rank3 -lt $rank4 ]]
    [[ $rank4 -lt $rank5 ]]
}

# ── detect_gpu (requires GNU grep for -oP; skip on macOS) ──────────────────

@test "detect_gpu: detects single NVIDIA GPU via mock nvidia-smi" {
    # grep -oP (Perl regex) is used inside detect_gpu; only available with GNU grep
    if [[ "$(uname -s)" == "Darwin" ]]; then
        skip "detect_gpu uses GNU grep -oP, not available on macOS"
    fi

    mkdir -p "$BATS_TEST_TMPDIR/bin"
    cat > "$BATS_TEST_TMPDIR/bin/nvidia-smi" << 'MOCK'
#!/bin/bash
if [[ "$*" == *"--query-gpu=name,memory.total"* ]]; then
    echo "NVIDIA GeForce RTX 4090, 24564 MiB"
elif [[ "$*" == *"pci.device_id"* ]]; then
    echo "0x2684"
fi
MOCK
    chmod +x "$BATS_TEST_TMPDIR/bin/nvidia-smi"
    export PATH="$BATS_TEST_TMPDIR/bin:$PATH"

    # Mock sysfs vendor ID so detect_gpu trusts nvidia-smi without real hardware
    mkdir -p "$BATS_TEST_TMPDIR/sys/class/drm/card0/device"
    echo "0x10de" > "$BATS_TEST_TMPDIR/sys/class/drm/card0/device/vendor"
    export ODS_DRM_SYS="$BATS_TEST_TMPDIR/sys/class/drm"

    detect_gpu
    assert_equal "$GPU_NAME" "NVIDIA GeForce RTX 4090"
    assert_equal "$GPU_BACKEND" "nvidia"
    assert_equal "$GPU_COUNT" "1"
    assert_equal "$GPU_VRAM" "24564"
}

@test "detect_gpu: sums VRAM for multi-GPU and formats display name" {
    if [[ "$(uname -s)" == "Darwin" ]]; then
        skip "detect_gpu uses GNU grep -oP, not available on macOS"
    fi

    mkdir -p "$BATS_TEST_TMPDIR/bin"
    cat > "$BATS_TEST_TMPDIR/bin/nvidia-smi" << 'MOCK'
#!/bin/bash
if [[ "$*" == *"--query-gpu=name,memory.total"* ]]; then
    printf "NVIDIA GeForce RTX 4090, 24564 MiB\nNVIDIA GeForce RTX 4090, 24564 MiB\n"
elif [[ "$*" == *"pci.device_id"* ]]; then
    printf "0x2684\n0x2684\n"
fi
MOCK
    chmod +x "$BATS_TEST_TMPDIR/bin/nvidia-smi"
    export PATH="$BATS_TEST_TMPDIR/bin:$PATH"

    # Mock sysfs vendor ID so detect_gpu trusts nvidia-smi without real hardware
    mkdir -p "$BATS_TEST_TMPDIR/sys/class/drm/card0/device"
    echo "0x10de" > "$BATS_TEST_TMPDIR/sys/class/drm/card0/device/vendor"
    export ODS_DRM_SYS="$BATS_TEST_TMPDIR/sys/class/drm"

    detect_gpu
    assert_equal "$GPU_COUNT" "2"
    assert_equal "$GPU_VRAM" "49128"
    # Two identical GPUs get the "GPU x N" naming format
    [[ "$GPU_NAME" == *"4090"* ]]
    [[ "$GPU_NAME" == *"2"* ]]
}

@test "detect_gpu: returns failure when no GPU found" {
    if [[ "$(uname -s)" == "Darwin" ]]; then
        skip "detect_gpu uses GNU grep -oP, not available on macOS"
    fi

    # Place a broken nvidia-smi on PATH that always fails
    mkdir -p "$BATS_TEST_TMPDIR/bin"
    cat > "$BATS_TEST_TMPDIR/bin/nvidia-smi" << 'MOCK'
#!/bin/bash
exit 1
MOCK
    chmod +x "$BATS_TEST_TMPDIR/bin/nvidia-smi"
    # Also shadow any real nvidia-smi
    export PATH="$BATS_TEST_TMPDIR/bin:$PATH"

    # On a machine with no sysfs AMD GPUs, detect_gpu should fail
    run detect_gpu
    assert_failure
}

# ── detect_gpu AMD APU path ──────────────────────────────────────────────────

_setup_amd_sysfs() {
    local vram_bytes="$1"
    local gtt_bytes="$2"
    local card_dir="$BATS_TEST_TMPDIR/sys/class/drm/card0/device"
    mkdir -p "$card_dir"
    echo "0x1002" > "$card_dir/vendor"
    echo "0x1234" > "$card_dir/device"
    echo "$vram_bytes" > "$card_dir/mem_info_vram_total"
    echo "$gtt_bytes"  > "$card_dir/mem_info_gtt_total"
    export ODS_DRM_SYS="$BATS_TEST_TMPDIR/sys/class/drm"
    # Shadow nvidia-smi so the NVIDIA branch is skipped
    mkdir -p "$BATS_TEST_TMPDIR/bin"
    printf '#!/bin/bash\nexit 1\n' > "$BATS_TEST_TMPDIR/bin/nvidia-smi"
    chmod +x "$BATS_TEST_TMPDIR/bin/nvidia-smi"
    export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
}

@test "detect_gpu: detects AMD APU via small VRAM + large GTT" {
    # Classic APU: 2 GB dedicated VRAM, 32 GB GTT (system RAM pool)
    _setup_amd_sysfs $(( 2 * 1073741824 )) $(( 32 * 1073741824 ))
    detect_gpu
    assert_equal "$GPU_BACKEND"     "amd"
    assert_equal "$GPU_MEMORY_TYPE" "unified"
}

@test "detect_gpu: detects Strix Halo via large GTT alone" {
    # Strix Halo: 8 GB VRAM tile, 96 GB GTT — GTT alone is the signal
    _setup_amd_sysfs $(( 8 * 1073741824 )) $(( 96 * 1073741824 ))
    detect_gpu
    assert_equal "$GPU_BACKEND"     "amd"
    assert_equal "$GPU_MEMORY_TYPE" "unified"
}

@test "amd_gpu_runtime_devices_available: accepts mocked AMD device nodes" {
    export ODS_AMD_DEVICE_ROOT="$BATS_TEST_TMPDIR/dev"
    mkdir -p "$ODS_AMD_DEVICE_ROOT/dri"
    touch "$ODS_AMD_DEVICE_ROOT/kfd"
    touch "$ODS_AMD_DEVICE_ROOT/dri/renderD128"

    run amd_gpu_runtime_devices_available
    assert_success
}

@test "amd_gpu_runtime_devices_available: reports missing AMD device nodes" {
    export ODS_AMD_DEVICE_ROOT="$BATS_TEST_TMPDIR/dev"
    mkdir -p "$ODS_AMD_DEVICE_ROOT/dri"

    run amd_gpu_runtime_devices_available
    assert_failure

    run amd_gpu_missing_devices_csv
    assert_success
    assert_output --partial "$ODS_AMD_DEVICE_ROOT/kfd"
    assert_output --partial "$ODS_AMD_DEVICE_ROOT/dri/renderD*"
}

@test "apply_cpu_gpu_fallback: resets AMD capability state to CPU" {
    GPU_BACKEND="amd"
    GPU_NAME="AMD APU"
    GPU_VRAM=98304
    GPU_COUNT=1
    GPU_MEMORY_TYPE="unified"
    CAP_LLM_BACKEND="amd"
    CAP_RECOMMENDED_TIER="SH_LARGE"
    CAP_COMPOSE_OVERLAYS="docker-compose.base.yml,docker-compose.amd.yml"

    apply_cpu_gpu_fallback "test fallback"

    assert_equal "$GPU_BACKEND" "cpu"
    assert_equal "$GPU_COUNT" "0"
    assert_equal "$GPU_VRAM" "0"
    assert_equal "$GPU_MEMORY_TYPE" "none"
    assert_equal "$CAP_LLM_BACKEND" "cpu"
    assert_equal "$CAP_RECOMMENDED_TIER" ""
    assert_equal "$CAP_COMPOSE_OVERLAYS" ""
}

@test "detect_gpu: does not misidentify discrete AMD GPU as APU" {
    # Future discrete card: 32 GB VRAM, 16 GB GTT — should be classified as
    # a discrete AMD GPU, NOT as an APU/unified-memory device.
    _setup_amd_sysfs $(( 32 * 1073741824 )) $(( 16 * 1073741824 ))
    detect_gpu
    assert_equal "$GPU_BACKEND"     "amd"
    assert_equal "$GPU_MEMORY_TYPE" "discrete"
}
