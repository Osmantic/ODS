#!/usr/bin/env bash
# ============================================================================
# Dream Server Installer — AMD GPU Topology Detection
# ============================================================================
# Part of: installers/lib/
# Purpose: Detect AMD Multi-GPU topology and return as JSON.
#          Mirrors nvidia-topo.sh output schema for vendor-agnostic consumption
#          by assign_gpus.py and the dashboard API.
#
# Expects: jq, warn(), log()
# Provides: detect_amd_topo(), amd_gpu_id(), amd_gfx_version(),
#           amd_render_node()
#
# Modder notes:
#   Detection fallback chain:
#     GPU ID:   amd-smi list --json → sysfs unique_id → composite PCI BDF ID
#     Topology: amd-smi topology --json → rocm-smi --showtopo → sysfs NUMA/IOMMU
#     gfx ver:  rocm-smi --showproductname → amd-smi static --asic → ip_discovery
#
#   Link rank values (shared with nvidia-topo.sh):
#     XGMI = 90  AMD Infinity Fabric (Instinct MI-series)
#     PIX  = 50  Same PCIe switch
#     PXB  = 40  Different PCIe switches, same CPU root complex
#     PHB  = 30  PCIe host bridge (default for same-NUMA consumer GPUs)
#     SYS  = 10  Cross-NUMA
# ============================================================================

_amd_card_id() {
    local card_dir="$1"
    local card_name
    card_name=$(basename "$(dirname "$card_dir")" 2>/dev/null) || card_name=""
    card_name="${card_name#card}"
    if [[ "$card_name" =~ ^[0-9]+$ ]]; then
        echo "$card_name"
    else
        echo "999999"
    fi
}

_amd_sort_card_dirs() {
    local card_dir
    for card_dir in "$@"; do
        printf '%s\t%s\n' "$(_amd_card_id "$card_dir")" "$card_dir"
    done | sort -n -k1,1 | cut -f2-
}

amd_memory_type() {
    local vram_bytes="${1:-0}"
    local gtt_bytes="${2:-0}"
    local gtt_gb_int=$(( gtt_bytes / 1073741824 ))
    local vram_gb_int=$(( vram_bytes / 1073741824 ))

    # GTT is the reliable unified-memory signal for AMD APUs. VRAM alone is
    # not: MI300X and future 32 GB+ discrete cards report large VRAM too.
    if [[ $gtt_gb_int -ge 16 && $vram_gb_int -le 4 ]] || [[ $gtt_gb_int -ge 32 ]]; then
        echo "unified"
    else
        echo "discrete"
    fi
}

# Generate a stable AMD GPU identifier using tiered strategy:
#   1. amd-smi UUID (Instinct only)
#   2. sysfs unique_id (Instinct only)
#   3. Composite PCI BDF + device_id + subsystem_id (all AMD GPUs)
amd_gpu_id() {
    local card_dir="$1"
    local gpu_idx="$2"  # 0-based index for amd-smi lookup

    # Method 1: amd-smi UUID
    if command -v amd-smi &>/dev/null; then
        local uuid
        uuid=$(amd-smi list --json 2>/dev/null | jq -r ".[$gpu_idx].uuid // empty" 2>/dev/null)
        if [[ -n "$uuid" && "$uuid" != "null" && "$uuid" != "N/A" ]]; then
            echo "$uuid"
            return 0
        fi
    fi

    # Method 2: sysfs unique_id (Instinct hardware)
    if [[ -f "$card_dir/unique_id" ]]; then
        local uid
        uid=$(cat "$card_dir/unique_id" 2>/dev/null)
        if [[ -n "$uid" && "$uid" != "0x0000000000000000" ]]; then
            echo "AMD-UID-${uid}"
            return 0
        fi
    fi

    # Method 3: Composite PCI BDF ID (works on all AMD GPUs)
    local pci_bdf device_id subsystem_id
    pci_bdf=$(readlink -f "$card_dir" | grep -oP '[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9]' | tail -1) || pci_bdf="unknown"
    device_id=$(cat "$card_dir/device" 2>/dev/null | sed 's/^0x//') || device_id="0000"
    subsystem_id=$(cat "$card_dir/subsystem_device" 2>/dev/null | sed 's/^0x//') || subsystem_id="0000"
    echo "AMD-${pci_bdf}-${device_id}-${subsystem_id}"
}

# Detect gfx version for an AMD GPU
# Fallback chain: rocm-smi → amd-smi → ip_discovery sysfs
amd_gfx_version() {
    local card_dir="$1"
    local gpu_idx="$2"

    # Method 1: rocm-smi --showproductname (most reliable for full gfx string)
    if command -v rocm-smi &>/dev/null; then
        local gfx
        gfx=$(rocm-smi --showproductname 2>/dev/null \
            | sed 's/\x1b\[[0-9;]*m//g' \
            | grep "GPU\[$gpu_idx\].*GFX Version" \
            | sed 's/.*GFX Version:[[:space:]]*//' \
            | head -1)
        [[ -n "$gfx" && "$gfx" != "N/A" ]] && echo "$gfx" && return 0
    fi

    # Method 2: amd-smi static --asic (TARGET_GRAPHICS_VERSION)
    if command -v amd-smi &>/dev/null; then
        local gfx
        gfx=$(amd-smi static --json --asic 2>/dev/null \
            | jq -r ".[$gpu_idx].asic.target_graphics_version // empty" 2>/dev/null)
        [[ -n "$gfx" && "$gfx" != "null" ]] && echo "$gfx" && return 0
    fi

    # Method 3: ip_discovery sysfs (kernel 6.1+) — may return incomplete values
    local ip_path="$card_dir/ip_discovery/die/0/GC/0"
    if [[ -d "$ip_path" ]]; then
        local major minor revision
        major=$(cat "$ip_path/major" 2>/dev/null) || major=""
        minor=$(cat "$ip_path/minor" 2>/dev/null) || minor=""
        revision=$(cat "$ip_path/revision" 2>/dev/null) || revision="0"
        if [[ -n "$major" && -n "$minor" ]]; then
            echo "gfx${major}${minor}${revision}"
            return 0
        fi
    fi

    # Method 4: Parse rocminfo by PCI BDF
    if command -v rocminfo &>/dev/null; then
        local pci_bdf
        pci_bdf=$(readlink -f "$card_dir" | grep -oP '[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9]') || pci_bdf=""
        if [[ -n "$pci_bdf" ]]; then
            local gfx
            gfx=$(rocminfo 2>/dev/null | grep -A10 "$pci_bdf" | grep -oP 'gfx\d+' | head -1)
            [[ -n "$gfx" ]] && echo "$gfx" && return 0
        fi
    fi

    echo "unknown"
}

# Get render node for an AMD GPU card by matching PCI device paths
amd_render_node() {
    local card_dir="$1"
    local card_pci
    card_pci=$(readlink -f "$card_dir") || { echo "unknown"; return 1; }

    for render_dir in /sys/class/drm/renderD*/device; do
        [[ -d "$render_dir" ]] || continue
        local render_pci
        render_pci=$(readlink -f "$render_dir") || continue
        if [[ "$card_pci" == "$render_pci" ]]; then
            # Return numeric ID only (e.g. "130"), not "renderD130"
            basename "$(dirname "$render_dir")" | sed 's/^renderD//'
            return 0
        fi
    done

    echo "unknown"
}

# Get GPU marketing name with fallback chain:
#   sysfs product_name → lspci → device ID
amd_gpu_name() {
    local card_dir="$1"
    local device_id="$2"

    # Try sysfs product_name
    if [[ -f "$card_dir/product_name" ]]; then
        local name
        name=$(cat "$card_dir/product_name" 2>/dev/null)
        if [[ -n "$name" && "$name" != "(null)" && -n "${name// /}" ]]; then
            echo "$name"
            return 0
        fi
    fi

    # Fallback: parse lspci for the bracketed device name
    local pci_bdf
    pci_bdf=$(readlink -f "$card_dir" | grep -oP '[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9]') || pci_bdf=""
    if [[ -n "$pci_bdf" ]] && command -v lspci &>/dev/null; then
        local lspci_name
        lspci_name=$(lspci -s "$pci_bdf" 2>/dev/null | sed 's/.*: //')
        [[ -n "$lspci_name" ]] && echo "$lspci_name" && return 0
    fi

    echo "AMD GPU (0x${device_id})"
}

# ============================================================================
# Topology detection via amd-smi (preferred) or sysfs fallback
# ============================================================================

# Try amd-smi topology --json for rich topology data (XGMI, weights, hops)
_detect_topo_amdsmi() {
    local gpu_count="$1"
    command -v amd-smi &>/dev/null || return 1

    local topo_json
    topo_json=$(amd-smi topology --json 2>/dev/null) || return 1
    [[ -z "$topo_json" || "$topo_json" == "null" ]] && return 1

    local links_tsv=""
    local i j
    for ((i = 0; i < gpu_count; i++)); do
        for ((j = i + 1; j < gpu_count; j++)); do
            local link_type weight rank link_label
            link_type=$(echo "$topo_json" | jq -r ".[$i].links[] | select(.gpu == $j) | .link_type // \"PCIE\"" 2>/dev/null)
            weight=$(echo "$topo_json" | jq -r ".[$i].links[] | select(.gpu == $j) | .weight // 30" 2>/dev/null)

            case "$link_type" in
                XGMI*)  rank=90; link_label="XGMI" ;;
                PCIE)
                    # Map weight to rank: lower weight = closer connection
                    if [[ "$weight" -le 15 ]]; then rank=50; link_label="PCIe-SameSwitch"
                    elif [[ "$weight" -le 30 ]]; then rank=40; link_label="PCIe-CrossSwitch"
                    elif [[ "$weight" -le 50 ]]; then rank=30; link_label="PCIe-HostBridge"
                    else rank=10; link_label="CrossNUMA"; fi
                    ;;
                *)      rank=30; link_label="PCIe-HostBridge" ;;
            esac

            links_tsv+="${i}	${j}	${link_type}	${link_label}	${rank}"$'\n'
        done
    done

    if [[ -n "$links_tsv" ]]; then
        printf '%s' "$links_tsv" | jq -Rn '[inputs | split("\t") | {
            gpu_a: (.[0] | tonumber),
            gpu_b: (.[1] | tonumber),
            link_type: .[2],
            link_label: .[3],
            rank: (.[4] | tonumber)
        }]'
        return 0
    fi
    return 1
}

# Try rocm-smi --showtopo for topology (text parsing)
_detect_topo_rocmsmi() {
    local gpu_count="$1"
    command -v rocm-smi &>/dev/null || return 1

    local topo_out
    topo_out=$(rocm-smi --showtopo 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g') || return 1
    [[ -z "$topo_out" ]] && return 1

    # Parse weight and type tables from rocm-smi output
    local links_tsv=""
    local i j
    for ((i = 0; i < gpu_count; i++)); do
        for ((j = i + 1; j < gpu_count; j++)); do
            local weight=30 link_type="PCIE" rank link_label

            # Try to extract weight from the weight table
            local w
            w=$(echo "$topo_out" | awk -v gpu="GPU$i" -v col=$((j+2)) '/^GPU/ && $1==gpu {print $col}' | head -1)
            [[ -n "$w" && "$w" =~ ^[0-9]+$ ]] && weight="$w"

            # Try to extract link type from the type table
            local lt
            lt=$(echo "$topo_out" | awk -v gpu="GPU$i" -v col=$((j+2)) '/Link Type/ {found=1; next} found && /^GPU/ && $1==gpu {print $col; exit}')
            [[ -n "$lt" ]] && link_type="$lt"

            case "$link_type" in
                XGMI*)  rank=90; link_label="XGMI" ;;
                PCIE)
                    if [[ "$weight" -le 15 ]]; then rank=50; link_label="PCIe-SameSwitch"
                    elif [[ "$weight" -le 30 ]]; then rank=40; link_label="PCIe-CrossSwitch"
                    elif [[ "$weight" -le 50 ]]; then rank=30; link_label="PCIe-HostBridge"
                    else rank=10; link_label="CrossNUMA"; fi
                    ;;
                *)      rank=30; link_label="PCIe-HostBridge" ;;
            esac

            links_tsv+="${i}	${j}	${link_type}	${link_label}	${rank}"$'\n'
        done
    done

    if [[ -n "$links_tsv" ]]; then
        printf '%s' "$links_tsv" | jq -Rn '[inputs | split("\t") | {
            gpu_a: (.[0] | tonumber),
            gpu_b: (.[1] | tonumber),
            link_type: .[2],
            link_label: .[3],
            rank: (.[4] | tonumber)
        }]'
        return 0
    fi
    return 1
}

# Fallback: sysfs NUMA + IOMMU topology
_detect_topo_sysfs() {
    local gpu_count="$1"
    shift
    local card_dirs=("$@")

    local links_tsv=""
    local i j
    for ((i = 0; i < gpu_count; i++)); do
        for ((j = i + 1; j < gpu_count; j++)); do
            local link_type link_label rank
            link_type=$(_sysfs_link_type "${card_dirs[$i]}" "${card_dirs[$j]}")

            case "$link_type" in
                PIX)  rank=50; link_label="PCIe-SameSwitch" ;;
                PXB)  rank=40; link_label="PCIe-CrossSwitch" ;;
                PHB)  rank=30; link_label="PCIe-HostBridge" ;;
                SYS)  rank=10; link_label="CrossNUMA" ;;
                *)    rank=30; link_label="PCIe-HostBridge" ;;
            esac

            links_tsv+="${i}	${j}	${link_type}	${link_label}	${rank}"$'\n'
        done
    done

    if [[ -n "$links_tsv" ]]; then
        printf '%s' "$links_tsv" | jq -Rn '[inputs | split("\t") | {
            gpu_a: (.[0] | tonumber),
            gpu_b: (.[1] | tonumber),
            link_type: .[2],
            link_label: .[3],
            rank: (.[4] | tonumber)
        }]'
        return 0
    fi
    echo "[]"
}

# Determine PCIe link type between two card dirs via sysfs
_sysfs_link_type() {
    local card_a="$1" card_b="$2"

    # Read NUMA nodes
    local numa_a numa_b
    numa_a=$(cat "$card_a/numa_node" 2>/dev/null) || numa_a="-1"
    numa_b=$(cat "$card_b/numa_node" 2>/dev/null) || numa_b="-1"

    # Cross-NUMA → SYS
    if [[ "$numa_a" != "$numa_b" && "$numa_a" != "-1" && "$numa_b" != "-1" ]]; then
        echo "SYS"; return 0
    fi

    # Check IOMMU groups for PCIe switch proximity
    local pci_a pci_b
    pci_a=$(readlink -f "$card_a" | grep -oP '[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9]') || pci_a=""
    pci_b=$(readlink -f "$card_b" | grep -oP '[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9]') || pci_b=""

    if [[ -n "$pci_a" && -n "$pci_b" ]]; then
        local iommu_a="" iommu_b=""
        for group_dir in /sys/kernel/iommu_groups/*/devices/; do
            [[ -d "$group_dir" ]] || continue
            [[ -e "${group_dir}${pci_a}" ]] && iommu_a=$(basename "$(dirname "$group_dir")")
            [[ -e "${group_dir}${pci_b}" ]] && iommu_b=$(basename "$(dirname "$group_dir")")
        done

        if [[ -n "$iommu_a" && -n "$iommu_b" && "$iommu_a" == "$iommu_b" ]]; then
            echo "PIX"; return 0
        fi

        # Check for shared upstream PCI bridge
        local upstream_a upstream_b
        upstream_a=$(readlink -f "$card_a/../" 2>/dev/null | xargs basename 2>/dev/null) || upstream_a=""
        upstream_b=$(readlink -f "$card_b/../" 2>/dev/null | xargs basename 2>/dev/null) || upstream_b=""
        if [[ -n "$upstream_a" && -n "$upstream_b" && "$upstream_a" == "$upstream_b" ]]; then
            echo "PXB"; return 0
        fi
    fi

    echo "PHB"
}

# ============================================================================
# Main topology detection
# ============================================================================

detect_amd_topo() {
    # Discover all AMD GPU card dirs (vendor 0x1002)
    local card_dirs=()
    local drm_sys="${DREAM_DRM_SYS:-/sys/class/drm}"
    for card_dir in "$drm_sys"/card*/device; do
        [[ -d "$card_dir" ]] || continue
        local vendor
        vendor=$(cat "$card_dir/vendor" 2>/dev/null) || continue
        [[ "$vendor" == "0x1002" ]] && card_dirs+=("$card_dir")
    done
    if [[ ${#card_dirs[@]} -gt 1 ]]; then
        mapfile -t card_dirs < <(_amd_sort_card_dirs "${card_dirs[@]}")
    fi

    local gpu_count=${#card_dirs[@]}
    if [[ $gpu_count -eq 0 ]]; then
        warn "No AMD GPUs found in sysfs"
        echo "{}"
        return 1
    fi

    # Build per-GPU JSON
    local gpus_tsv=""
    local idx=0
    for card_dir in "${card_dirs[@]}"; do
        local device_id vram_bytes vram_gb uuid gfx_ver render_node name
        local pci_bdf pcie_gen pcie_width

        device_id=$(cat "$card_dir/device" 2>/dev/null | sed 's/^0x//') || device_id="0000"
        vram_bytes=$(cat "$card_dir/mem_info_vram_total" 2>/dev/null) || vram_bytes=0
        vram_gb=$(awk -v bytes="$vram_bytes" 'BEGIN { printf "%.1f", bytes / 1073741824 }')

        uuid=$(amd_gpu_id "$card_dir" "$idx")
        gfx_ver=$(amd_gfx_version "$card_dir" "$idx")
        render_node=$(amd_render_node "$card_dir")
        name=$(amd_gpu_name "$card_dir" "$device_id")

        # PCIe info: try current, fall back to max
        pci_bdf=$(readlink -f "$card_dir" | grep -oP '[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9]' | tail -1) || pci_bdf="unknown"
        pcie_gen=$(cat "$card_dir/current_link_speed" 2>/dev/null | grep -oP '^\d+' || \
                   cat "$card_dir/max_link_speed" 2>/dev/null | grep -oP '^\d+' || echo "unknown")
        pcie_width=$(cat "$card_dir/current_link_width" 2>/dev/null | grep -oP '^\d+' || \
                     cat "$card_dir/max_link_width" 2>/dev/null | grep -oP '^\d+' || echo "unknown")
        [[ "$pcie_width" == "0" ]] && \
            pcie_width=$(cat "$card_dir/max_link_width" 2>/dev/null | grep -oP '^\d+' || echo "unknown")

        # Detect memory type per card
        local gtt_bytes mem_type
        gtt_bytes=$(cat "$card_dir/mem_info_gtt_total" 2>/dev/null) || gtt_bytes=0
        mem_type=$(amd_memory_type "$vram_bytes" "$gtt_bytes")

        gpus_tsv+="${idx}	${name}	${vram_gb}	${pcie_gen}	x${pcie_width}	${uuid}	${gfx_ver}	${render_node}	${mem_type}	${pci_bdf}"$'\n'
        idx=$((idx + 1))
    done

    local gpus_json
    gpus_json=$(printf '%s' "$gpus_tsv" | jq -Rn '[inputs | split("\t") | {
        index: (.[0] | tonumber),
        name: .[1],
        memory_gb: (.[2] | tonumber),
        pcie_gen: .[3],
        pcie_width: .[4],
        uuid: .[5],
        gfx_version: .[6],
        render_node: .[7],
        memory_type: .[8],
        pci_bdf: .[9]
    }]')

    # Topology detection with fallback chain:
    #   amd-smi topology --json → rocm-smi --showtopo → sysfs NUMA/IOMMU
    local links_json="[]"
    if [[ $gpu_count -gt 1 ]]; then
        links_json=$(_detect_topo_amdsmi "$gpu_count") || \
        links_json=$(_detect_topo_rocmsmi "$gpu_count") || \
        links_json=$(_detect_topo_sysfs "$gpu_count" "${card_dirs[@]}")
    fi

    # Driver version
    local driver_ver
    driver_ver=$(modinfo amdgpu 2>/dev/null | grep '^version:' | awk '{print $2}') || driver_ver="unknown"

    # NUMA info
    local numa_json="{}"
    if command -v numactl &>/dev/null; then
        local numa_nodes
        numa_nodes=$(numactl --hardware 2>/dev/null | grep "^node [0-9]* cpus" | wc -l)
        numa_json=$(jq -n --argjson n "$numa_nodes" '{nodes: $n}')
    fi

    jq -n \
        --arg vendor "amd" \
        --argjson gpu_count "$gpu_count" \
        --arg driver "$driver_ver" \
        --argjson numa "$numa_json" \
        --argjson gpus "$gpus_json" \
        --argjson links "$links_json" \
        '{
            vendor: $vendor,
            gpu_count: $gpu_count,
            driver_version: $driver,
            numa: $numa,
            gpus: $gpus,
            links: $links
        }'
}
