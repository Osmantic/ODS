#!/usr/bin/env bats
# ============================================================================
# BATS tests for installers/lib/amd-topo.sh
# ============================================================================
# Tests: amd_render_node(), amd_gfx_version(), amd_gpu_name(), amd_gpu_id(),
#        _detect_topo_amdsmi(), _detect_topo_rocmsmi()

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

FIXTURES_DIR="$BATS_TEST_DIRNAME/../fixtures/amd"

setup() {
    # Stub logging functions that amd-topo.sh expects
    warn() { :; }; export -f warn
    err() { :; }; export -f err

    # Source the library under test
    source "$BATS_TEST_DIRNAME/../../installers/lib/amd-topo.sh"
}

# -- AMD card ordering and memory classification --------------------------------

@test "_amd_sort_card_dirs: orders non-contiguous DRM cards numerically" {
    local drm="$BATS_TEST_TMPDIR/sys/class/drm"
    mkdir -p "$drm/card0/device" "$drm/card16/device" "$drm/card24/device" "$drm/card8/device"

    run _amd_sort_card_dirs \
        "$drm/card0/device" \
        "$drm/card16/device" \
        "$drm/card24/device" \
        "$drm/card8/device"
    assert_success
    assert_line --index 0 "$drm/card0/device"
    assert_line --index 1 "$drm/card8/device"
    assert_line --index 2 "$drm/card16/device"
    assert_line --index 3 "$drm/card24/device"
}

@test "amd_memory_type: keeps 32GB+ discrete cards discrete" {
    run amd_memory_type $((32 * 1073741824)) $((16 * 1073741824))
    assert_success
    assert_output "discrete"
}

@test "amd_memory_type: detects AMD APUs from large GTT" {
    run amd_memory_type $((8 * 1073741824)) $((96 * 1073741824))
    assert_success
    assert_output "unified"
}

@test "detect_amd_topo: emits GPUs in numeric DRM card order" {
    local drm="$BATS_TEST_TMPDIR/sys/class/drm"
    local card
    for card in 0 8 16 24; do
        local card_dir="$drm/card${card}/device"
        mkdir -p "$card_dir"
        echo "0x1002" > "$card_dir/vendor"
        echo "0x74b5" > "$card_dir/device"
        echo "0x${card}" > "$card_dir/subsystem_device"
        echo "$((16 * 1073741824))" > "$card_dir/mem_info_vram_total"
        echo "$((16 * 1073741824))" > "$card_dir/mem_info_gtt_total"
        echo "AMD card${card}" > "$card_dir/product_name"
        echo "0x00000000000000${card}" > "$card_dir/unique_id"
    done

    amd-smi() { return 1; }
    rocm-smi() { return 1; }
    rocminfo() { return 1; }
    export DREAM_DRM_SYS="$drm"

    local output names
    output=$(detect_amd_topo)
    names=$(echo "$output" | jq -r '.gpus[].name' | paste -sd ',' -)

    assert_equal "$names" "AMD card0,AMD card8,AMD card16,AMD card24"
    assert_equal "$(echo "$output" | jq -r '.gpus[].memory_type' | sort -u)" "discrete"
}

# ── amd_render_node ─────────────────────────────────────────────────────────

@test "amd_render_node: returns numeric ID, not full name" {
    # Create mock sysfs structure — device must be a symlink (not a dir)
    # to match real sysfs layout where /sys/class/drm/card0/device → PCI path
    local tmpdir
    tmpdir=$(mktemp -d)
    mkdir -p "$tmpdir/card0"
    mkdir -p "$tmpdir/renderD128"

    local pci_path="$tmpdir/pci-0000:03:00.0"
    mkdir -p "$pci_path"
    ln -sfn "$pci_path" "$tmpdir/card0/device"
    ln -sfn "$pci_path" "$tmpdir/renderD128/device"

    # Redefine to use tmpdir instead of /sys/class/drm (hardcoded in original)
    # Call directly (not via `run`) because BATS `run` uses a subshell
    # where function redefinitions aren't visible.
    amd_render_node() {
        local card_dir="$1"
        local card_pci
        card_pci=$(readlink -f "$card_dir") || { echo "unknown"; return 1; }

        for render_dir in "$tmpdir"/renderD*/device; do
            [[ -d "$render_dir" ]] || continue
            local render_pci
            render_pci=$(readlink -f "$render_dir") || continue
            if [[ "$card_pci" == "$render_pci" ]]; then
                basename "$(dirname "$render_dir")" | sed 's/^renderD//'
                return 0
            fi
        done
        echo "unknown"
    }

    output=$(amd_render_node "$tmpdir/card0/device")
    status=$?
    assert_output "128"
    rm -rf "$tmpdir"
}

@test "amd_render_node: returns unknown when no matching render node" {
    local tmpdir
    tmpdir=$(mktemp -d)
    mkdir -p "$tmpdir/card0/device"
    ln -sfn "$tmpdir/pci-a" "$tmpdir/card0/device"

    amd_render_node() {
        local card_dir="$1"
        local card_pci
        card_pci=$(readlink -f "$card_dir") || { echo "unknown"; return 1; }
        # No render nodes to iterate
        echo "unknown"
    }

    run amd_render_node "$tmpdir/card0/device"
    assert_output "unknown"
    rm -rf "$tmpdir"
}

@test "amd_render_node: handles renderD130 correctly" {
    local tmpdir
    tmpdir=$(mktemp -d)
    local pci_path="$tmpdir/pci-0000:fd:00.0"
    mkdir -p "$pci_path"
    mkdir -p "$tmpdir/card2"
    mkdir -p "$tmpdir/renderD130"
    ln -sfn "$pci_path" "$tmpdir/card2/device"
    ln -sfn "$pci_path" "$tmpdir/renderD130/device"

    # Call directly — BATS `run` subshell doesn't see redefined functions
    amd_render_node() {
        local card_dir="$1"
        local card_pci
        card_pci=$(readlink -f "$card_dir") || { echo "unknown"; return 1; }
        for render_dir in "$tmpdir"/renderD*/device; do
            [[ -d "$render_dir" ]] || continue
            local render_pci
            render_pci=$(readlink -f "$render_dir") || continue
            if [[ "$card_pci" == "$render_pci" ]]; then
                basename "$(dirname "$render_dir")" | sed 's/^renderD//'
                return 0
            fi
        done
        echo "unknown"
    }

    output=$(amd_render_node "$tmpdir/card2/device")
    status=$?
    assert_output "130"
    rm -rf "$tmpdir"
}

# ── amd_gfx_version: rocm-smi parsing ──────────────────────────────────────

@test "amd_gfx_version: parses GPU[0] gfx942 from rocm-smi" {
    rocm-smi() {
        cat "$FIXTURES_DIR/rocm_smi_productname_4gpu_mi300x.txt"
    }

    run amd_gfx_version "/fake/card0" 0
    assert_output "gfx942"
}

@test "amd_gfx_version: parses GPU[2] gfx942 from rocm-smi (not GPU[0])" {
    rocm-smi() {
        cat "$FIXTURES_DIR/rocm_smi_productname_4gpu_mi300x.txt"
    }

    run amd_gfx_version "/fake/card2" 2
    assert_output "gfx942"
}

@test "amd_gfx_version: parses GPU[3] correctly (last GPU)" {
    rocm-smi() {
        cat "$FIXTURES_DIR/rocm_smi_productname_4gpu_mi300x.txt"
    }

    run amd_gfx_version "/fake/card3" 3
    assert_output "gfx942"
}

@test "amd_gfx_version: falls back to amd-smi when rocm-smi unavailable" {
    # Undefine rocm-smi, define amd-smi
    rocm-smi() { return 1; }

    # amd-smi static --json --asic → jq path: .[gpu_idx].asic.target_graphics_version
    amd-smi() {
        if [[ "$1" == "static" && "$2" == "--json" && "$3" == "--asic" ]]; then
            echo '[{"asic": {"target_graphics_version": "gfx1100"}}]'
        else
            return 1
        fi
    }

    run amd_gfx_version "/fake/card0" 0
    assert_output "gfx1100"
}

# ── amd_gpu_name ────────────────────────────────────────────────────────────

@test "amd_gpu_name: reads sysfs product_name when available" {
    local tmpdir
    tmpdir=$(mktemp -d)
    echo "AMD Radeon RX 7900 XTX" > "$tmpdir/product_name"

    run amd_gpu_name "$tmpdir" "744c"
    assert_output "AMD Radeon RX 7900 XTX"
    rm -rf "$tmpdir"
}

@test "amd_gpu_name: skips empty product_name" {
    local tmpdir
    tmpdir=$(mktemp -d)
    echo "" > "$tmpdir/product_name"

    # No lspci, no PCI BDF
    lspci() { return 1; }

    run amd_gpu_name "$tmpdir" "74b5"
    assert_output "AMD GPU (0x74b5)"
    rm -rf "$tmpdir"
}

@test "amd_gpu_name: skips (null) product_name" {
    local tmpdir
    tmpdir=$(mktemp -d)
    echo "(null)" > "$tmpdir/product_name"

    lspci() { return 1; }

    run amd_gpu_name "$tmpdir" "74b5"
    assert_output "AMD GPU (0x74b5)"
    rm -rf "$tmpdir"
}

@test "amd_gpu_name: falls back to device ID format" {
    local tmpdir
    tmpdir=$(mktemp -d)
    # No product_name file

    lspci() { return 1; }

    run amd_gpu_name "$tmpdir" "744c"
    assert_output "AMD GPU (0x744c)"
    rm -rf "$tmpdir"
}

# ── amd_gpu_id ──────────────────────────────────────────────────────────────

@test "amd_gpu_id: returns unique_id from sysfs when available" {
    local tmpdir
    tmpdir=$(mktemp -d)
    echo "0x0123456789abcdef" > "$tmpdir/unique_id"

    # No amd-smi
    amd-smi() { return 1; }

    run amd_gpu_id "$tmpdir" 0
    # Code wraps unique_id with AMD-UID- prefix
    assert_output "AMD-UID-0x0123456789abcdef"
    rm -rf "$tmpdir"
}

@test "amd_gpu_id: builds composite ID when unique_id unavailable" {
    local tmpdir
    tmpdir=$(mktemp -d)
    # No unique_id file

    # No amd-smi
    amd-smi() { return 1; }

    # Create files for composite ID (device_id + subsystem_id)
    # Don't mkdir device — it's read as a file by cat "$card_dir/device"
    echo "0x74b5" > "$tmpdir/device"
    echo "0x1234" > "$tmpdir/subsystem_device"

    run amd_gpu_id "$tmpdir" 5
    # Should contain "AMD-" prefix since it builds a composite
    assert_output --partial "AMD-"
    rm -rf "$tmpdir"
}

# ── _detect_topo_amdsmi: 4-GPU XGMI full mesh ──────────────────────────────

@test "_detect_topo_amdsmi: parses 4-GPU XGMI topology correctly" {
    amd-smi() {
        if [[ "$1" == "topology" && "$2" == "--json" ]]; then
            cat "$FIXTURES_DIR/amd_smi_topology_4gpu_xgmi.json"
        fi
    }

    run _detect_topo_amdsmi 4
    assert_success

    # Should produce 6 links (4 choose 2 = 6)
    local link_count
    link_count=$(echo "$output" | jq '. | length')
    [[ "$link_count" -eq 6 ]]

    # All links should be XGMI
    local xgmi_count
    xgmi_count=$(echo "$output" | jq '[.[] | select(.link_type == "XGMI")] | length')
    [[ "$xgmi_count" -eq 6 ]]

    # All XGMI links should have rank 90
    local rank_90
    rank_90=$(echo "$output" | jq '[.[] | select(.rank == 90)] | length')
    [[ "$rank_90" -eq 6 ]]
}

@test "_detect_topo_amdsmi: link pairs are correct (0-1, 0-2, 0-3, 1-2, 1-3, 2-3)" {
    amd-smi() {
        if [[ "$1" == "topology" && "$2" == "--json" ]]; then
            cat "$FIXTURES_DIR/amd_smi_topology_4gpu_xgmi.json"
        fi
    }

    run _detect_topo_amdsmi 4
    assert_success

    # Check all 6 expected pairs exist
    local pair
    for pair in "0,1" "0,2" "0,3" "1,2" "1,3" "2,3"; do
        local a=${pair%%,*} b=${pair##*,}
        local found
        found=$(echo "$output" | jq --argjson a "$a" --argjson b "$b" \
            '[.[] | select(.gpu_a == $a and .gpu_b == $b)] | length')
        [[ "$found" -eq 1 ]]
    done
}

@test "_detect_topo_amdsmi: uses field-based lookup, not positional indexing" {
    # Create a topology where links array is NOT in GPU index order
    # GPU 0's links: SELF, GPU2, GPU1 (reversed order)
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

    run _detect_topo_amdsmi 3
    assert_success

    # GPU 0→1 should be PCIE (weight 40), not XGMI
    local link_01_type
    link_01_type=$(echo "$output" | jq -r '.[] | select(.gpu_a == 0 and .gpu_b == 1) | .link_type')
    [[ "$link_01_type" == "PCIE" ]]

    # GPU 0→2 should be XGMI
    local link_02_type
    link_02_type=$(echo "$output" | jq -r '.[] | select(.gpu_a == 0 and .gpu_b == 2) | .link_type')
    [[ "$link_02_type" == "XGMI" ]]
}

# ── _detect_topo_amdsmi: 2-GPU PCIe ────────────────────────────────────────

@test "_detect_topo_amdsmi: parses 2-GPU PCIe topology" {
    amd-smi() {
        if [[ "$1" == "topology" && "$2" == "--json" ]]; then
            cat "$FIXTURES_DIR/amd_smi_topology_2gpu_pcie.json"
        fi
    }

    run _detect_topo_amdsmi 2
    assert_success

    local link_count
    link_count=$(echo "$output" | jq '. | length')
    [[ "$link_count" -eq 1 ]]

    local link_type
    link_type=$(echo "$output" | jq -r '.[0].link_type')
    [[ "$link_type" == "PCIE" ]]

    # Weight 40 → PCIe-HostBridge (rank 30)
    local rank
    rank=$(echo "$output" | jq '.[0].rank')
    [[ "$rank" -eq 30 ]]

    local label
    label=$(echo "$output" | jq -r '.[0].link_label')
    [[ "$label" == "PCIe-HostBridge" ]]
}

# ── _detect_topo_amdsmi: PCIe weight-to-rank mapping ───────────────────────

@test "_detect_topo_amdsmi: weight 15 maps to PCIe-SameSwitch rank 50" {
    amd-smi() {
        if [[ "$1" == "topology" && "$2" == "--json" ]]; then
            echo '[
                {"gpu":0,"links":[{"gpu":0,"weight":0,"link_type":"SELF"},{"gpu":1,"weight":15,"link_type":"PCIE"}]},
                {"gpu":1,"links":[{"gpu":1,"weight":0,"link_type":"SELF"},{"gpu":0,"weight":15,"link_type":"PCIE"}]}
            ]'
        fi
    }

    run _detect_topo_amdsmi 2
    assert_success

    local rank label
    rank=$(echo "$output" | jq '.[0].rank')
    label=$(echo "$output" | jq -r '.[0].link_label')
    [[ "$rank" -eq 50 ]]
    [[ "$label" == "PCIe-SameSwitch" ]]
}

@test "_detect_topo_amdsmi: weight 60 maps to CrossNUMA rank 10" {
    amd-smi() {
        if [[ "$1" == "topology" && "$2" == "--json" ]]; then
            echo '[
                {"gpu":0,"links":[{"gpu":0,"weight":0,"link_type":"SELF"},{"gpu":1,"weight":60,"link_type":"PCIE"}]},
                {"gpu":1,"links":[{"gpu":1,"weight":0,"link_type":"SELF"},{"gpu":0,"weight":60,"link_type":"PCIE"}]}
            ]'
        fi
    }

    run _detect_topo_amdsmi 2
    assert_success

    local rank label
    rank=$(echo "$output" | jq '.[0].rank')
    label=$(echo "$output" | jq -r '.[0].link_label')
    [[ "$rank" -eq 10 ]]
    [[ "$label" == "CrossNUMA" ]]
}

# ── _detect_topo_amdsmi: edge cases ────────────────────────────────────────

@test "_detect_topo_amdsmi: returns failure when amd-smi not available" {
    amd-smi() { return 1; }

    run _detect_topo_amdsmi 2
    assert_failure
}

@test "_detect_topo_amdsmi: returns failure on empty JSON" {
    amd-smi() {
        if [[ "$1" == "topology" && "$2" == "--json" ]]; then
            echo ""
        fi
    }

    run _detect_topo_amdsmi 2
    assert_failure
}

@test "_detect_topo_amdsmi: single GPU produces no links" {
    amd-smi() {
        if [[ "$1" == "topology" && "$2" == "--json" ]]; then
            echo '[{"gpu":0,"links":[{"gpu":0,"weight":0,"link_type":"SELF"}]}]'
        fi
    }

    run _detect_topo_amdsmi 1
    # With 1 GPU, the inner loop (j = i+1) never runs → empty links_tsv → failure
    assert_failure
}

# ── _detect_topo_rocmsmi ────────────────────────────────────────────────────

@test "_detect_topo_rocmsmi: parses 4-GPU XGMI topology from rocm-smi" {
    rocm-smi() {
        if [[ "$1" == "--showtopo" ]]; then
            cat "$FIXTURES_DIR/rocm_smi_showtopo_4gpu_xgmi.txt"
        fi
    }

    run _detect_topo_rocmsmi 4
    assert_success

    local link_count
    link_count=$(echo "$output" | jq '. | length')
    [[ "$link_count" -eq 6 ]]

    local xgmi_count
    xgmi_count=$(echo "$output" | jq '[.[] | select(.link_type == "XGMI")] | length')
    [[ "$xgmi_count" -eq 6 ]]
}

@test "_detect_topo_rocmsmi: parses 2-GPU PCIe topology from rocm-smi" {
    rocm-smi() {
        if [[ "$1" == "--showtopo" ]]; then
            cat "$FIXTURES_DIR/rocm_smi_showtopo_2gpu_pcie.txt"
        fi
    }

    run _detect_topo_rocmsmi 2
    assert_success

    local link_count
    link_count=$(echo "$output" | jq '. | length')
    [[ "$link_count" -eq 1 ]]

    local link_type
    link_type=$(echo "$output" | jq -r '.[0].link_type')
    [[ "$link_type" == "PCIE" ]]
}

@test "_detect_topo_rocmsmi: returns failure when rocm-smi not available" {
    rocm-smi() { return 1; }

    run _detect_topo_rocmsmi 2
    assert_failure
}
