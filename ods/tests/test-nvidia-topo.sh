#!/usr/bin/env bash
# ============================================================================
# ODS — NVIDIA Topology Detection Test
# ============================================================================
# Part of: tests/
# Purpose: Test NVIDIA topology detection against fixture files
#
# Usage: ./test-nvidia-topo.sh
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIXTURES_DIR="$SCRIPT_DIR/fixtures/topology_matrix"
TOPO_SCRIPT="$SCRIPT_DIR/../installers/lib/nvidia-topo.sh"

source "$SCRIPT_DIR/../installers/lib/constants.sh"

# Counters
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# Check dependencies
if ! command -v jq &>/dev/null; then
    echo -e "${RED}ERROR: jq is required but not installed${NC}"
    exit 1
fi

# Test fixture: nvidia_smi_topo_matrix_1gpu_pcie.txt
test_1gpu_pcie() {
    echo -e "${BLU}Testing: nvidia_smi_topo_matrix_1gpu_pcie.txt${NC}"
    TESTS_RUN=$((TESTS_RUN + 1))
    
    nvidia-smi() {
        if [[ "$1" == "topo" && "$2" == "-m" ]]; then
            cat "$FIXTURES_DIR/nvidia_smi_topo_matrix_1gpu_pcie.txt"
        elif [[ "$*" == "--query-gpu=index,name,memory.total,memory.free,pcie.link.gen.current,pcie.link.width.current,uuid --format=csv,noheader,nounits" ]]; then
            echo "0, NVIDIA RTX 4090, 24564, 24564, 4, 16, GPU-12345678-1234-1234-1234-123456789012"
        elif [[ "$*" == "--query-gpu=driver_version --format=csv,noheader" ]]; then
            echo "535.129.03"
        elif [[ "$1" == "-q" ]]; then
            echo "MIG Mode: Disabled"
        fi
    }
    
    source "$TOPO_SCRIPT"
    local result=$(detect_nvidia_topo)
    
    # Assertions
    local gpu_count=$(echo "$result" | jq -r '.gpu_count')
    local links_count=$(echo "$result" | jq -r '.links | length')
    
    if [[ "$gpu_count" == "1" ]] && [[ "$links_count" == "0" ]]; then
        echo -e "${GRN}✓ PASS: 1 GPU, 0 links${NC}"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        echo -e "${RED}✗ FAIL: Expected 1 GPU and 0 links, got $gpu_count GPUs and $links_count links${NC}"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
}

# Test fixture: nvidia_smi_topo_matrix_4gpus_soc.txt
test_4gpus_soc() {
    echo -e "${BLU}Testing: nvidia_smi_topo_matrix_4gpus_soc.txt${NC}"
    TESTS_RUN=$((TESTS_RUN + 1))
    
    nvidia-smi() {
        if [[ "$1" == "topo" && "$2" == "-m" ]]; then
            cat "$FIXTURES_DIR/nvidia_smi_topo_matrix_4gpus_soc.txt"
        elif [[ "$*" == "--query-gpu=index,name,memory.total,memory.free,pcie.link.gen.current,pcie.link.width.current,uuid --format=csv,noheader,nounits" ]]; then
            echo "0, NVIDIA A100-SXM4-80GB, 81920, 81920, 4, 16, GPU-00000000-0000-0000-0000-000000000000"
            echo "1, NVIDIA A100-SXM4-80GB, 81920, 81920, 4, 16, GPU-11111111-1111-1111-1111-111111111111"
            echo "2, NVIDIA A100-SXM4-80GB, 81920, 81920, 4, 16, GPU-22222222-2222-2222-2222-222222222222"
            echo "3, NVIDIA A100-SXM4-80GB, 81920, 81920, 4, 16, GPU-33333333-3333-3333-3333-333333333333"
        elif [[ "$*" == "--query-gpu=driver_version --format=csv,noheader" ]]; then
            echo "535.129.03"
        elif [[ "$1" == "-q" ]]; then
            echo "MIG Mode: Disabled"
        fi
    }
    
    source "$TOPO_SCRIPT"
    local result=$(detect_nvidia_topo)
    
    # Assertions
    local gpu_count=$(echo "$result" | jq -r '.gpu_count')
    local links_count=$(echo "$result" | jq -r '.links | length')
    local has_soc=$(echo "$result" | jq -r '.links[] | select(.link_type == "SOC") | .link_type' | head -1)
    
    if [[ "$gpu_count" == "4" ]] && [[ "$links_count" -gt "0" ]] && [[ "$has_soc" == "SOC" ]]; then
        echo -e "${GRN}✓ PASS: 4 GPUs, $links_count links, SOC topology detected${NC}"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        echo -e "${RED}✗ FAIL: Expected 4 GPUs with SOC links, got $gpu_count GPUs, $links_count links${NC}"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
}

# Test fixture: nvidia_smi_topo_matrix_4gpus_sys_separated_nv_pairs.txt
test_4gpus_sys_separated_nv_pairs() {
    echo -e "${BLU}Testing: nvidia_smi_topo_matrix_4gpus_sys_separated_nv_pairs.txt${NC}"
    TESTS_RUN=$((TESTS_RUN + 1))
    
    nvidia-smi() {
        if [[ "$1" == "topo" && "$2" == "-m" ]]; then
            cat "$FIXTURES_DIR/nvidia_smi_topo_matrix_4gpus_sys_separated_nv_pairs.txt"
        elif [[ "$*" == "--query-gpu=index,name,memory.total,memory.free,pcie.link.gen.current,pcie.link.width.current,uuid --format=csv,noheader,nounits" ]]; then
            echo "0, NVIDIA A100-SXM4-80GB, 81920, 81920, 4, 16, GPU-00000000-0000-0000-0000-000000000000"
            echo "1, NVIDIA A100-SXM4-80GB, 81920, 81920, 4, 16, GPU-11111111-1111-1111-1111-111111111111"
            echo "2, NVIDIA A100-SXM4-80GB, 81920, 81920, 4, 16, GPU-22222222-2222-2222-2222-222222222222"
            echo "3, NVIDIA A100-SXM4-80GB, 81920, 81920, 4, 16, GPU-33333333-3333-3333-3333-333333333333"
        elif [[ "$*" == "--query-gpu=driver_version --format=csv,noheader" ]]; then
            echo "535.129.03"
        elif [[ "$1" == "-q" ]]; then
            echo "MIG Mode: Disabled"
        fi
    }
    
    source "$TOPO_SCRIPT"
    local result=$(detect_nvidia_topo)
    
    # Assertions
    local gpu_count=$(echo "$result" | jq -r '.gpu_count')
    local nvlink_count=$(echo "$result" | jq -r '[.links[] | select(.link_type | startswith("NV"))] | length')
    
    if [[ "$gpu_count" == "4" ]] && [[ "$nvlink_count" -gt "0" ]]; then
        echo -e "${GRN}✓ PASS: 4 GPUs, $nvlink_count NVLink connections${NC}"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        echo -e "${RED}✗ FAIL: Expected 4 GPUs with NVLink, got $gpu_count GPUs, $nvlink_count NVLinks${NC}"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
}

# Test fixture: nvidia_smi_topo_matrix_5gpus_nv12_with_mlx5.txt
test_5gpus_nv12_with_mlx5() {
    echo -e "${BLU}Testing: nvidia_smi_topo_matrix_5gpus_nv12_with_mlx5.txt${NC}"
    TESTS_RUN=$((TESTS_RUN + 1))
    
    nvidia-smi() {
        if [[ "$1" == "topo" && "$2" == "-m" ]]; then
            cat "$FIXTURES_DIR/nvidia_smi_topo_matrix_5gpus_nv12_with_mlx5.txt"
        elif [[ "$*" == "--query-gpu=index,name,memory.total,memory.free,pcie.link.gen.current,pcie.link.width.current,uuid --format=csv,noheader,nounits" ]]; then
            echo "0, NVIDIA A100-SXM4-80GB, 81920, 81920, 4, 16, GPU-00000000-0000-0000-0000-000000000000"
            echo "1, NVIDIA A100-SXM4-80GB, 81920, 81920, 4, 16, GPU-11111111-1111-1111-1111-111111111111"
            echo "2, NVIDIA A100-SXM4-80GB, 81920, 81920, 4, 16, GPU-22222222-2222-2222-2222-222222222222"
            echo "3, NVIDIA A100-SXM4-80GB, 81920, 81920, 4, 16, GPU-33333333-3333-3333-3333-333333333333"
            echo "4, NVIDIA A100-SXM4-80GB, 81920, 81920, 4, 16, GPU-44444444-4444-4444-4444-444444444444"
        elif [[ "$*" == "--query-gpu=driver_version --format=csv,noheader" ]]; then
            echo "535.129.03"
        elif [[ "$1" == "-q" ]]; then
            echo "MIG Mode: Disabled"
        fi
    }
    
    source "$TOPO_SCRIPT"
    local result=$(detect_nvidia_topo)
    
    # Assertions
    local gpu_count=$(echo "$result" | jq -r '.gpu_count')
    local nv12_count=$(echo "$result" | jq -r '[.links[] | select(.link_type == "NV12")] | length')
    
    if [[ "$gpu_count" == "5" ]] && [[ "$nv12_count" -gt "0" ]]; then
        echo -e "${GRN}✓ PASS: 5 GPUs, $nv12_count NV12 connections${NC}"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        echo -e "${RED}✗ FAIL: Expected 5 GPUs with NV12, got $gpu_count GPUs, $nv12_count NV12 links${NC}"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
}

# Test fixture: nvidia_smi_topo_matrix_8gpus_nv12_full_mesh.txt
test_8gpus_nv12_full_mesh() {
    echo -e "${BLU}Testing: nvidia_smi_topo_matrix_8gpus_nv12_full_mesh.txt${NC}"
    TESTS_RUN=$((TESTS_RUN + 1))
    
    nvidia-smi() {
        if [[ "$1" == "topo" && "$2" == "-m" ]]; then
            cat "$FIXTURES_DIR/nvidia_smi_topo_matrix_8gpus_nv12_full_mesh.txt"
        elif [[ "$*" == "--query-gpu=index,name,memory.total,memory.free,pcie.link.gen.current,pcie.link.width.current,uuid --format=csv,noheader,nounits" ]]; then
            for i in {0..7}; do
                echo "$i, NVIDIA A100-SXM4-80GB, 81920, 81920, 4, 16, GPU-${i}${i}${i}${i}${i}${i}${i}${i}-${i}${i}${i}${i}-${i}${i}${i}${i}-${i}${i}${i}${i}-${i}${i}${i}${i}${i}${i}${i}${i}${i}${i}${i}${i}"
            done
        elif [[ "$*" == "--query-gpu=driver_version --format=csv,noheader" ]]; then
            echo "535.129.03"
        elif [[ "$1" == "-q" ]]; then
            echo "MIG Mode: Disabled"
        fi
    }
    
    source "$TOPO_SCRIPT"
    local result=$(detect_nvidia_topo)
    
    # Assertions
    local gpu_count=$(echo "$result" | jq -r '.gpu_count')
    local nv12_count=$(echo "$result" | jq -r '[.links[] | select(.link_type == "NV12")] | length')
    
    # Full mesh of 8 GPUs should have 28 links (8*7/2)
    if [[ "$gpu_count" == "8" ]] && [[ "$nv12_count" -gt "20" ]]; then
        echo -e "${GRN}✓ PASS: 8 GPUs, $nv12_count NV12 connections (full mesh)${NC}"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        echo -e "${RED}✗ FAIL: Expected 8 GPUs with full mesh NV12, got $gpu_count GPUs, $nv12_count NV12 links${NC}"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
}

# Test fixture: nvidia_smi_topo_matrix_8gpus_nv12_full_mesh_with_numa_id.txt
test_8gpus_nv12_full_mesh_with_numa() {
    echo -e "${BLU}Testing: nvidia_smi_topo_matrix_8gpus_nv12_full_mesh_with_numa_id.txt${NC}"
    TESTS_RUN=$((TESTS_RUN + 1))
    
    nvidia-smi() {
        if [[ "$1" == "topo" && "$2" == "-m" ]]; then
            cat "$FIXTURES_DIR/nvidia_smi_topo_matrix_8gpus_nv12_full_mesh_with_numa_id.txt"
        elif [[ "$*" == "--query-gpu=index,name,memory.total,memory.free,pcie.link.gen.current,pcie.link.width.current,uuid --format=csv,noheader,nounits" ]]; then
            for i in {0..7}; do
                echo "$i, NVIDIA A100-SXM4-80GB, 81920, 81920, 4, 16, GPU-${i}${i}${i}${i}${i}${i}${i}${i}-${i}${i}${i}${i}-${i}${i}${i}${i}-${i}${i}${i}${i}-${i}${i}${i}${i}${i}${i}${i}${i}${i}${i}${i}${i}"
            done
        elif [[ "$*" == "--query-gpu=driver_version --format=csv,noheader" ]]; then
            echo "535.129.03"
        elif [[ "$1" == "-q" ]]; then
            echo "MIG Mode: Disabled"
        fi
    }
    
    numactl() {
        if [[ "$1" == "--hardware" ]]; then
            echo "node 0 cpus: 0 1 2 3"
            echo "node 1 cpus: 4 5 6 7"
        fi
    }
    
    source "$TOPO_SCRIPT"
    local result=$(detect_nvidia_topo)
    
    # Assertions
    local gpu_count=$(echo "$result" | jq -r '.gpu_count')
    local numa_nodes=$(echo "$result" | jq -r '.numa.nodes')
    local nv12_count=$(echo "$result" | jq -r '[.links[] | select(.link_type == "NV12")] | length')
    
    if [[ "$gpu_count" == "8" ]] && [[ "$numa_nodes" == "2" ]] && [[ "$nv12_count" -gt "20" ]]; then
        echo -e "${GRN}✓ PASS: 8 GPUs, 2 NUMA nodes, $nv12_count NV12 connections${NC}"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        echo -e "${RED}✗ FAIL: Expected 8 GPUs, 2 NUMA nodes with NV12, got $gpu_count GPUs, $numa_nodes NUMA nodes, $nv12_count NV12 links${NC}"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
}

# Test fixture: nvidia_smi_topo_matrix_8gpus_nv1_nv2_partial_mesh.txt
test_8gpus_nv1_nv2_partial_mesh() {
    echo -e "${BLU}Testing: nvidia_smi_topo_matrix_8gpus_nv1_nv2_partial_mesh.txt${NC}"
    TESTS_RUN=$((TESTS_RUN + 1))
    
    nvidia-smi() {
        if [[ "$1" == "topo" && "$2" == "-m" ]]; then
            cat "$FIXTURES_DIR/nvidia_smi_topo_matrix_8gpus_nv1_nv2_partial_mesh.txt"
        elif [[ "$*" == "--query-gpu=index,name,memory.total,memory.free,pcie.link.gen.current,pcie.link.width.current,uuid --format=csv,noheader,nounits" ]]; then
            for i in {0..7}; do
                echo "$i, NVIDIA V100-SXM2-32GB, 32768, 32768, 3, 16, GPU-${i}${i}${i}${i}${i}${i}${i}${i}-${i}${i}${i}${i}-${i}${i}${i}${i}-${i}${i}${i}${i}-${i}${i}${i}${i}${i}${i}${i}${i}${i}${i}${i}${i}"
            done
        elif [[ "$*" == "--query-gpu=driver_version --format=csv,noheader" ]]; then
            echo "535.129.03"
        elif [[ "$1" == "-q" ]]; then
            echo "MIG Mode: Disabled"
        fi
    }
    
    source "$TOPO_SCRIPT"
    local result=$(detect_nvidia_topo)
    
    # Assertions
    local gpu_count=$(echo "$result" | jq -r '.gpu_count')
    local nvlink_count=$(echo "$result" | jq -r '[.links[] | select(.link_type | startswith("NV"))] | length')
    
    if [[ "$gpu_count" == "8" ]] && [[ "$nvlink_count" -gt "0" ]]; then
        echo -e "${GRN}✓ PASS: 8 GPUs, $nvlink_count NVLink connections (partial mesh)${NC}"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        echo -e "${RED}✗ FAIL: Expected 8 GPUs with NVLink, got $gpu_count GPUs, $nvlink_count NVLink connections${NC}"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
}

# Main test runner
echo -e "${MAG}=== NVIDIA Topology Detection Tests ===${NC}\n"

test_1gpu_pcie
test_4gpus_soc
test_4gpus_sys_separated_nv_pairs
test_5gpus_nv12_with_mlx5
test_8gpus_nv12_full_mesh
test_8gpus_nv12_full_mesh_with_numa
test_8gpus_nv1_nv2_partial_mesh

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
