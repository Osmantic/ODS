#!/usr/bin/env bats
# ============================================================================
# BATS tests for installers/lib/nvidia-topo.sh
# ============================================================================
# Tests: link_rank(), link_label()
# Note: parse_nvidia_topo_matrix() and detect_nvidia_topo() require nvidia-smi
#       and are not tested here.

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    # Stub logging functions that nvidia-topo.sh expects
    warn() { :; }; export -f warn
    err() { :; }; export -f err

    # Source the library under test
    source "$BATS_TEST_DIRNAME/../../installers/lib/nvidia-topo.sh"
}

# ── link_rank: NVLink gen2/3 ────────────────────────────────────────────────

@test "link_rank: NV4 returns 100" {
    run link_rank NV4
    assert_output "100"
}

@test "link_rank: NV6 returns 100" {
    run link_rank NV6
    assert_output "100"
}

@test "link_rank: NV8 returns 100" {
    run link_rank NV8
    assert_output "100"
}

@test "link_rank: NV12 returns 100" {
    run link_rank NV12
    assert_output "100"
}

@test "link_rank: NV18 returns 100" {
    run link_rank NV18
    assert_output "100"
}

# ── link_rank: AMD Infinity Fabric ──────────────────────────────────────────

@test "link_rank: XGMI returns 90" {
    run link_rank XGMI
    assert_output "90"
}

@test "link_rank: XGMI2 returns 90" {
    run link_rank XGMI2
    assert_output "90"
}

# ── link_rank: NVLink gen1 ──────────────────────────────────────────────────

@test "link_rank: NV1 returns 80" {
    run link_rank NV1
    assert_output "80"
}

@test "link_rank: NV2 returns 80" {
    run link_rank NV2
    assert_output "80"
}

@test "link_rank: NV3 returns 80" {
    run link_rank NV3
    assert_output "80"
}

# ── link_rank: MIG ──────────────────────────────────────────────────────────

@test "link_rank: MIG returns 70" {
    run link_rank MIG
    assert_output "70"
}

# ── link_rank: PCIe ─────────────────────────────────────────────────────────

@test "link_rank: PIX returns 50" {
    run link_rank PIX
    assert_output "50"
}

@test "link_rank: PXB returns 40" {
    run link_rank PXB
    assert_output "40"
}

@test "link_rank: PHB returns 30" {
    run link_rank PHB
    assert_output "30"
}

# ── link_rank: NUMA ─────────────────────────────────────────────────────────

@test "link_rank: NODE returns 20" {
    run link_rank NODE
    assert_output "20"
}

@test "link_rank: SYS returns 10" {
    run link_rank SYS
    assert_output "10"
}

@test "link_rank: SOC returns 10" {
    run link_rank SOC
    assert_output "10"
}

# ── link_rank: unknown ──────────────────────────────────────────────────────

@test "link_rank: unknown string returns 0" {
    run link_rank UNKNOWN
    assert_output "0"
}

@test "link_rank: empty string returns 0" {
    run link_rank ""
    assert_output "0"
}

# ── link_rank: ordering consistency ─────────────────────────────────────────

@test "link_rank: ordering is NVLink > XGMI > NV1 > MIG > PIX > PXB > PHB > NODE > SYS > unknown" {
    local nv4 xgmi nv1 mig pix pxb phb node sys unk
    nv4=$(link_rank NV4)
    xgmi=$(link_rank XGMI)
    nv1=$(link_rank NV1)
    mig=$(link_rank MIG)
    pix=$(link_rank PIX)
    pxb=$(link_rank PXB)
    phb=$(link_rank PHB)
    node=$(link_rank NODE)
    sys=$(link_rank SYS)
    unk=$(link_rank UNKNOWN)

    [[ $nv4 -gt $xgmi ]]
    [[ $xgmi -gt $nv1 ]]
    [[ $nv1 -gt $mig ]]
    [[ $mig -gt $pix ]]
    [[ $pix -gt $pxb ]]
    [[ $pxb -gt $phb ]]
    [[ $phb -gt $node ]]
    [[ $node -gt $sys ]]
    [[ $sys -gt $unk ]]
}

# ── link_label: NVLink ──────────────────────────────────────────────────────

@test "link_label: NV4 returns NVLink" {
    run link_label NV4
    assert_output "NVLink"
}

@test "link_label: NV1 returns NVLink" {
    run link_label NV1
    assert_output "NVLink"
}

@test "link_label: NV12 returns NVLink" {
    run link_label NV12
    assert_output "NVLink"
}

# ── link_label: InfinityFabric ──────────────────────────────────────────────

@test "link_label: XGMI returns InfinityFabric" {
    run link_label XGMI
    assert_output "InfinityFabric"
}

@test "link_label: XGMI2 returns InfinityFabric" {
    run link_label XGMI2
    assert_output "InfinityFabric"
}

# ── link_label: MIG ─────────────────────────────────────────────────────────

@test "link_label: MIG returns MIG-SameDie" {
    run link_label MIG
    assert_output "MIG-SameDie"
}

# ── link_label: PCIe variants ───────────────────────────────────────────────

@test "link_label: PIX returns PCIe-SameSwitch" {
    run link_label PIX
    assert_output "PCIe-SameSwitch"
}

@test "link_label: PXB returns PCIe-CrossSwitch" {
    run link_label PXB
    assert_output "PCIe-CrossSwitch"
}

@test "link_label: PHB returns PCIe-HostBridge" {
    run link_label PHB
    assert_output "PCIe-HostBridge"
}

# ── link_label: NUMA ────────────────────────────────────────────────────────

@test "link_label: NODE returns SameNUMA-NoBridge" {
    run link_label NODE
    assert_output "SameNUMA-NoBridge"
}

@test "link_label: SYS returns CrossNUMA" {
    run link_label SYS
    assert_output "CrossNUMA"
}

@test "link_label: SOC returns CrossNUMA" {
    run link_label SOC
    assert_output "CrossNUMA"
}

# ── link_label: Self ────────────────────────────────────────────────────────

@test "link_label: X returns Self" {
    run link_label X
    assert_output "Self"
}

# ── link_label: unknown ─────────────────────────────────────────────────────

@test "link_label: unknown string returns Unknown" {
    run link_label SOMETHING_ELSE
    assert_output "Unknown"
}

@test "link_label: empty string returns Unknown" {
    run link_label ""
    assert_output "Unknown"
}
