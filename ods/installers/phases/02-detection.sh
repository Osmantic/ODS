#!/bin/bash
# ============================================================================
# ODS Installer — Phase 02: System Detection
# ============================================================================
# Part of: installers/phases/
# Purpose: Orchestrate hardware detection → tier assignment → compose config
#
# Expects: SCRIPT_DIR, LOG_FILE, TIER, GPU_BACKEND, GPU_VRAM, GPU_COUNT,
#           INTERACTIVE, DRY_RUN, CAP_PROFILE_LOADED, detect_gpu(),
#           load_capability_profile(), load_backend_contract(),
#           fix_nvidia_secure_boot(), normalize_profile_tier(), tier_rank(),
#           resolve_tier_config(), resolve_compose_config(),
#           show_hardware_summary(), show_tier_recommendation(),
#           chapter(), ai(), ai_ok(), log(), warn(), success()
# Provides: GPU_BACKEND, GPU_NAME, GPU_VRAM, GPU_COUNT, GPU_MEMORY_TYPE,
#           TIER, TIER_NAME, LLM_MODEL, GGUF_FILE, GGUF_URL, MAX_CONTEXT,
#           COMPOSE_FILE, COMPOSE_FLAGS, RAM_GB, DISK_AVAIL, BACKEND_ID,
#           LLM_HEALTHCHECK_URL, LLM_PUBLIC_API_PORT,
#           OPENCLAW_PROVIDER_NAME_DEFAULT, OPENCLAW_PROVIDER_URL_DEFAULT,
#           GPU_TOPOLOGY_JSON, GPU_HAS_NVLINK, GPU_TOTAL_VRAM,
#           LLM_MODEL_SIZE_MB
#
# Modder notes:
#   Change tier auto-detection thresholds or add new hardware classes here.
# ============================================================================

[[ -f "${SCRIPT_DIR:-}/lib/safe-env.sh" ]] && . "$SCRIPT_DIR/lib/safe-env.sh"

ods_progress 12 "detection" "Detecting GPU hardware"
chapter "SYSTEM DETECTION"

GPU_BACKEND_REQUESTED="${GPU_BACKEND:-}"
GPU_BACKEND_FORCED=false
[[ "${GPU_BACKEND_REQUESTED,,}" == "amd" ]] && GPU_BACKEND_FORCED=true
GPU_BACKEND_FORCED_CPU=false
[[ "${GPU_BACKEND_REQUESTED,,}" == "cpu" ]] && GPU_BACKEND_FORCED_CPU=true
TIER_REQUESTED="${TIER:-}"
TIER_FORCED=false
[[ -n "$TIER_REQUESTED" ]] && TIER_FORCED=true

# Cloud mode: skip GPU detection entirely
if [[ "${ODS_MODE:-local}" == "cloud" ]]; then
    ai "Cloud mode — skipping GPU detection"
    GPU_BACKEND="cpu"
    GPU_NAME="Cloud (no local GPU)"
    GPU_VRAM=0
    GPU_COUNT=0
    GPU_MEMORY_TYPE="none"
    TIER="CLOUD"
    if grep -qi microsoft /proc/version 2>/dev/null; then
        _wsl_ram_bytes=""
        if command -v powershell.exe &>/dev/null; then
            _wsl_ram_bytes=$(powershell.exe -NoProfile -Command \
                "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory" 2>/dev/null | tr -d '\r')
        fi
        if [[ -n "$_wsl_ram_bytes" && "$_wsl_ram_bytes" =~ ^[0-9]+$ ]]; then
            RAM_KB=$((_wsl_ram_bytes / 1024))
        else
            RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
        fi
    else
        RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    fi
    RAM_GB=$((RAM_KB / 1024 / 1024))
    DISK_AVAIL=$(df -BG "$HOME" | tail -1 | awk '{print $4}' | tr -d 'G')
    BACKEND_ID="cpu"
    LLM_HEALTHCHECK_URL="http://localhost:4000/health/readiness"
    LLM_PUBLIC_API_PORT="4000"
    OPENCLAW_PROVIDER_NAME_DEFAULT="litellm-cloud"
    OPENCLAW_PROVIDER_URL_DEFAULT="http://litellm:4000/v1"
    resolve_compose_config
    resolve_tier_config
    if [[ "$INTERACTIVE" == "true" ]]; then
        success "Cloud mode: LLM via LiteLLM gateway (no GPU required)"
        log "  RAM: ${RAM_GB}GB, Disk: ${DISK_AVAIL}GB"
    fi
    # Skip rest of detection phase
    return 0 2>/dev/null || true
fi

ai "Reading hardware telemetry..."

load_capability_profile || true

# RAM Detection (WSL2-aware: query Windows host RAM if available)
if grep -qi microsoft /proc/version 2>/dev/null; then
    _wsl_ram_kb=""
    if command -v powershell.exe &>/dev/null; then
        _wsl_ram_bytes=$(powershell.exe -NoProfile -Command \
            "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory" 2>/dev/null | tr -d '\r')
        if [[ -n "$_wsl_ram_bytes" && "$_wsl_ram_bytes" =~ ^[0-9]+$ ]]; then
            _wsl_ram_kb=$((_wsl_ram_bytes / 1024))
        fi
    fi
    if [[ -z "$_wsl_ram_kb" ]] && command -v wmic.exe &>/dev/null; then
        _wsl_ram_kb=$(wmic.exe OS get TotalVisibleMemorySize /value 2>/dev/null \
            | grep -oE '[0-9]+' | sed -n '1p')
    fi
    if [[ -n "$_wsl_ram_kb" && "$_wsl_ram_kb" =~ ^[0-9]+$ ]]; then
        RAM_KB="$_wsl_ram_kb"
        RAM_GB=$((RAM_KB / 1024 / 1024))
        _wsl_vm_kb=$(grep MemTotal /proc/meminfo | awk '{print $2}')
        _wsl_vm_gb=$((_wsl_vm_kb / 1024 / 1024))
        log "WSL2 detected — Windows host RAM: ${RAM_GB}GB (WSL2 VM sees: ${_wsl_vm_gb}GB)"
    else
        RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
        RAM_GB=$((RAM_KB / 1024 / 1024))
        log "WSL2 detected — could not query Windows host RAM (VM sees: ${RAM_GB}GB)"
        log "For correct tier selection: use --tier N or configure .wslconfig"
    fi
else
    RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    RAM_GB=$((RAM_KB / 1024 / 1024))
    log "RAM: ${RAM_GB}GB"
fi

# Disk Detection
# Check free space on the filesystem where ODS will actually be installed.
# INSTALL_DIR may not exist yet, so walk up to the nearest existing ancestor
# so df always receives a valid path. Falls back to $HOME if nothing resolves.
_disk_probe_path="${INSTALL_DIR:-$HOME/ods}"
while [[ -n "$_disk_probe_path" ]] && [[ ! -e "$_disk_probe_path" ]]; do
    _disk_probe_path="$(dirname "$_disk_probe_path")"
done
_disk_probe_path="${_disk_probe_path:-$HOME}"
DISK_AVAIL=$(df -BG "$_disk_probe_path" | tail -1 | awk '{print $4}' | tr -d 'G')
log "Available disk: ${DISK_AVAIL}GB (on filesystem: $_disk_probe_path)"

# GPU Detection
if [[ "$GPU_BACKEND_FORCED_CPU" == "true" ]]; then
    ai "GPU_BACKEND=cpu requested - skipping GPU detection"
    apply_cpu_gpu_fallback "GPU_BACKEND=cpu was requested."
else
    ai "Detecting GPU..."
    detect_gpu || true

    if [[ "${CAP_PROFILE_LOADED:-false}" == "true" ]]; then
        case "${CAP_LLM_BACKEND:-}" in
            amd)    GPU_BACKEND="amd" ;;
            intel)  GPU_BACKEND="intel" ;;
            cpu)    GPU_BACKEND="cpu" ;;
            apple)  GPU_BACKEND="apple" ;;
            jetson)
                if [[ "${ODS_ENABLE_EXPERIMENTAL_JETSON:-0}" == "1" ]]; then
                    GPU_BACKEND="jetson"
                else
                    GPU_BACKEND="cpu"
                fi
                ;;
            *) GPU_BACKEND="nvidia" ;;
        esac
        [[ -n "${CAP_GPU_MEMORY_TYPE:-}" ]] && GPU_MEMORY_TYPE="${CAP_GPU_MEMORY_TYPE}"
        [[ -n "${CAP_GPU_NAME:-}" ]] && GPU_NAME="${CAP_GPU_NAME}"
        [[ -n "${CAP_GPU_VRAM_MB:-}" ]] && GPU_VRAM="${CAP_GPU_VRAM_MB}"
        [[ -n "${CAP_GPU_COUNT:-}" ]] && GPU_COUNT="${CAP_GPU_COUNT}"
        log "Capabilities override detection: backend=${GPU_BACKEND}, memory=${GPU_MEMORY_TYPE}, tier=${CAP_RECOMMENDED_TIER:-unknown}"
    fi

    if [[ "$GPU_BACKEND" == "amd" ]] && ! amd_gpu_runtime_devices_available; then
        _amd_missing_devices="$(amd_gpu_missing_devices_csv)"
        if [[ "${GPU_BACKEND_FORCED:-false}" == "true" ]]; then
            ai_bad "GPU_BACKEND=amd was explicitly requested, but required AMD device nodes are missing."
            show_amd_gpu_device_guidance "$_amd_missing_devices"
            error "Cannot continue with AMD GPU mode until device passthrough is available."
        elif ods_in_container; then
            ai_warn "AMD hardware was detected, but this container cannot access the AMD GPU devices."
            show_amd_gpu_device_guidance "$_amd_missing_devices"
            apply_cpu_gpu_fallback "Falling back to CPU mode because AMD GPU passthrough is unavailable in this container."
        else
            ai_warn "AMD GPU runtime devices not ready yet: ${_amd_missing_devices:-unknown}"
            ai "Continuing for now; AMD tuning will try to load kernel modules before services start."
        fi
    fi

    if [[ "${GPU_BACKEND_REQUESTED,,}" != "nvidia" \
        && "${TIER_FORCED:-false}" != "true" \
        && "${GPU_BACKEND:-}" == "nvidia" \
        && "${GPU_MEMORY_TYPE:-discrete}" == "discrete" \
        && "${GPU_VRAM:-0}" -gt 0 \
        && "${GPU_VRAM:-0}" -lt 4096 ]]; then
        apply_cpu_gpu_fallback "Detected NVIDIA GPU has only ${GPU_VRAM}MB VRAM; using CPU/Tier 0 fallback to avoid CUDA OOM loops."
    fi
fi

BACKEND_ID="$GPU_BACKEND"
if [[ "${CAP_LLM_BACKEND:-}" == "cpu" || "${CAP_LLM_BACKEND:-}" == "apple" ]]; then
    BACKEND_ID="${CAP_LLM_BACKEND}"
fi
load_backend_contract "$BACKEND_ID" || true
LLM_HEALTHCHECK_URL="${BACKEND_PUBLIC_HEALTH_URL:-http://localhost:8080/health}"
LLM_PUBLIC_API_PORT="${BACKEND_PUBLIC_API_PORT:-8080}"
OPENCLAW_PROVIDER_NAME_DEFAULT="${BACKEND_PROVIDER_NAME:-local-llama}"
OPENCLAW_PROVIDER_URL_DEFAULT="${BACKEND_PROVIDER_URL:-http://llama-server:8080/v1}"

#-----------------------------------------------------------------------------
# Host architecture detection
#-----------------------------------------------------------------------------
HOST_ARCH=$(detect_host_arch)
log "Host architecture: ${HOST_ARCH}"

#-----------------------------------------------------------------------------
# Secure Boot + NVIDIA auto-fix
#-----------------------------------------------------------------------------
# If detect_gpu found no working GPU, check if it's a fixable driver/Secure Boot issue
# (Only for NVIDIA — AMD APU is handled above)
if [[ "${GPU_BACKEND_FORCED_CPU:-false}" != "true" && $GPU_COUNT -eq 0 && "$GPU_BACKEND" != "amd" ]] && ! $DRY_RUN; then
    fix_nvidia_secure_boot || true
fi

validate_nvidia_blackwell_open_modules

# NVIDIA Driver Compatibility Check
# llama-server (CUDA) requires driver >= 570
if [[ $GPU_COUNT -gt 0 && "$GPU_BACKEND" == "nvidia" ]]; then
    DRIVER_VERSION=""
    if raw_driver=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null); then
        DRIVER_VERSION=$(echo "$raw_driver" | head -1 | cut -d. -f1)
    fi
    if [[ -n "$DRIVER_VERSION" && "$DRIVER_VERSION" =~ ^[0-9]+$ ]]; then
        log "NVIDIA driver: $DRIVER_VERSION"
        if [[ "$DRIVER_VERSION" -lt "$MIN_DRIVER_VERSION" ]]; then
            ai_bad "NVIDIA driver $DRIVER_VERSION is too old. llama-server (CUDA) requires driver >= $MIN_DRIVER_VERSION."
            if nvidia_blackwell_hardware_detected; then
                ai_bad "This is a Blackwell GPU, so install an NVIDIA open kernel module driver."
                ai "  sudo apt install nvidia-open"
                ai "  # or: sudo apt install nvidia-driver-${MIN_DRIVER_VERSION}-open"
                error "Blackwell requires driver >= ${MIN_DRIVER_VERSION} with open kernel modules."
            fi
            ai "Attempting to install a compatible driver..."
            if ! $DRY_RUN; then
                if command -v ubuntu-drivers &> /dev/null; then
                    sudo ubuntu-drivers install nvidia:${MIN_DRIVER_VERSION}-server 2>>"$LOG_FILE" || \
                    sudo apt-get install -y nvidia-driver-${MIN_DRIVER_VERSION} 2>>"$LOG_FILE" || true
                else
                    sudo apt-get install -y nvidia-driver-${MIN_DRIVER_VERSION} 2>>"$LOG_FILE" || true
                fi
                # Check if upgrade succeeded
                if dpkg -l "nvidia-driver-${MIN_DRIVER_VERSION}"* 2>/dev/null | grep -q "^ii"; then
                    ai_ok "NVIDIA driver ${MIN_DRIVER_VERSION} installed."
                    ai_warn "A REBOOT is required before continuing."
                    ai "After rebooting, re-run this installer. It will pick up where it left off."
                    echo ""
                    if $INTERACTIVE; then
                        read -p "  Reboot now? [y/N] " -r < /dev/tty
                        if [[ $REPLY =~ ^[Yy]$ ]]; then
                            sudo reboot
                        fi
                    fi
                    error "Reboot required to load NVIDIA driver ${MIN_DRIVER_VERSION}. Re-run install.sh after rebooting."
                else
                    ai_bad "Driver install failed. Please install NVIDIA driver >= ${MIN_DRIVER_VERSION} manually."
                    ai "  Try: sudo apt install nvidia-driver-${MIN_DRIVER_VERSION}"
                    error "Compatible NVIDIA driver required."
                fi
            else
                log "[DRY RUN] Would install nvidia-driver-${MIN_DRIVER_VERSION}"
            fi
        else
            ai_ok "NVIDIA driver $DRIVER_VERSION (>= $MIN_DRIVER_VERSION required)"
        fi
    else
        ai_warn "Could not determine driver version — continuing anyway"
    fi
fi

#-----------------------------------------------------------------------------
# Intel Arc validation (lspci cross-check, Level Zero, intel_gpu_top)
#-----------------------------------------------------------------------------
if [[ $GPU_COUNT -gt 0 && "$GPU_BACKEND" == "intel" ]]; then

    # 1. Cross-validate with lspci — confirm the Arc card is visible to the PCI bus
    #    detect_gpu() already confirmed it via sysfs; this adds a human-readable log line.
    _arc_pci_name=""
    if command -v lspci &>/dev/null; then
        _arc_pci_name=$(lspci 2>/dev/null \
            | grep -i 'VGA\|Display\|3D' \
            | grep -i 'Intel.*Arc\|Arc.*Intel\|Intel.*A[0-9][0-9][0-9]\|Intel.*B[0-9][0-9][0-9]' \
            | head -1 \
            | sed 's/.*: //')
        if [[ -n "$_arc_pci_name" ]]; then
            ai_ok "lspci: $_arc_pci_name"
        else
            # Broader fallback: any Intel VGA/3D controller (covers cards lspci names without "Arc")
            _arc_pci_name=$(lspci 2>/dev/null \
                | grep -i 'VGA\|Display\|3D' \
                | grep -i 'Intel' \
                | head -1 \
                | sed 's/.*: //')
            [[ -n "$_arc_pci_name" ]] && ai_ok "lspci: $_arc_pci_name (Intel GPU)" \
                || ai_warn "lspci: Intel Arc sysfs entry found but lspci VGA entry not visible — IOMMU or PCIe bridge may obscure it"
        fi
    else
        ai_warn "lspci not found (install pciutils for richer GPU info); sysfs detection succeeded"
    fi

    # 2. Check Level Zero runtime — required for SYCL inference
    #    level-zero-loader provides /usr/lib/libze_loader.so.1 or the ze_info binary.
    _level_zero_ok=false
    if command -v ze_info &>/dev/null; then
        _level_zero_ok=true
        _ze_version=$(ze_info 2>/dev/null | grep -i 'driver version\|Driver Version' | head -1 | xargs || true)
        ai_ok "Level Zero: available${_ze_version:+ — $_ze_version}"
    elif ldconfig -p 2>/dev/null | grep -q 'libze_loader'; then
        _level_zero_ok=true
        ai_ok "Level Zero: libze_loader found"
    elif [[ -f /usr/lib/x86_64-linux-gnu/libze_loader.so.1 || \
            -f /usr/lib/libze_loader.so.1 ]]; then
        _level_zero_ok=true
        ai_ok "Level Zero: libze_loader.so.1 present"
    fi
    if [[ "$_level_zero_ok" == "false" ]]; then
        ai_warn "Level Zero runtime not detected."
        ai "  The SYCL backend requires Level Zero to offload inference to the Arc GPU."
        ai "  Install: sudo apt install intel-level-zero-gpu level-zero"
        ai "  Without it, llama-server will fall back to CPU-only mode inside the container."
    fi

    # 3. Check /dev/dri — device node needed for Docker passthrough
    if [[ -c /dev/dri/renderD128 || -d /dev/dri ]]; then
        _render_node=$(ls /dev/dri/renderD* 2>/dev/null | head -1 || true)
        ai_ok "/dev/dri: ${_render_node:-/dev/dri present} (GPU device pass-through available)"
    else
        ai_warn "/dev/dri not found — Docker GPU device pass-through may fail."
        ai "  Ensure the Intel i915/xe kernel module is loaded: modprobe i915"
    fi

    # 4. Check intel_gpu_top (from intel-gpu-tools) — non-fatal, used for monitoring
    if command -v intel_gpu_top &>/dev/null; then
        _igt_ver=$(intel_gpu_top --version 2>/dev/null | head -1 || true)
        ai_ok "intel_gpu_top: available${_igt_ver:+ ($_igt_ver)}"
    else
        log "intel_gpu_top not found (optional — used for GPU utilisation monitoring)"
        log "  Install: sudo apt install intel-gpu-tools"
    fi

    # 5. Check video/render group membership (needed for rootless Docker device access)
    _missing_groups=()
    for _grp in video render; do
        if ! id -nG 2>/dev/null | grep -qw "$_grp"; then
            _missing_groups+=("$_grp")
        fi
    done
    if [[ ${#_missing_groups[@]} -gt 0 ]]; then
        ai_warn "Current user is not in group(s): ${_missing_groups[*]}"
        ai "  Run: sudo usermod -aG ${_missing_groups[*]} \$USER   (then re-login)"
        ai "  Without this, Docker cannot access /dev/dri inside the container."
    else
        ai_ok "User groups: video + render membership confirmed"
    fi

    # 6. Log final Arc summary
    _arc_vram_gb=$((GPU_VRAM / 1024))
    ai_ok "Intel Arc detected: $GPU_NAME (${_arc_vram_gb} GB VRAM, device ${GPU_DEVICE_ID:-unknown})"
    log "Intel Arc backend: GPU_BACKEND=intel, VRAM=${GPU_VRAM}MB, Level Zero=${_level_zero_ok}"
fi

# -----------------------------------------------------------------------------
# NVIDIA Multi-GPU Topology Detection
# -----------------------------------------------------------------------------
GPU_TOPOLOGY_JSON="{}"
GPU_HAS_NVLINK="false"
GPU_TOTAL_VRAM=0
if [[ $GPU_COUNT -gt 1 && "$GPU_BACKEND" == "nvidia" ]]; then
    ai "Detecting multi-GPU topology..."
    if [[ -f "$SCRIPT_DIR/installers/lib/nvidia-topo.sh" ]]; then
        # Source the topology detection script
        source "$SCRIPT_DIR/installers/lib/nvidia-topo.sh"
        
        # Run topology detection and capture JSON output
        GPU_TOPOLOGY_JSON=$(detect_nvidia_topo 2>>"$LOG_FILE") || {
            warn "Multi-GPU topology detection failed — multi-GPU configuration disabled"
            ai_warn "Could not detect GPU topology. Multi-GPU features will be skipped."
            ai_warn "Check $LOG_FILE for details. You can re-run the installer after fixing the issue."
            GPU_TOPOLOGY_JSON="{}"
        }
        
        # Extract key topology information for tier assignment
        if [[ -n "$GPU_TOPOLOGY_JSON" && "$GPU_TOPOLOGY_JSON" != "{}" ]]; then
            GPU_HAS_NVLINK=$(echo "$GPU_TOPOLOGY_JSON" | jq -r '[.links[] | select(.link_type | startswith("NV"))] | length > 0')
            GPU_TOTAL_VRAM=$(echo "$GPU_TOPOLOGY_JSON" | jq -r '[.gpus[].memory_gb] | add * 1024 | floor')
            log "Multi-GPU topology: NVLink=$GPU_HAS_NVLINK, Total VRAM=${GPU_TOTAL_VRAM}MB"
        else
            log "topology detection returned empty, using basic GPU info"
            GPU_TOTAL_VRAM=$((GPU_VRAM * GPU_COUNT))
        fi
    else
        log "NVIDIA topology detection script not found, skipping detailed topology analysis"
        GPU_TOTAL_VRAM=$((GPU_VRAM * GPU_COUNT))
    fi
fi

# -----------------------------------------------------------------------------
# AMD Multi-GPU Topology Detection
# -----------------------------------------------------------------------------
if [[ $GPU_COUNT -gt 1 && "$GPU_BACKEND" == "amd" ]]; then
    ai "Detecting AMD multi-GPU topology..."
    if [[ -f "$SCRIPT_DIR/installers/lib/amd-topo.sh" ]]; then
        source "$SCRIPT_DIR/installers/lib/amd-topo.sh"

        GPU_TOPOLOGY_JSON=$(detect_amd_topo 2>>"$LOG_FILE") || {
            warn "AMD multi-GPU topology detection failed — using fallback"
            ai_warn "Could not detect AMD GPU topology. Using default PCIe configuration."
            GPU_TOPOLOGY_JSON="{}"
        }

        if [[ -n "$GPU_TOPOLOGY_JSON" && "$GPU_TOPOLOGY_JSON" != "{}" ]]; then
            GPU_TOTAL_VRAM=$(echo "$GPU_TOPOLOGY_JSON" | jq -r '[.gpus[].memory_gb] | add * 1024 | floor')
            log "AMD multi-GPU topology: Total VRAM=${GPU_TOTAL_VRAM}MB"
        else
            log "AMD topology detection returned empty, using basic GPU info"
            GPU_TOTAL_VRAM=$GPU_VRAM
        fi
    else
        log "AMD topology detection script not found, using basic GPU info"
        GPU_TOTAL_VRAM=$GPU_VRAM
    fi
fi

# Auto-detect tier if not specified
if [[ -z "$TIER" ]]; then
    PROFILE_TIER="$(normalize_profile_tier "${CAP_RECOMMENDED_TIER:-}")"
    if [[ -n "$PROFILE_TIER" ]]; then
        TIER="$PROFILE_TIER"
    elif [[ "$GPU_BACKEND" == "intel" ]]; then
        # Intel Arc discrete GPU — SYCL backend via llama.cpp
        # A770 = 16 GB  → ARC  (≥12 GB)
        # A750 =  8 GB  → ARC_LITE
        # A380 =  6 GB  → ARC_LITE
        arc_vram_gb=$((GPU_VRAM / 1024))
        if [[ $arc_vram_gb -ge 12 ]]; then
            TIER="ARC"
        else
            TIER="ARC_LITE"
        fi
    elif [[ "$GPU_BACKEND" == "amd" && "$GPU_MEMORY_TYPE" == "unified" ]]; then
        # Strix Halo binary tier system
        unified_gb=$((GPU_VRAM / 1024))
        if [[ $unified_gb -ge 90 ]]; then
            TIER="SH_LARGE"
        else
            TIER="SH_COMPACT"
        fi
    elif [[ "$GPU_BACKEND" == "nvidia" && "$GPU_MEMORY_TYPE" == "unified" ]]; then
        # NVIDIA Grace Blackwell (GB10, GB200) — unified CPU+GPU memory
        unified_gb=$((GPU_VRAM / 1024))
        if [[ $unified_gb -ge 90 ]]; then
            TIER="NV_ULTRA"
        elif [[ $unified_gb -ge 48 ]]; then
            TIER=4
        elif [[ $unified_gb -ge 20 ]]; then
            TIER=3
        elif [[ $unified_gb -ge 12 ]]; then
            TIER=2
        else
            TIER=1
        fi
        log "NVIDIA unified memory: ${unified_gb}GB → Tier $TIER"
    elif [[ $GPU_VRAM -ge 90000 ]]; then
        TIER="NV_ULTRA"
    elif [[ $GPU_COUNT -ge 2 ]]; then
        # Enhanced multi-GPU tier assignment based on topology
        if [[ "$GPU_HAS_NVLINK" == "true" ]]; then
            # High-bandwidth interconnect (NVLink)
            if [[ $GPU_COUNT -ge 4 || $GPU_TOTAL_VRAM -ge 90000 ]]; then
                TIER="NV_ULTRA"
            else
                TIER=4
            fi
        else
            # PCIe or other interconnect
            if [[ $GPU_COUNT -ge 4 ]]; then
                TIER=4
            elif [[ $GPU_TOTAL_VRAM -ge 40000 ]]; then
                TIER=4
            else
                TIER=3
            fi
        fi
    elif [[ $GPU_VRAM -ge 40000 ]]; then
        TIER=4
    elif [[ $GPU_VRAM -ge 20000 ]] || [[ $RAM_GB -ge 96 ]]; then
        TIER=3
    elif [[ $GPU_VRAM -ge 12000 ]] || [[ $RAM_GB -ge 48 ]]; then
        TIER=2
    elif [[ $GPU_VRAM -lt 4000 ]] && [[ $RAM_GB -lt 12 ]]; then
        TIER=0
    else
        TIER=1
    fi
    log "Auto-detected tier: $TIER"
else
    log "Using specified tier: $TIER"
fi

# Resolve compose overlay files
resolve_compose_config

# Validate compose stack syntax before proceeding (skip on fresh install — .env
# is not generated until phase 06, so variable interpolation would fail)
if [[ -n "${COMPOSE_FLAGS:-}" ]] && [[ -f "$INSTALL_DIR/.env" ]]; then
    ai "Validating compose stack configuration..."
    if "$SCRIPT_DIR/scripts/validate-compose-stack.sh" --compose-flags "$COMPOSE_FLAGS" --env-file "$INSTALL_DIR/.env" --quiet >> "$LOG_FILE" 2>&1; then
        ai_ok "Compose stack validated"
    else
        ai "Compose validation found issues (will validate when services start)"
        log "Compose validation deferred — .env may be stale from a previous install"
    fi
fi

# Resolve tier → model/GGUF/context
if [[ -z "${MODEL_PROFILE:-}" ]]; then
    if [[ -f "$INSTALL_DIR/.env" ]]; then
        _existing_model_profile=$(grep -m1 '^MODEL_PROFILE=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '\r' || true)
        if [[ -n "$_existing_model_profile" ]]; then
            MODEL_PROFILE="$_existing_model_profile"
        else
            MODEL_PROFILE="qwen"
        fi
    else
        MODEL_PROFILE="qwen"
    fi
fi
resolve_tier_config

# Refine the tier's model choice using the versioned catalog before any GGUF is
# downloaded. This keeps install-time selection aligned with the dashboard
# oracle while preserving the tier map as a no-Python fallback.
if [[ "${ODS_DISABLE_CATALOG_MODEL_SELECTOR:-false}" != "true" && "${TIER:-}" != "CLOUD" ]]; then
    _selector_script="$SCRIPT_DIR/scripts/select-model.py"
    _selector_catalog="$SCRIPT_DIR/config/model-library.json"
    if [[ -f "$_selector_script" && -f "$_selector_catalog" ]]; then
        _selector_python=""
        if [[ -f "$SCRIPT_DIR/lib/python-cmd.sh" ]]; then
            # shellcheck source=/dev/null
            . "$SCRIPT_DIR/lib/python-cmd.sh"
            _selector_python="$(ods_detect_python_cmd || true)"
        fi
        if [[ -z "$_selector_python" ]]; then
            if command -v python3 >/dev/null 2>&1; then
                _selector_python="python3"
            elif command -v python >/dev/null 2>&1; then
                _selector_python="python"
            fi
        fi
        if [[ -n "$_selector_python" ]]; then
            _selector_env="$("$_selector_python" "$_selector_script" \
                --catalog "$_selector_catalog" \
                --backend "${GPU_BACKEND:-unknown}" \
                --memory-type "${GPU_MEMORY_TYPE:-discrete}" \
                --vram-mb "${GPU_VRAM:-0}" \
                --ram-gb "${RAM_GB:-0}" \
                --profile "${MODEL_PROFILE_EFFECTIVE:-${MODEL_PROFILE:-qwen}}" \
                --tier "${TIER:-1}" \
                --host-arch "${HOST_ARCH:-unknown}" \
                --installable-only \
                --env 2>>"$LOG_FILE" || true)"
            if [[ -n "$_selector_env" ]]; then
                if command -v load_model_selector_env_from_output >/dev/null 2>&1; then
                    load_model_selector_env_from_output <<< "$_selector_env"
                    log "Catalog model selector: ${MODEL_RECOMMENDATION_REASON:-$LLM_MODEL}"
                else
                    log "Catalog model selector output ignored; safe env loader unavailable"
                fi
            else
                log "Catalog model selector unavailable; using tier-map model ${LLM_MODEL}"
            fi
        else
            log "Python unavailable for catalog model selector; using tier-map model ${LLM_MODEL}"
        fi
    fi
fi

# Display hardware summary with nice formatting
CPU_INFO=$(grep "model name" /proc/cpuinfo 2>/dev/null | head -1 | cut -d: -f2 | xargs || echo "Unknown")
if [[ "$INTERACTIVE" == "true" ]]; then
    show_hardware_summary "$GPU_NAME" "$((GPU_VRAM / 1024))" "$CPU_INFO" "$RAM_GB" "$DISK_AVAIL"

    if [[ "$TIER" == "CLOUD" ]]; then
        SPEED_EST="cloud API"
        USERS_EST="depends on API tier"
    else
        SPEED_EST="benchmark after first launch"
        USERS_EST="measured after local benchmark"
    fi
    show_tier_recommendation "$TIER" "$LLM_MODEL" "$SPEED_EST" "$USERS_EST"
else
    success "Configuration: Tier $TIER ($TIER_NAME)"
    log "  Model: $LLM_MODEL"
    log "  Context: ${MAX_CONTEXT} tokens"
fi
