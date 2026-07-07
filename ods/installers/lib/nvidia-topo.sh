#!/usr/bin/env bash
# ============================================================================
# ODS Installer — NVIDIA GPU Topology Detection
# ============================================================================
# Part of: installers/lib/
# Purpose: Detect NVIDIA Multi-GPU topology as well as basic GPU info
#          and return as JSON. Sourced by detection.sh and 03-features.sh.
#
# Expects: nvidia-smi, warn(), err(), LINK_RANK
# Provides: parse_nvidia_topo_matrix(), detect_nvidia_topo(), link_rank(),
#           link_label(), get_rank()
#
# Modder notes:
#   This script handles NVIDIA-specific topology detection including NVLink,
#   PCIe, and NUMA relationships. It outputs structured JSON for consumption
#   by the multi-GPU strategy selection logic.
# ============================================================================

link_rank() {
  case "$1" in
  NV4 | NV6 | NV8 | NV12 | NV18)  echo 100 ;;   # NVLink gen2/3
  XGMI | XGMI2)                   echo 90  ;;   # AMD Infinity Fabric
  NV1 | NV2 | NV3)                echo 80  ;;   # NVLink gen1
  MIG)                            echo 70  ;;   # MIG instance, same die
  PIX)                            echo 50  ;;   # Same PCIe switch
  PXB)                            echo 40  ;;   # Multiple PCIe switches, same CPU
  PHB)                            echo 30  ;;   # PCIe host bridge
  NODE)                           echo 20  ;;   # Same NUMA, no direct bridge
  SYS | SOC)                      echo 10  ;;   # Cross-NUMA (SOC = old name for SYS)
  *)                              echo 0   ;;
  esac
}

link_label() {
  case "$1" in
  NV*)   echo "NVLink" ;;
  XGMI*) echo "InfinityFabric" ;;
  MIG)   echo "MIG-SameDie" ;;
  PIX)   echo "PCIe-SameSwitch" ;;
  PXB)   echo "PCIe-CrossSwitch" ;;
  PHB)   echo "PCIe-HostBridge" ;;
  NODE)  echo "SameNUMA-NoBridge" ;;
  SYS | SOC) echo "CrossNUMA" ;;
  X)     echo "Self" ;;
  *)     echo "Unknown" ;;
  esac
}
parse_nvidia_topo_matrix() {
  # Returns JSON array of {gpu_a, gpu_b, link_type, link_label, rank}
  local matrix
  matrix=$(nvidia-smi topo -m 2>/dev/null) || {
    warn "nvidia-smi topo -m failed"
    echo "[]"
    return
  }
  
  matrix=$(echo "$matrix" | sed 's/\x1b\[[0-9;]*m//g')

  local header_line headers=()
  header_line=$(echo "$matrix" | grep -E '^\s+GPU[0-9]' | head -1)
  read -ra headers <<<"$header_line"

  # Collect pairs as TSV, then convert to JSON via jq
  local pairs_tsv=""

  while IFS= read -r line; do
    # Skip header line and blanks (header starts with whitespace, data rows don't)
    [[ "$line" =~ ^[[:space:]] ]] && continue
    [[ -z "$line" ]] && continue
    local row_label
    row_label=$(echo "$line" | awk '{print $1}')
    [[ "$row_label" =~ ^GPU[0-9]+$ ]] || continue
    local gpu_a="${row_label#GPU}"
    local cells=()
    read -ra cells <<<"$line"
    # cells[0] = row label, cells[1..] = columns
    for col_idx in "${!headers[@]}"; do
      local col_header="${headers[$col_idx]}"
      [[ "$col_header" =~ ^GPU[0-9]+$ ]] || continue
      local gpu_b="${col_header#GPU}"
      [[ "$gpu_a" == "$gpu_b" ]] && continue  # skip self
      [[ "$gpu_a" -ge "$gpu_b" ]] && continue # dedup (only A<B pairs)
      local cell="${cells[$((col_idx + 1))]:-UNKNOWN}"
      local rank
      rank=$(link_rank "$cell")
      local label
      label=$(link_label "$cell")
      pairs_tsv+="${gpu_a}	${gpu_b}	${cell}	${label}	${rank}"$'\n'
    done
  done <<<"$matrix"

  if [[ -z "$pairs_tsv" ]]; then
    echo "[]"
    return
  fi

  printf '%s' "$pairs_tsv" | jq -Rn '[inputs | split("\t") | {
    gpu_a: (.[0] | tonumber),
    gpu_b: (.[1] | tonumber),
    link_type: .[2],
    link_label: .[3],
    rank: (.[4] | tonumber)
  }]'
}

detect_nvidia_topo() {
  # Basic GPU list
  local gpu_list
  gpu_list=$(nvidia-smi --query-gpu=index,name,memory.total,memory.free,pcie.link.gen.current,pcie.link.width.current,uuid \
    --format=csv,noheader,nounits 2>/dev/null) || {
    err "nvidia-smi query failed"
    return 1
  }

  # Parse CSV into JSON array via jq
  local gpus_json
  gpus_json=$(echo "$gpu_list" | jq -Rn '[inputs | split(",") | map(gsub("^\\s+|\\s+$"; "")) | {
    index: (.[0] | tonumber),
    name: .[1],
    memory_gb: ((.[2] | tonumber) / 1024 * 10 | round / 10),
    memory_free_gb: ((.[3] | tonumber) / 1024 * 10 | round / 10),
    pcie_gen: .[4],
    pcie_width: .[5],
    uuid: .[6]
  }]')

  local gpu_count
  gpu_count=$(echo "$gpus_json" | jq 'length')

  # MIG detection
  local mig_mode="false"
  if nvidia-smi -q 2>/dev/null | grep -q "MIG Mode.*Enabled"; then
    mig_mode="true"
  fi

  # Driver version
  local driver_ver
  driver_ver=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 | xargs)

  # Topology matrix
  local topo_pairs
  topo_pairs=$(parse_nvidia_topo_matrix)

  # NUMA info
  local numa_json="{}"
  if command -v numactl &>/dev/null; then
    local numa_nodes
    numa_nodes=$(numactl --hardware 2>/dev/null | grep "^node [0-9]* cpus" | wc -l)
    numa_json=$(jq -n --argjson n "$numa_nodes" '{nodes: $n}')
  fi

  # Compose final JSON
  jq -n \
    --arg vendor "nvidia" \
    --argjson gpu_count "$gpu_count" \
    --arg driver "$driver_ver" \
    --argjson mig "$mig_mode" \
    --argjson numa "$numa_json" \
    --argjson gpus "$gpus_json" \
    --argjson links "$topo_pairs" \
    '{
      vendor: $vendor,
      gpu_count: $gpu_count,
      driver_version: $driver,
      mig_enabled: $mig,
      numa: $numa,
      gpus: $gpus,
      links: $links
    }'
}

# ============================================================================
# Topology lookup helpers (used by 03-features.sh custom assignment path)
# ============================================================================

get_rank()  { echo "${LINK_RANK["$1,$2"]:-0}"; }
