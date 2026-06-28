#!/bin/bash
# ODS Hardware Detection
# Detects GPU, CPU, RAM and recommends tier
# Supports: NVIDIA (nvidia-smi), AMD APU/dGPU (sysfs), Apple Silicon

set -euo pipefail

# Note: this script is used by installers/tests. Keep it deterministic and safe:
# - never rely on uninitialized vars
# - avoid pipeline masking failures
# - do not hang on missing system tools

# Print errors with context
err() {
    echo -e "${RED}Error:${NC} $*" >&2
}

# Require a dependency for a given feature
require_cmd() {
    local cmd="$1"
    local hint="${2:-}"
    if ! command -v "$cmd" &>/dev/null; then
        err "Missing required command: $cmd${hint:+ ($hint)}"
        return 1
    fi
    return 0
}

# Safe command execution: returns empty string on failure
try() {
    "$@" 2>/dev/null || true
}

# Safer head -1 that doesn't error under pipefail
first_line() {
    awk 'NR==1{print; exit 0}'
}

# JSON string escape (minimal, sufficient for our fields)
json_escape() {
    local s="$1"
    s=${s//\\/\\\\}
    s=${s//\"/\\\"}
    s=${s//$'\n'/\\n}
    s=${s//$'\r'/\\r}
    s=${s//$'\t'/\\t}
    printf '%s' "$s"
}

# Normalize integer (digits only) else 0
as_int() {
    local v="${1:-}"
    [[ "$v" =~ ^[0-9]+$ ]] && echo "$v" || echo "0"
}

# Clamp integer between min/max
clamp_int() {
    local v min max
    v=$(as_int "${1:-0}")
    min=$(as_int "${2:-0}")
    max=$(as_int "${3:-0}")
    (( v < min )) && v=$min
    (( v > max )) && v=$max
    echo "$v"
}

# Detect if running inside a container
in_container() {
    [[ -f /.dockerenv ]] && return 0
    [[ -f /run/.containerenv ]] && return 0
    if [[ -f /proc/1/cgroup ]] && grep -qiE '(docker|containerd|kubepods|lxc)' /proc/1/cgroup 2>/dev/null; then
        return 0
    fi
    return 1
}

# Detect if inside WSL2
is_wsl() {
    [[ -f /proc/version ]] && grep -qi microsoft /proc/version 2>/dev/null
}

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

usage() {
    cat <<'EOF'
Usage: detect-hardware.sh [--json] [--json-compact] [--verbose] [--help]

Options:
  --json          Print machine-readable JSON output
  --json-compact  Print JSON in one line (for scripting)
  --verbose       Include extra hardware identifiers in text mode
  --help          Show this help
EOF
}

# Detect OS and environment
detect_os() {
    # Explicit WSL detection first
    if is_wsl; then
        echo "wsl"
        return 0
    fi

    # OSTYPE is set in bash, but keep a fallback in case it's missing
    local os_type="${OSTYPE:-}"
    if [[ "$os_type" == "darwin"* ]]; then
        echo "macos"
    elif [[ "$os_type" == "linux-gnu"* || -f /proc/version ]]; then
        echo "linux"
    else
        echo "unknown"
    fi
}

# Detect platform quirks that impact Docker/GPU detection
# Returns: "native" | "wsl2" | "container"
detect_platform() {
    if in_container; then
        echo "container"
    elif is_wsl; then
        echo "wsl2"
    else
        echo "native"
    fi
}

# Detect NVIDIA GPU
detect_nvidia() {
    # Validate NVIDIA hardware present in sysfs before trusting nvidia-smi,
    # which may be installed without NVIDIA hardware (e.g. nvidia-container-toolkit
    # on AMD-only systems).
    local _has_nvidia=false
    for _v in /sys/class/drm/card*/device/vendor; do
        [[ "$(cat "$_v" 2>/dev/null)" == "0x10de" ]] && _has_nvidia=true && break
    done
    $_has_nvidia || return 1

    # Works on bare metal Linux; on WSL2 it may be present via Docker Desktop GPU integration.
    if command -v nvidia-smi &>/dev/null; then
        nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>/dev/null | first_line
    fi
}

# Parse PCI device id from nvidia-smi output, normalize to 0x1234
nvidia_device_id() {
    if command -v nvidia-smi &>/dev/null; then
        local pci_id
        pci_id=$(nvidia-smi --query-gpu=pci.device_id --format=csv,noheader 2>/dev/null | first_line | xargs || true)
        # Example: 0x26B110DE => device part is first 6 chars => 0x26B1
        if [[ -n "$pci_id" && "$pci_id" == 0x* && ${#pci_id} -ge 6 ]]; then
            echo "${pci_id:0:6}"
            return 0
        fi
    fi
    echo ""
    return 1
}

# Parse nvidia-smi memory.total robustly (MB)
parse_nvidia_vram_mb() {
    local output="$1"
    local mb
    mb=$(echo "$output" | awk -F',' '{gsub(/^[ \t]+|[ \t]+$/,"",$2); print $2}' | xargs || true)
    as_int "$mb"
}

# Count NVIDIA GPUs
count_nvidia_gpus() {
    if command -v nvidia-smi &>/dev/null; then
        nvidia-smi --query-gpu=name --format=csv,noheader,nounits 2>/dev/null | wc -l | tr -d ' '
    else
        echo "0"
    fi
}

# Detect AMD GPU via sysfs (works without ROCm installed)
# Returns: gpu_name|vram_bytes|gtt_bytes|is_apu|gpu_busy|temp|power|vulkan|rocm|driver|device_id|subsystem_device|revision
detect_amd_sysfs() {
    for card_dir in /sys/class/drm/card*/device; do
        [[ -d "$card_dir" ]] || continue
        local vendor
        vendor=$(cat "$card_dir/vendor" 2>/dev/null) || continue

        # 0x1002 = AMD
        if [[ "$vendor" == "0x1002" ]]; then
            local vram_total gtt_total gpu_name gpu_busy temp power hwmon_dir is_apu
            local device_id subsystem_device revision

            # Read PCI device identifiers
            device_id=$(cat "$card_dir/device" 2>/dev/null) || device_id="unknown"
            subsystem_device=$(cat "$card_dir/subsystem_device" 2>/dev/null) || subsystem_device="unknown"
            revision=$(cat "$card_dir/revision" 2>/dev/null) || revision="unknown"

            # Read memory info
            vram_total=$(cat "$card_dir/mem_info_vram_total" 2>/dev/null) || vram_total=0
            gtt_total=$(cat "$card_dir/mem_info_gtt_total" 2>/dev/null) || gtt_total=0

            # Detect if APU (unified memory)
            # Strix Halo has small VRAM carve-out (UMA frame buffer, often 1GB)
            # but large GTT (actual usable GPU memory from system RAM).
            is_apu="false"
            if [[ $vram_total -gt 0 && $gtt_total -gt 0 ]]; then
                local vram_gb=$(( vram_total / 1073741824 ))
                local gtt_gb=$(( gtt_total / 1073741824 ))
                if [[ $gtt_gb -ge 16 && $vram_gb -le 4 ]]; then
                    # Small VRAM + large GTT = APU with unified memory
                    is_apu="true"
                elif [[ $gtt_gb -ge 32 ]]; then
                    is_apu="true"
                elif [[ $vram_gb -ge 32 ]]; then
                    is_apu="true"
                fi
            fi

            # GPU utilization
            gpu_busy=$(cat "$card_dir/gpu_busy_percent" 2>/dev/null) || gpu_busy=0

            # Find hwmon for temp/power
            temp=0
            power=0
            for hwmon_dir in "$card_dir"/hwmon/hwmon*; do
                if [[ -d "$hwmon_dir" ]]; then
                    local raw_temp raw_power
                    raw_temp=$(cat "$hwmon_dir/temp1_input" 2>/dev/null) || raw_temp=0
                    temp=$(( raw_temp / 1000 ))  # millidegrees → C
                    raw_power=$(cat "$hwmon_dir/power1_average" 2>/dev/null) || raw_power=0
                    power=$(( raw_power / 1000000 ))  # microwatts → W
                    break
                fi
            done

            # Try to get GPU name from various sources
            gpu_name=""
            # Try marketing name first
            if [[ -f "$card_dir/product_name" ]]; then
                gpu_name=$(cat "$card_dir/product_name" 2>/dev/null) || true
            fi
            # Fall back to device ID lookup
            if [[ -z "$gpu_name" ]]; then
                gpu_name="AMD GPU ($device_id)"
            fi

            # Check for Vulkan support
            local vulkan_available="false"
            if command -v vulkaninfo &>/dev/null; then
                if vulkaninfo --summary 2>/dev/null | grep -qiE "radeon|amd|gfx11"; then
                    vulkan_available="true"
                fi
            fi

            # Check for ROCm
            local rocm_available="false"
            if command -v rocminfo &>/dev/null; then
                rocm_available="true"
            fi

            # Check amdgpu driver loaded
            local driver_loaded="false"
            if lsmod 2>/dev/null | grep -q amdgpu; then
                driver_loaded="true"
            fi

            echo "${gpu_name}|${vram_total}|${gtt_total}|${is_apu}|${gpu_busy}|${temp}|${power}|${vulkan_available}|${rocm_available}|${driver_loaded}|${device_id}|${subsystem_device}|${revision}"
            return 0
        fi
    done
    return 1
}

# Count AMD GPUs via sysfs
count_amd_gpus() {
    local count=0
    for card_dir in /sys/class/drm/card*/device; do
        [[ -d "$card_dir" ]] || continue
        local vendor
        vendor=$(cat "$card_dir/vendor" 2>/dev/null) || continue
        # (( 0++ )) returns exit 1 in bash, so || true prevents pipefail abort
        [[ "$vendor" == "0x1002" ]] && (( count++ )) || true
    done
    echo "$count"
}

# Detect AMD GPU (legacy ROCm-only path)
detect_amd() {
    # Try sysfs first (works without ROCm)
    local sysfs_out
    if sysfs_out=$(detect_amd_sysfs 2>/dev/null); then
        echo "$sysfs_out"
        return 0
    fi
    # Fall back to rocm-smi
    if command -v rocm-smi &>/dev/null; then
        rocm-smi --showproductname --showmeminfo vram 2>/dev/null | grep -E "GPU|Total Memory" | head -2
    fi
}

# Detect NVIDIA Jetson (Tegra SoC)
# Returns: <model_name>|<l4t_release>|<ram_mb>   (pipe-delimited) or empty
# Test hooks: ODS_NV_TEGRA_RELEASE, ODS_DEVICE_TREE_COMPATIBLE,
#             ODS_DEVICE_TREE_MODEL, ODS_GPU0_SYSFS, ODS_UNAME_M
detect_jetson() {
    [[ "${ODS_ENABLE_EXPERIMENTAL_JETSON:-0}" == "1" ]] || return 1

    local tegra_release="${ODS_NV_TEGRA_RELEASE:-/etc/nv_tegra_release}"
    local dt_compat="${ODS_DEVICE_TREE_COMPATIBLE:-/proc/device-tree/compatible}"
    local dt_model="${ODS_DEVICE_TREE_MODEL:-/proc/device-tree/model}"
    local gpu0_sysfs="${ODS_GPU0_SYSFS:-/sys/devices/gpu.0}"
    local uname_m="${ODS_UNAME_M:-$(uname -m)}"

    [[ "$uname_m" == "aarch64" ]] || return 1

    local is_jetson=false
    if [[ -f "$tegra_release" ]]; then
        is_jetson=true
    elif [[ -f "$dt_compat" ]] && tr -d '\0' < "$dt_compat" 2>/dev/null | grep -q "nvidia,tegra"; then
        is_jetson=true
    elif [[ -d "$gpu0_sysfs" ]]; then
        is_jetson=true
    fi
    $is_jetson || return 1

    local model="NVIDIA Jetson (Tegra)"
    if [[ -f "$dt_model" ]]; then
        local raw
        raw=$(tr -d '\0' < "$dt_model" 2>/dev/null) || raw=""
        [[ -n "$raw" ]] && model="$raw"
    fi

    local l4t=""
    if [[ -f "$tegra_release" ]]; then
        local major minor
        major=$(grep -oE 'R[0-9]+' "$tegra_release" 2>/dev/null | head -1)
        minor=$(grep -oE 'REVISION: [0-9.]+' "$tegra_release" 2>/dev/null | head -1 | awk '{print $2}')
        [[ -n "$major" && -n "$minor" ]] && l4t="${major}.${minor}"
    fi

    local ram_kb ram_mb=0
    ram_kb=$(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print $2}')
    [[ -n "$ram_kb" && "$ram_kb" -gt 0 ]] && ram_mb=$(( ram_kb / 1024 ))

    echo "${model}|${l4t}|${ram_mb}"
}

# Detect Apple Silicon
detect_apple() {
    if [[ "$(detect_os)" == "macos" ]]; then
        sysctl -n machdep.cpu.brand_string 2>/dev/null
        # Unified memory = system RAM on Apple Silicon
        sysctl -n hw.memsize 2>/dev/null | awk '{print int($1/1024/1024/1024)"GB unified"}'
    fi
}

# Get CPU info
detect_cpu() {
    local os
    os=$(detect_os)
    case $os in
        macos)
            sysctl -n machdep.cpu.brand_string 2>/dev/null || echo "Unknown"
            ;;
        *)
            grep -m1 "model name" /proc/cpuinfo 2>/dev/null | cut -d: -f2 | xargs || echo "Unknown"
            ;;
    esac
}

# Get CPU cores
detect_cores() {
    local os
    os=$(detect_os)
    case $os in
        macos)
            sysctl -n hw.ncpu 2>/dev/null || echo "0"
            ;;
        *)
            nproc 2>/dev/null || grep -c ^processor /proc/cpuinfo 2>/dev/null || echo "0"
            ;;
    esac
}

# Get RAM in GB
detect_ram() {
    local os
    os=$(detect_os)
    case $os in
        macos)
            sysctl -n hw.memsize 2>/dev/null | awk '{print int($1/1024/1024/1024)}'
            ;;
        *)
            grep MemTotal /proc/meminfo 2>/dev/null | awk '{print int($2/1024/1024)}'
            ;;
    esac
}

# Parse VRAM from nvidia-smi output (in MB)
# Kept for backwards compatibility with older callers; prefer parse_nvidia_vram_mb.
parse_nvidia_vram() {
    parse_nvidia_vram_mb "$1"
}

# Determine tier based on VRAM (discrete GPU)
# T4: 48GB+ | T3: 20-47GB | T2: 12-19GB | T1: <12GB
get_tier() {
    local vram_mb=$1
    local vram_gb=$((vram_mb / 1024))

    if [[ $vram_gb -ge 48 ]]; then
        echo "T4"
    elif [[ $vram_gb -ge 20 ]]; then
        echo "T3"
    elif [[ $vram_gb -ge 12 ]]; then
        echo "T2"
    else
        echo "T1"
    fi
}

# Determine Strix Halo tier based on unified memory
# SH_LARGE: 90GB+ | SH_COMPACT: <90GB
get_strix_halo_tier() {
    local unified_gb=$1

    if [[ $unified_gb -ge 90 ]]; then
        echo "SH_LARGE"
    else
        echo "SH_COMPACT"
    fi
}

# Determine NVIDIA Grace Blackwell tier based on unified memory
# Mirrors installers/phases/02-detection.sh:346-360 thresholds
# NV_ULTRA: 90GB+ | T4: 48-89GB | T3: 20-47GB | T2: 12-19GB | T1: <12GB
get_nvidia_unified_tier() {
    local unified_gb=$1

    if [[ $unified_gb -ge 90 ]]; then
        echo "NV_ULTRA"
    elif [[ $unified_gb -ge 48 ]]; then
        echo "T4"
    elif [[ $unified_gb -ge 20 ]]; then
        echo "T3"
    elif [[ $unified_gb -ge 12 ]]; then
        echo "T2"
    else
        echo "T1"
    fi
}

# Determine Apple Silicon tier based on unified memory
# AP_PRO: 36GB+ | AP_BASE: <36GB
get_apple_tier() {
    local unified_gb=$1
    if [[ $unified_gb -ge 96 ]]; then
        echo "AP_ULTRA"
    elif [[ $unified_gb -ge 36 ]]; then
        echo "AP_PRO"
    else
        echo "AP_BASE"
    fi
}

# Get tier description (supports NVIDIA, Strix Halo, and Apple tiers)
tier_description() {
    case $1 in
        T4)    echo "Ultimate (48GB+): large flagship local profile with long context headroom" ;;
        T3)    echo "Pro (20-47GB): strong local profile with room for larger models" ;;
        T2)    echo "Starter (12-19GB): mid-size local profile for everyday work" ;;
        T0)    echo "Lightweight (<4GB discrete VRAM): CPU fallback profile for install stability" ;;
        T1)    echo "Mini (<12GB): compact local profile or CPU inference" ;;
        NV_ULTRA)   echo "NVIDIA Ultra (90GB+ unified or discrete): flagship local profile (Grace Blackwell or multi-GPU)" ;;
        SH_LARGE)   echo "Strix Halo 90+: flagship unified-memory local profile" ;;
        SH_COMPACT) echo "Strix Halo Compact: balanced unified-memory local profile" ;;
        AP_ULTRA)   echo "Apple Ultra (96GB+): high-end local profile via CPU inference in Docker" ;;
        AP_PRO)     echo "Apple Pro (36GB+): balanced local profile via CPU inference in Docker" ;;
        AP_BASE)    echo "Apple Base (<36GB): compact local profile via CPU inference in Docker" ;;
    esac
}

detect_model_profile() {
    local profile="${MODEL_PROFILE:-qwen}"
    profile="$(printf '%s' "$profile" | tr '[:upper:]' '[:lower:]')"
    case "$profile" in
        auto|gemma|gemma4|gemma-4) echo "gemma4" ;;
        *)                         echo "qwen" ;;
    esac
}

# Get recommended model for tier
tier_model() {
    local profile
    profile="$(detect_model_profile)"

    if [[ "$profile" == "gemma4" ]]; then
        case $1 in
            T4)    echo "gemma-4-31b-it" ;;
            T3)    echo "gemma-4-26b-a4b-it" ;;
            T2)    echo "gemma-4-e4b-it" ;;
            T0)    echo "qwen3.5-2b" ;;
            T1)    echo "gemma-4-e2b-it" ;;
            NV_ULTRA)   echo "gemma-4-31b-it" ;;
            SH_LARGE)   echo "gemma-4-31b-it" ;;
            SH_COMPACT) echo "gemma-4-26b-a4b-it" ;;
            AP_ULTRA)   echo "gemma-4-31b-it-Q4_K_M.gguf" ;;
            AP_PRO)     echo "gemma-4-e4b-it-Q4_K_M.gguf" ;;
            AP_BASE)    echo "gemma-4-e2b-it-Q4_K_M.gguf" ;;
        esac
        return
    fi

    case $1 in
        T4)    echo "qwen3-coder-next" ;;
        T3)    echo "qwen3-30b-a3b" ;;
        T2)    echo "qwen3.5-9b" ;;
        T0)    echo "qwen3.5-2b" ;;
        T1)    echo "qwen3.5-2b" ;;
        NV_ULTRA)   echo "qwen3-coder-next" ;;
        SH_LARGE)   echo "qwen3-coder-next" ;;
        SH_COMPACT) echo "qwen3-30b-a3b" ;;
        AP_ULTRA)   echo "qwen3-coder-next-Q4_K_M.gguf" ;;
        AP_PRO)     echo "qwen3.5-9b-Q4_K_M.gguf" ;;
        AP_BASE)    echo "qwen3.5-2b-Q4_K_M.gguf" ;;
    esac
}

# Main detection
main() {
    local json_output=false
    local compact_json=false
    local verbose=false

    for arg in "$@"; do
        case "$arg" in
            --json) json_output=true ;;
            --json-compact) json_output=true; compact_json=true ;;
            --verbose) verbose=true ;;
            --help|-h) usage; return 0 ;;
            *)
                err "Unknown argument: $arg"
                usage
                return 2
                ;;
        esac
    done

    local os platform
    os=$(detect_os)
    platform=$(detect_platform)

    local cpu cores ram
    cpu=$(detect_cpu)
    cores=$(as_int "$(detect_cores)")
    ram=$(as_int "$(detect_ram)")

    local gpu_name=""
    local gpu_vram_mb=0
    local gpu_count=0
    local gpu_type="none"
    local gpu_architecture=""
    local memory_type="discrete"
    local gpu_temp=0
    local gpu_power=0
    local gpu_busy=0
    local vulkan_available="false"
    local rocm_available="false"
    local driver_loaded="false"
    local device_id=""
    local subsystem_device=""
    local revision=""

    # Guardrails: avoid nonsense values
    cores=$(clamp_int "$cores" 1 1024)
    ram=$(clamp_int "$ram" 1 4096)

    # Try Jetson first — Tegra iGPUs don't show up as PCIe NVIDIA cards in
    # sysfs and nvidia-smi is unreliable on JetPack, so detect via L4T release
    # file / device-tree signature before the discrete-NVIDIA branch below.
    local jetson_out=""
    jetson_out=$(detect_jetson || true)
    if [[ -n "$jetson_out" ]]; then
        local _jetson_l4t _jetson_ram_mb
        IFS='|' read -r gpu_name _jetson_l4t _jetson_ram_mb <<< "$jetson_out"
        gpu_vram_mb=$(as_int "$_jetson_ram_mb")
        gpu_count=1
        gpu_type="jetson"
        gpu_architecture="tegra"
        memory_type="unified"
        device_id="$_jetson_l4t"
    fi

    # Try NVIDIA next
    local nvidia_out=""
    if [[ -z "$gpu_name" ]]; then
        nvidia_out=$(detect_nvidia || true)
    fi
    if [[ -n "$nvidia_out" ]]; then
        gpu_name=$(echo "$nvidia_out" | awk -F',' '{gsub(/^[ \t]+|[ \t]+$/,"",$1); print $1}' | xargs || true)
        gpu_vram_mb=$(parse_nvidia_vram_mb "$nvidia_out")
        gpu_count=$(count_nvidia_gpus)
        gpu_type="nvidia"
        gpu_architecture="cuda"
        memory_type="discrete"

        # NVIDIA Grace Blackwell (GB10, GB200): nvidia-smi reports [N/A] for
        # memory.total when CPU+GPU share unified memory. Fall back to system
        # RAM as the VRAM budget. Mirrors installers/lib/detection.sh:178-194.
        if [[ "$gpu_vram_mb" -eq 0 ]]; then
            local _vram_field
            _vram_field=$(echo "$nvidia_out" | head -1 | awk -F',' '{gsub(/^[ \t]+|[ \t]+$/,"",$2); print $2}' | xargs)
            if [[ "$_vram_field" == "[N/A]" || "$_vram_field" == "N/A" ]]; then
                memory_type="unified"
                gpu_architecture="grace-blackwell"
                local _ram_kb
                _ram_kb=$(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print $2}')
                if [[ -n "$_ram_kb" && "$_ram_kb" -gt 0 ]]; then
                    gpu_vram_mb=$(( _ram_kb / 1024 ))
                fi
            fi
        fi

        device_id="$(nvidia_device_id || true)"
    fi

    # Try AMD if no NVIDIA
    if [[ -z "$gpu_name" ]]; then
        local amd_out=""
        if amd_out=$(detect_amd_sysfs 2>/dev/null); then
            # Parse pipe-delimited output from detect_amd_sysfs
            local vram_bytes gtt_bytes is_apu busy temp power vulkan rocm driver dev_id subsys_dev rev
            IFS='|' read -r gpu_name vram_bytes gtt_bytes is_apu busy temp power vulkan rocm driver dev_id subsys_dev rev <<< "$amd_out"

            vram_bytes=$(as_int "$vram_bytes")
            gtt_bytes=$(as_int "$gtt_bytes")

            gpu_vram_mb=$(( vram_bytes / 1048576 ))
            gpu_count=$(count_amd_gpus)
            gpu_type="amd"
            gpu_temp=$(as_int "$temp")
            gpu_power=$(as_int "$power")
            gpu_busy=$(as_int "$busy")
            vulkan_available="$vulkan"
            rocm_available="$rocm"
            driver_loaded="$driver"
            device_id="$dev_id"
            subsystem_device="$subsys_dev"
            revision="$rev"

            if [[ "$is_apu" == "true" ]]; then
                gpu_architecture="apu-unified"
                memory_type="unified"
            else
                gpu_architecture="rdna"
                memory_type="discrete"
            fi

            # Sanity clamp for telemetry
            gpu_temp=$(clamp_int "$gpu_temp" 0 130)
            gpu_power=$(clamp_int "$gpu_power" 0 2000)
            gpu_busy=$(clamp_int "$gpu_busy" 0 100)
        fi
    fi

    # Try Apple Silicon if macOS
    if [[ -z "$gpu_name" && "$os" == "macos" ]]; then
        local apple_out
        apple_out=$(detect_apple || true)
        if [[ -n "$apple_out" ]]; then
            gpu_name="Apple Silicon (Unified Memory)"
            gpu_vram_mb=$((ram * 1024))
            gpu_count=1
            gpu_type="apple"
            gpu_architecture="apple-unified"
            memory_type="unified"
        fi
    fi

    # Determine tier
    # For unified memory AMD APUs, use system RAM — VRAM reports only carve-out, not usable UMA.
    local tier tier_desc recommended_model
    if [[ "$memory_type" == "discrete" && "$gpu_type" == "nvidia" && "$gpu_vram_mb" -gt 0 && "$gpu_vram_mb" -lt 4096 ]]; then
        tier="T0"
    elif [[ "$memory_type" == "unified" && "$gpu_type" == "amd" ]]; then
        tier=$(get_strix_halo_tier "$ram")
    elif [[ "$memory_type" == "unified" && "$gpu_type" == "nvidia" ]]; then
        local unified_gb
        unified_gb=$((gpu_vram_mb / 1024))
        tier=$(get_nvidia_unified_tier "$unified_gb")
    elif [[ "$gpu_type" == "apple" ]]; then
        local unified_gb
        unified_gb=$((gpu_vram_mb / 1024))
        tier=$(get_apple_tier "$unified_gb")
    else
        tier=$(get_tier "$gpu_vram_mb")
    fi
    tier_desc=$(tier_description "$tier")
    recommended_model=$(tier_model "$tier")

    local gpu_vram_gb
    gpu_vram_gb=$((gpu_vram_mb / 1024))

    if $json_output; then
        # Emit JSON with escaping to avoid breaking downstream parsers.
        local esc_cpu esc_gpu esc_os
        esc_os=$(json_escape "$os")
        esc_cpu=$(json_escape "$cpu")
        esc_gpu=$(json_escape "$gpu_name")
        local payload
        payload=$(cat <<EOF
{
  "os": "$esc_os",
  "platform": "$(json_escape "$platform")",
  "cpu": "$esc_cpu",
  "cores": $cores,
  "ram_gb": $ram,
  "gpu": {
    "type": "$(json_escape "$gpu_type")",
    "name": "$esc_gpu",
    "architecture": "$(json_escape "$gpu_architecture")",
    "memory_type": "$(json_escape "$memory_type")",
    "count": $gpu_count,
    "vram_mb": $gpu_vram_mb,
    "vram_gb": $gpu_vram_gb,
    "device_id": "$(json_escape "$device_id")",
    "subsystem_device": "$(json_escape "$subsystem_device")",
    "revision": "$(json_escape "$revision")",
    "utilization": $gpu_busy,
    "temperature_c": $gpu_temp,
    "power_w": $gpu_power,
    "vulkan": $vulkan_available,
    "rocm": $rocm_available,
    "driver_loaded": $driver_loaded
  },
  "tier": "$(json_escape "$tier")",
  "tier_description": "$(json_escape "$tier_desc")",
  "recommended_model": "$(json_escape "$recommended_model")"
}
EOF
)
        if $compact_json; then
            printf '%s\n' "$(printf '%s' "$payload" | tr -d '\n')"
        else
            printf '%s\n' "$payload"
        fi
    else
        echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
        echo -e "${BLUE}║      ODS Hardware Detection     ║${NC}"
        echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
        echo ""
        echo -e "${GREEN}System:${NC}"
        echo "  OS:       $os"
        echo "  Platform: $platform"
        echo "  CPU:      $cpu"
        echo "  Cores:    $cores"
        echo "  RAM:      ${ram}GB"
        echo ""
        echo -e "${GREEN}GPU:${NC}"
        if [[ -n "$gpu_name" ]]; then
            echo "  Type:     $gpu_type"
            echo "  Name:     $gpu_name"
            if [[ "$memory_type" == "unified" ]]; then
                echo -e "  Memory:   ${CYAN}${gpu_vram_gb}GB (Unified)${NC}"
            else
                echo "  VRAM:     ${gpu_vram_gb}GB"
            fi

            if $verbose; then
                [[ -n "$device_id" ]] && echo "  Device:   $device_id"
                [[ -n "$subsystem_device" && "$subsystem_device" != "unknown" ]] && echo "  Subsys:   $subsystem_device"
                [[ -n "$revision" && "$revision" != "unknown" ]] && echo "  Rev:      $revision"
            fi

            if [[ "$gpu_type" == "amd" ]]; then
                echo "  Arch:     $gpu_architecture"
                [[ $gpu_temp -gt 0 ]] && echo "  Temp:     ${gpu_temp}C"
                [[ $gpu_power -gt 0 ]] && echo "  Power:    ${gpu_power}W"
                [[ $gpu_busy -gt 0 ]] && echo "  Load:     ${gpu_busy}%"
                echo "  Vulkan:   $vulkan_available"
                echo "  ROCm:     $rocm_available"
                echo "  Driver:   $driver_loaded"
            fi
        else
            echo "  No GPU detected (CPU-only mode)"
            if [[ "$platform" == "container" ]]; then
                echo "  Hint: running inside a container; GPU device nodes may not be exposed."
            elif [[ "$platform" == "wsl2" ]]; then
                echo "  Hint: on WSL2, GPU detection depends on the host + Docker Desktop GPU integration."
            fi
        fi
        echo ""
        echo -e "${YELLOW}Recommended Tier: ${tier}${NC}"
        echo "  $tier_desc"
        echo -e "  Model: ${CYAN}${recommended_model}${NC}"
        echo ""
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
