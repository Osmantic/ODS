#!/usr/bin/env bash
# ============================================================================
# ODS — AMD Topology Detection Integration Test
# ============================================================================
# Part of: tests/
# Purpose: Test AMD topology detection against fixture files
#
# Usage: ./test-amd-topo.sh
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIXTURES_DIR="$SCRIPT_DIR/fixtures/amd"
TOPO_SCRIPT="$SCRIPT_DIR/../installers/lib/amd-topo.sh"

source "$SCRIPT_DIR/../installers/lib/constants.sh"

# Counters
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# Check dependencies
if ! command -v jq &>/dev/null; then
    echo -e "${RED}ERROR: jq is required but not installed.${NC}" >&2
    echo "Install it: apt-get install -y jq | dnf install -y jq | brew install jq" >&2
    echo "(jq is a hard project dependency — installers/phases/01-preflight.sh auto-installs it on a full run.)" >&2
    exit 1
fi

assert_eq() {
    local label="$1" expected="$2" actual="$3"
    TESTS_RUN=$((TESTS_RUN + 1))
    if [[ "$expected" == "$actual" ]]; then
        echo -e "  ${GRN}✓ $label${NC}"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        echo -e "  ${RED}✗ $label: expected '$expected', got '$actual'${NC}"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
}

assert_gt() {
    local label="$1" value="$2" threshold="$3"
    TESTS_RUN=$((TESTS_RUN + 1))
    if [[ "$value" -gt "$threshold" ]]; then
        echo -e "  ${GRN}✓ $label ($value > $threshold)${NC}"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        echo -e "  ${RED}✗ $label: expected > $threshold, got $value${NC}"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
}

# ── Test: _detect_topo_amdsmi with 4-GPU XGMI fixture ──────────────────────

test_amdsmi_4gpu_xgmi() {
    echo -e "${BLU}Testing: _detect_topo_amdsmi with 4-GPU XGMI${NC}"

    amd-smi() {
        if [[ "$1" == "topology" && "$2" == "--json" ]]; then
            cat "$FIXTURES_DIR/amd_smi_topology_4gpu_xgmi.json"
        fi
    }

    source "$TOPO_SCRIPT"
    local result
    result=$(_detect_topo_amdsmi 4)

    local link_count xgmi_count all_rank_90
    link_count=$(echo "$result" | jq '. | length')
    xgmi_count=$(echo "$result" | jq '[.[] | select(.link_type == "XGMI")] | length')
    all_rank_90=$(echo "$result" | jq '[.[] | select(.rank == 90)] | length')

    assert_eq "6 links (4 choose 2)" "6" "$link_count"
    assert_eq "all links are XGMI" "6" "$xgmi_count"
    assert_eq "all XGMI links have rank 90" "6" "$all_rank_90"

    # Verify specific pairs
    local pair_01 pair_23
    pair_01=$(echo "$result" | jq '[.[] | select(.gpu_a == 0 and .gpu_b == 1)] | length')
    pair_23=$(echo "$result" | jq '[.[] | select(.gpu_a == 2 and .gpu_b == 3)] | length')
    assert_eq "pair 0-1 exists" "1" "$pair_01"
    assert_eq "pair 2-3 exists" "1" "$pair_23"
}

# ── Test: _detect_topo_amdsmi with 2-GPU PCIe fixture ──────────────────────

test_amdsmi_2gpu_pcie() {
    echo -e "${BLU}Testing: _detect_topo_amdsmi with 2-GPU PCIe${NC}"

    amd-smi() {
        if [[ "$1" == "topology" && "$2" == "--json" ]]; then
            cat "$FIXTURES_DIR/amd_smi_topology_2gpu_pcie.json"
        fi
    }

    source "$TOPO_SCRIPT"
    local result
    result=$(_detect_topo_amdsmi 2)

    local link_count link_type link_rank link_label
    link_count=$(echo "$result" | jq '. | length')
    link_type=$(echo "$result" | jq -r '.[0].link_type')
    link_rank=$(echo "$result" | jq '.[0].rank')
    link_label=$(echo "$result" | jq -r '.[0].link_label')

    assert_eq "1 link" "1" "$link_count"
    assert_eq "link type is PCIE" "PCIE" "$link_type"
    assert_eq "rank is 30 (HostBridge, weight=40)" "30" "$link_rank"
    assert_eq "label is PCIe-HostBridge" "PCIe-HostBridge" "$link_label"
}

# ── Test: _detect_topo_rocmsmi with 4-GPU XGMI fixture ─────────────────────

test_rocmsmi_4gpu_xgmi() {
    echo -e "${BLU}Testing: _detect_topo_rocmsmi with 4-GPU XGMI${NC}"

    rocm-smi() {
        if [[ "$1" == "--showtopo" ]]; then
            cat "$FIXTURES_DIR/rocm_smi_showtopo_4gpu_xgmi.txt"
        fi
    }

    source "$TOPO_SCRIPT"
    local result
    result=$(_detect_topo_rocmsmi 4)

    local link_count xgmi_count
    link_count=$(echo "$result" | jq '. | length')
    xgmi_count=$(echo "$result" | jq '[.[] | select(.link_type == "XGMI")] | length')

    assert_eq "6 links" "6" "$link_count"
    assert_eq "all links are XGMI" "6" "$xgmi_count"
}

# ── Test: _detect_topo_rocmsmi with 2-GPU PCIe fixture ─────────────────────

test_rocmsmi_2gpu_pcie() {
    echo -e "${BLU}Testing: _detect_topo_rocmsmi with 2-GPU PCIe${NC}"

    rocm-smi() {
        if [[ "$1" == "--showtopo" ]]; then
            cat "$FIXTURES_DIR/rocm_smi_showtopo_2gpu_pcie.txt"
        fi
    }

    source "$TOPO_SCRIPT"
    local result
    result=$(_detect_topo_rocmsmi 2)

    local link_count link_type
    link_count=$(echo "$result" | jq '. | length')
    link_type=$(echo "$result" | jq -r '.[0].link_type')

    assert_eq "1 link" "1" "$link_count"
    assert_eq "link type is PCIE" "PCIE" "$link_type"
}

# ── Test: amd-smi and rocm-smi produce same topology ──────────────────────

test_amdsmi_rocmsmi_agreement() {
    echo -e "${BLU}Testing: amd-smi and rocm-smi agree on 4-GPU XGMI topology${NC}"

    amd-smi() {
        if [[ "$1" == "topology" && "$2" == "--json" ]]; then
            cat "$FIXTURES_DIR/amd_smi_topology_4gpu_xgmi.json"
        fi
    }
    rocm-smi() {
        if [[ "$1" == "--showtopo" ]]; then
            cat "$FIXTURES_DIR/rocm_smi_showtopo_4gpu_xgmi.txt"
        fi
    }

    source "$TOPO_SCRIPT"
    local amdsmi_result rocmsmi_result
    amdsmi_result=$(_detect_topo_amdsmi 4)
    rocmsmi_result=$(_detect_topo_rocmsmi 4)

    local amdsmi_count rocmsmi_count
    amdsmi_count=$(echo "$amdsmi_result" | jq '. | length')
    rocmsmi_count=$(echo "$rocmsmi_result" | jq '. | length')
    assert_eq "same link count" "$amdsmi_count" "$rocmsmi_count"

    local amdsmi_xgmi rocmsmi_xgmi
    amdsmi_xgmi=$(echo "$amdsmi_result" | jq '[.[] | select(.link_type == "XGMI")] | length')
    rocmsmi_xgmi=$(echo "$rocmsmi_result" | jq '[.[] | select(.link_type == "XGMI")] | length')
    assert_eq "same XGMI count" "$amdsmi_xgmi" "$rocmsmi_xgmi"
}

# ── Test: field-based jq select vs positional indexing ─────────────────────

test_field_based_select() {
    echo -e "${BLU}Testing: field-based select handles reordered links correctly${NC}"

    # GPU 0's links are in order: SELF, GPU2(XGMI), GPU1(PCIE) — not sorted by GPU index
    amd-smi() {
        if [[ "$1" == "topology" && "$2" == "--json" ]]; then
            cat <<'EOF'
[
  {"gpu": 0, "bdf": "0:0", "links": [
    {"gpu": 0, "weight": 0, "link_type": "SELF"},
    {"gpu": 2, "weight": 15, "link_type": "XGMI"},
    {"gpu": 1, "weight": 40, "link_type": "PCIE"}
  ]},
  {"gpu": 1, "bdf": "0:1", "links": [
    {"gpu": 1, "weight": 0, "link_type": "SELF"},
    {"gpu": 0, "weight": 40, "link_type": "PCIE"},
    {"gpu": 2, "weight": 15, "link_type": "XGMI"}
  ]},
  {"gpu": 2, "bdf": "0:2", "links": [
    {"gpu": 2, "weight": 0, "link_type": "SELF"},
    {"gpu": 0, "weight": 15, "link_type": "XGMI"},
    {"gpu": 1, "weight": 15, "link_type": "XGMI"}
  ]}
]
EOF
        fi
    }

    source "$TOPO_SCRIPT"
    local result
    result=$(_detect_topo_amdsmi 3)

    # With positional indexing, 0→1 would look at links[1] which is GPU2(XGMI) — WRONG
    # With field-based select, 0→1 correctly finds GPU1(PCIE)
    local link_01_type link_02_type
    link_01_type=$(echo "$result" | jq -r '.[] | select(.gpu_a == 0 and .gpu_b == 1) | .link_type')
    link_02_type=$(echo "$result" | jq -r '.[] | select(.gpu_a == 0 and .gpu_b == 2) | .link_type')

    assert_eq "0→1 is PCIE (not XGMI)" "PCIE" "$link_01_type"
    assert_eq "0→2 is XGMI" "XGMI" "$link_02_type"
}

# ── Test: GFX version parsing from rocm-smi ────────────────────────────────

test_gfx_version_parsing() {
    echo -e "${BLU}Testing: amd_gfx_version parses per-GPU GFX version${NC}"

    rocm-smi() {
        cat "$FIXTURES_DIR/rocm_smi_productname_4gpu_mi300x.txt"
    }
    # Disable amd-smi fallback
    amd-smi() { return 1; }

    source "$TOPO_SCRIPT"

    local gfx0 gfx1 gfx2 gfx3
    gfx0=$(amd_gfx_version "/fake" 0)
    gfx1=$(amd_gfx_version "/fake" 1)
    gfx2=$(amd_gfx_version "/fake" 2)
    gfx3=$(amd_gfx_version "/fake" 3)

    assert_eq "GPU[0] gfx942" "gfx942" "$gfx0"
    assert_eq "GPU[1] gfx942" "gfx942" "$gfx1"
    assert_eq "GPU[2] gfx942" "gfx942" "$gfx2"
    assert_eq "GPU[3] gfx942" "gfx942" "$gfx3"
}

# ── Test: GFX version parsing with mixed versions ──────────────────────────

test_gfx_version_mixed() {
    echo -e "${BLU}Testing: amd_gfx_version handles mixed GPU versions${NC}"

    rocm-smi() {
        echo "============================ ROCm System Management Interface ============================"
        echo "====================================== Product Info ======================================"
        echo "GPU[0]		: GFX Version: 		gfx1100"
        echo "GPU[1]		: GFX Version: 		gfx1101"
        echo "==========================================================================================="
    }
    amd-smi() { return 1; }

    source "$TOPO_SCRIPT"

    local gfx0 gfx1
    gfx0=$(amd_gfx_version "/fake" 0)
    gfx1=$(amd_gfx_version "/fake" 1)

    assert_eq "GPU[0] gfx1100" "gfx1100" "$gfx0"
    assert_eq "GPU[1] gfx1101" "gfx1101" "$gfx1"
}

# ── Main test runner ───────────────────────────────────────────────────────

echo -e "${MAG}=== AMD Topology Detection Tests ===${NC}\n"

test_amdsmi_4gpu_xgmi
echo
test_amdsmi_2gpu_pcie
echo
test_rocmsmi_4gpu_xgmi
echo
test_rocmsmi_2gpu_pcie
echo
test_amdsmi_rocmsmi_agreement
echo
test_field_based_select
echo
test_gfx_version_parsing
echo
test_gfx_version_mixed

echo -e "\n${MAG}=== Test Summary ===${NC}"
echo -e "Tests run:    $TESTS_RUN"
echo -e "${GRN}Tests passed: $TESTS_PASSED${NC}"
if [[ $TESTS_FAILED -gt 0 ]]; then
    echo -e "${RED}Tests failed: $TESTS_FAILED${NC}"
    exit 1
else
    echo -e "${GRN}All tests passed!${NC}"
    exit 0
fi
