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
#           COMPOSE_FILE, COMPOSE_FLAGS, RAM_KB, RAM_GB, MODEL_TIER_RAM_GB,
#           WINDOWS_HOST_RAM_KB, RAM_IS_WSL,
#           DISK_AVAIL, BACKEND_ID,
#           LLM_HEALTHCHECK_URL, LLM_PUBLIC_API_PORT,
#           GPU_TOPOLOGY_JSON, GPU_HAS_NVLINK, GPU_TOTAL_VRAM,
#           LLM_MODEL_SIZE_MB, AMD_GFX_TARGET, AMD_INFERENCE_BACKEND
#
# Modder notes:
#   Change tier auto-detection thresholds or add new hardware classes here.
# ============================================================================

# Isolated phase reuse (tests) gets the route predicate installers/lib/
# native-llm.sh gives install-core: a host-native llama-server is in use.
declare -F ods_native_llm_requested >/dev/null 2>&1 \
    || ods_native_llm_requested() { [[ -n "${NATIVE_LLM_BASE_URL:-}" ]]; }

[[ -f "${SCRIPT_DIR:-}/lib/safe-env.sh" ]] && . "$SCRIPT_DIR/lib/safe-env.sh"
. "$SCRIPT_DIR/installers/lib/wsl-memory.sh"

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
# An owner choice of the AMD llama.cpp image (vulkan or rocm) from the
# environment; otherwise the retained .env value, then the hardware decides.
AMD_INFERENCE_BACKEND_REQUESTED="${AMD_INFERENCE_BACKEND:-}"

# Keep runtime budgets separate from the Windows physical memory report. This
# shared probe also applies to cloud installs before their early return.
# Profile eligibility and persisted RAM remain the VM's addressable memory;
# the summary reports the host total separately without enlarging that budget.
ods_detect_runtime_ram || error "Could not read the runtime RAM capacity."
_ram_display="$(ods_format_memory_kib "$RAM_KB")"
_host_ram_display=""
if [[ "$RAM_IS_WSL" == true ]]; then
    _wsl_headroom_gb=$((RAM_GB - MODEL_TIER_RAM_GB))
    if [[ -n "$WINDOWS_HOST_RAM_KB" ]]; then
        _host_ram_display="$(ods_format_memory_kib "$WINDOWS_HOST_RAM_KB")"
        log "WSL2 detected — Windows host RAM: ${_host_ram_display}; WSL RAM: ${_ram_display}; tier budget: ${MODEL_TIER_RAM_GB} GiB (${_wsl_headroom_gb} GiB reserved for ODS services)"
    else
        log "WSL2 detected — could not query Windows host RAM; WSL RAM: ${_ram_display}; tier budget: ${MODEL_TIER_RAM_GB} GiB (${_wsl_headroom_gb} GiB reserved for ODS services)"
    fi
else
    log "RAM: ${_ram_display}"
fi

# Cloud mode: skip GPU detection entirely
if [[ "${ODS_MODE:-local}" == "cloud" ]]; then
    ai "Cloud mode — skipping GPU detection"
    GPU_BACKEND="cpu"
    GPU_NAME="Cloud (no local GPU)"
    GPU_VRAM=0
    GPU_COUNT=0
    GPU_MEMORY_TYPE="none"
    TIER="CLOUD"
    DISK_AVAIL=$(df -Pk "$HOME" 2>/dev/null | tail -1 | awk '{printf "%d", $4 / 1048576}')
    BACKEND_ID="cpu"
    LLM_HEALTHCHECK_URL="http://127.0.0.1:4000/health/readiness"
    LLM_PUBLIC_API_PORT="4000"
    resolve_compose_config
    resolve_tier_config
    if [[ "$INTERACTIVE" == "true" ]]; then
        success "Cloud mode: LLM via LiteLLM gateway (no GPU required)"
        log "  Runtime RAM: ${_ram_display}, Disk: ${DISK_AVAIL}GB"
    fi
    # Skip rest of detection phase
    return 0 2>/dev/null || true
fi

ai "Reading hardware telemetry..."

load_capability_profile || true

# Disk Detection
# Check free space on the filesystem where ODS will actually be installed.
# INSTALL_DIR may not exist yet, so walk up to the nearest existing ancestor
# so df always receives a valid path. Falls back to $HOME if nothing resolves.
_disk_probe_path="${INSTALL_DIR:-$HOME/ods}"
while [[ -n "$_disk_probe_path" ]] && [[ ! -e "$_disk_probe_path" ]]; do
    _disk_probe_path="$(dirname "$_disk_probe_path")"
done
_disk_probe_path="${_disk_probe_path:-$HOME}"
DISK_AVAIL=$(df -Pk "$_disk_probe_path" 2>/dev/null | tail -1 | awk '{printf "%d", $4 / 1048576}')
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

    # AMD runs the llama.cpp image with Vulkan unless ROCm is requested or the
    # GPU has no Vulkan driver (Instinct). The choice decides which device
    # nodes are required (/dev/kfd only for ROCm) and the Compose overlay.
    AMD_GFX_TARGET=""
    if [[ "$GPU_BACKEND" == "amd" ]]; then
        [[ -f "$SCRIPT_DIR/installers/lib/amd-topo.sh" ]] && . "$SCRIPT_DIR/installers/lib/amd-topo.sh"
        _amd_gfx_targets=()
        if declare -F amd_gfx_targets >/dev/null 2>&1; then
            # An unreadable target leaves the choice to the retained value or Vulkan.
            mapfile -t _amd_gfx_targets < <(amd_gfx_targets 2>>"$LOG_FILE" || true)
        fi
        for _amd_target in "${_amd_gfx_targets[@]}"; do
            AMD_GFX_TARGET="$(ods_amd_normalize_gfx_target "$_amd_target")"
            [[ -z "$AMD_GFX_TARGET" ]] || break
        done
        # No .env (a fresh install) or no saved value: the hardware decides.
        _amd_retained_backend="$(external_llm_env_value "$INSTALL_DIR/.env" AMD_INFERENCE_BACKEND 2>/dev/null || true)"
        if ! AMD_INFERENCE_BACKEND="$(ods_amd_select_inference_backend \
            "$AMD_INFERENCE_BACKEND_REQUESTED" "$_amd_retained_backend" "${_amd_gfx_targets[@]}")"; then
            error "AMD_INFERENCE_BACKEND must be vulkan or rocm, got: $AMD_INFERENCE_BACKEND_REQUESTED"
        fi
        log "AMD llama.cpp backend: $AMD_INFERENCE_BACKEND (gfx target: ${AMD_GFX_TARGET:-unknown})"
        if [[ "$AMD_INFERENCE_BACKEND" == rocm \
            && -z "$(ods_amd_hsa_override_for_target rocm "$AMD_GFX_TARGET")" \
            && -n "$AMD_GFX_TARGET" \
            && " $ODS_AMD_ROCM_IMAGE_TARGETS " != *" $AMD_GFX_TARGET "* ]]; then
            ai_warn "The ROCm image is not built for ${AMD_GFX_TARGET}; set AMD_INFERENCE_BACKEND=vulkan if the model does not load."
        fi
        unset _amd_gfx_targets _amd_target _amd_retained_backend
    fi
    export AMD_GFX_TARGET AMD_INFERENCE_BACKEND

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
LLM_HEALTHCHECK_URL="${BACKEND_PUBLIC_HEALTH_URL:-http://127.0.0.1:8080/health}"
LLM_PUBLIC_API_PORT="${BACKEND_PUBLIC_API_PORT:-8080}"

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
            if ods_is_wsl_host; then
                ods_wsl_nvidia_driver_too_old "$DRIVER_VERSION"
            fi
            ai_bad "NVIDIA driver $DRIVER_VERSION is too old. llama-server (CUDA) requires driver >= $MIN_DRIVER_VERSION."
            if nvidia_blackwell_hardware_detected; then
                ai_bad "This is a Blackwell GPU, so install an NVIDIA open kernel module driver."
                ai "  sudo apt install nvidia-open"
                ai "  # or: sudo apt install nvidia-driver-${MIN_DRIVER_VERSION}-open"
                error "Blackwell requires driver >= ${MIN_DRIVER_VERSION} with open kernel modules."
            fi
            ai "Attempting to install a compatible driver..."
            if ! $DRY_RUN; then
                if ! ods_sudo_available; then
                    error "NVIDIA driver ${DRIVER_VERSION} is below ${MIN_DRIVER_VERSION}, but privileged driver installation is unavailable. Upgrade the driver manually, then re-run ODS."
                fi
                if command -v ubuntu-drivers &> /dev/null; then
                    ods_sudo ubuntu-drivers install "nvidia:${MIN_DRIVER_VERSION}-server" 2>>"$LOG_FILE" || \
                    ods_sudo apt-get install -y "nvidia-driver-${MIN_DRIVER_VERSION}" 2>>"$LOG_FILE" || true
                else
                    ods_sudo apt-get install -y "nvidia-driver-${MIN_DRIVER_VERSION}" 2>>"$LOG_FILE" || true
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
                            ods_sudo reboot
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

# The pinned Speaches CUDA image requires driver 575+, which is stricter than
# the llama-server CUDA floor. Keep the primary NVIDIA LLM path enabled on
# older drivers while selecting CPU Whisper and suppressing only its GPU
# overlay. This mirrors the existing Windows installer contract.
ods_configure_whisper_acceleration "$GPU_BACKEND" "${DRIVER_VERSION:-0}"
if [[ "$WHISPER_ACCELERATION_FORCED_CPU" == "true" ]]; then
    ai_warn "Whisper CUDA requires NVIDIA driver ${MIN_WHISPER_CUDA_DRIVER_VERSION}+; detected ${DRIVER_VERSION:-unknown}. Using CPU Whisper while keeping GPU inference enabled."
elif [[ "$WHISPER_ACCELERATION" == "cpu" && "$GPU_BACKEND" == "nvidia" ]]; then
    log "Whisper CPU acceleration explicitly selected; NVIDIA inference remains enabled"
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
    elif [[ $GPU_VRAM -ge 20000 ]] || [[ $MODEL_TIER_RAM_GB -ge 96 ]]; then
        TIER=3
    elif [[ $GPU_VRAM -ge 12000 ]] || [[ $MODEL_TIER_RAM_GB -ge 48 ]]; then
        TIER=2
    elif [[ $GPU_VRAM -lt 4000 ]] && [[ $MODEL_TIER_RAM_GB -lt 12 ]]; then
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
    if [[ -f "$_selector_script" && -f "$_selector_catalog" ]] \
        && declare -F ods_run_catalog_selector >/dev/null 2>&1; then
        _selector_python="$(ods_model_selector_python)"
        if [[ -n "$_selector_python" ]]; then
            PIXEL_AGENT_MODEL_READY=unknown
            _pixel_default_selector=false
            if declare -F ods_pixel_resolve_enablement >/dev/null 2>&1 \
                && [[ "${ODS_MODE:-local}" == "local" ]] \
                && [[ -z "${EXTERNAL_LLM_URL:-}" ]] \
                && ! ods_native_llm_requested \
                && [[ "$(ods_pixel_resolve_enablement "${ENABLE_PIXEL:-auto}" 2>/dev/null || true)" == "pixel" ]]; then
                _pixel_default_selector=true
            fi
            # Pixel adapts its prompt and tool surface to the selected route;
            # catalog qualification is performance guidance, never an access
            # gate. For the default agent, choose the strongest installable
            # model that fits measured hardware instead of inheriting the
            # bootstrap tier's download-size ceiling.
            _selector_max_size_mb="${LLM_MODEL_SIZE_MB:-0}"
            if [[ "$_pixel_default_selector" == true ]]; then
                _selector_max_size_mb=0
                # The selector overwrites this when its chosen model has an
                # explicit Pixel capability verdict. Fail closed if selection
                # itself cannot produce trusted metadata.
                PIXEL_AGENT_MODEL_READY=false
            fi
            # Hermes is on by default and needs 64K context: prefer models
            # that fit at 64K themselves (a soft floor; a smaller context is
            # chosen only when nothing fits at 64K). Phase 03 re-checks the
            # pick once the feature set is final.
            _run_catalog_selector() {
                ods_run_catalog_selector "$_selector_python" "$_selector_max_size_mb" \
                    --min-context "$ODS_HERMES_MIN_CONTEXT" \
                    "$@"
            }
            ODS_SELECTOR_MAX_SIZE_MB="$_selector_max_size_mb"
            _selector_status=0
            _selector_env="$(_run_catalog_selector 2>>"$LOG_FILE")" || _selector_status=$?
            if [[ "$_selector_status" -eq 2 ]]; then
                error "No catalog model fits the detected memory and selected profile. Choose a smaller model profile or use cloud mode; refusing an unsafe tier-map fallback."
                exit 1
            fi
            if [[ "$_pixel_default_selector" == true && -n "$_selector_env" ]]; then
                log "Pixel default selected the strongest installable hardware-fit model; catalog qualification remains advisory"
            fi
            export PIXEL_AGENT_MODEL_READY
            unset -f _run_catalog_selector
            unset _selector_max_size_mb _pixel_default_selector
            if [[ -n "$_selector_env" ]]; then
                if command -v load_model_selector_env_from_output >/dev/null 2>&1; then
                    load_model_selector_env_from_output <<< "$_selector_env"
                    # With an API selected, the API serves the model; the local
                    # pick only guides matching a name in phase 02b.
                    [[ -n "${EXTERNAL_LLM_URL:-}" ]] \
                        || log "Catalog model selector: ${MODEL_RECOMMENDATION_REASON:-$LLM_MODEL}"
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

# Host-native llama-server: llama-server on the Windows host serves the model
# Windows setup chose and loaded. This Linux host cannot see that GPU, so the
# catalog pick above describes a model nobody serves. Record the catalog model
# the served GGUF names instead, at the context it was loaded with, so .env
# describes what is served and the next rerun can check it
# (scripts/preserve-active-model.py --project-native-llm). Stop rather than
# record another model.
_native_python=""
if ods_native_llm_requested && [[ "${TIER:-}" != "CLOUD" ]]; then
    _native_python="${_selector_python:-}"
    if [[ -z "$_native_python" ]] && declare -F ods_model_selector_python >/dev/null 2>&1; then
        _native_python="$(ods_model_selector_python)"
    fi
    if [[ -z "$_native_python" ]] || ! declare -F load_model_selector_env_from_output >/dev/null 2>&1; then
        error "Python and the installer's safe env loader are required to record the model llama-server serves on Windows."
        exit 1
    fi
    # The helper takes the GGUF file name, or a retired Lemonade model id
    # from an older Windows setup, and matches exactly one catalog model.
    _native_args=(--env "$INSTALL_DIR/.env"
        --catalog "$SCRIPT_DIR/config/model-library.json"
        --imports "$INSTALL_DIR/data/model-imports.json"
        --models-dir "$INSTALL_DIR/data/models"
        --project-native-llm "${NATIVE_LLM_MODEL:-${NATIVE_LLM_LEGACY_MODEL_ID:-}}")
    [[ -z "${NATIVE_LLM_CONTEXT_SIZE:-}" ]] || _native_args+=(--context "$NATIVE_LLM_CONTEXT_SIZE")
    if ! _native_model_env="$("$_native_python" "$SCRIPT_DIR/scripts/preserve-active-model.py" \
            "${_native_args[@]}" 2>>"$LOG_FILE")"; then
        error "llama-server on Windows serves ${NATIVE_LLM_MODEL:-${NATIVE_LLM_LEGACY_MODEL_ID:-an unnamed model}}, which is not in the ODS model catalog, so ODS cannot record it or verify it on updates. Choose a catalog model in the ODS Portal and rerun."
        exit 1
    fi
    # Drop the CPU pick's runtime settings before loading the served model's
    # contract (the loader omits unset optional values).
    unset MODEL_RUNTIME_PROFILE MODEL_RUNTIME_PROFILE_LABEL MODEL_RUNTIME_PROFILE_SOURCE
    unset LLAMA_SERVER_IMAGE LLAMA_SERVER_MEMORY_LIMIT
    unset LLAMA_CPP_RELEASE_TAG_OVERRIDE LLAMA_CPP_SERVER_BINARY
    unset LLAMA_ARG_FLASH_ATTN LLAMA_ARG_CACHE_TYPE_K LLAMA_ARG_CACHE_TYPE_V
    unset LLAMA_ARG_N_CPU_MOE LLAMA_ARG_NO_CACHE_PROMPT
    unset LLAMA_ARG_CHECKPOINT_EVERY_NT LLAMA_ARG_SPEC_TYPE
    unset LLAMA_ARG_CTX_CHECKPOINTS LLAMA_ARG_CACHE_RAM
    unset LLAMA_ARG_SPEC_DRAFT_N_MAX LLAMA_ARG_SPLIT_MODE LLAMA_ARG_TENSOR_SPLIT
    unset MODEL_RECOMMENDED_ALTERNATIVES
    load_model_selector_env_from_output <<< "$_native_model_env"
    # A retired Lemonade id is now the GGUF file name llama-server serves.
    NATIVE_LLM_MODEL="$GGUF_FILE"
    MODEL_RECOMMENDATION_REASON="llama-server on the Windows host serves ${LLM_MODEL}${NATIVE_LLM_GPU_NAME:+ on ${NATIVE_LLM_GPU_NAME}}; Windows setup chose it for that GPU."
    log "Host-native llama-server model recorded from its catalog entry: ${LLM_MODEL} (${GGUF_FILE}) at ${MAX_CONTEXT}"
    unset _native_args _native_model_env
fi

# The tier/catalog result is a recommendation.  A valid local model already
# activated through the Dashboard is operator state and must survive routine
# installer reruns.  Keep those two concepts separate so updates can advertise
# a newer recommendation without silently replacing the live agent model.
INSTALLER_RECOMMENDED_MODEL="${LLM_MODEL:-}"
INSTALLER_RECOMMENDED_GGUF="${GGUF_FILE:-}"
INSTALLER_RECOMMENDED_CONTEXT="${MAX_CONTEXT:-}"
MODEL_SELECTION_SOURCE="installer"
if [[ -f "$INSTALL_DIR/.env" && "${ODS_RESELECT_MODEL:-false}" != "true" && "${TIER:-}" != "CLOUD" ]]; then
    # The model of a host-native llama-server belongs to Windows setup, which
    # passes it on every run. A rerun without it must not quietly switch the
    # install to a model in this Linux environment. (The helper fails only
    # without .env, which the condition above rules out.)
    _retained_native="$(external_llm_env_value "$INSTALL_DIR/.env" NATIVE_LLM_BASE_URL || true)"
    # An API selected for this run (--external-llm-url, Windows
    # -ExternalLlmUrl) is the owner's explicit switch away from the Windows
    # llama-server; phase 06 then writes the route without it.
    if ! ods_native_llm_requested && [[ "${ODS_MODE_EXPLICIT:-false}" != "true" && -n "$_retained_native" \
            && -z "${EXTERNAL_LLM_URL:-}" ]]; then
        error "This installation uses a llama-server that Windows setup manages. Rerun Windows setup, pass --native-llm-url, or use --reselect-model to choose a model in this Linux environment."
        exit 1
    fi
    unset _retained_native
    _preserve_script="$SCRIPT_DIR/scripts/preserve-active-model.py"
    if ods_native_llm_requested; then
        # The served model was recorded above. Keep the saved selection's
        # owner and context when it names the same model; repair only the
        # mismatch earlier installs wrote (the Linux host's own pick saved
        # next to the served model); stop on any other difference.
        _native_status=0
        _native_args=(--env "$INSTALL_DIR/.env"
            --catalog "$SCRIPT_DIR/config/model-library.json"
            --imports "$INSTALL_DIR/data/model-imports.json"
            --models-dir "$INSTALL_DIR/data/models"
            --state "$INSTALL_DIR/data/model-state.json")
        # The context Windows loaded is what is served, so it wins over the
        # saved one; without it the saved context stays.
        [[ -z "${NATIVE_LLM_CONTEXT_SIZE:-}" ]] || _native_args+=(--context "$NATIVE_LLM_CONTEXT_SIZE")
        _native_model_env="$("$_native_python" "$_preserve_script" "${_native_args[@]}" \
            --native-llm --served-model "$GGUF_FILE" 2>>"$LOG_FILE")" || _native_status=$?
        if [[ "$_native_status" -ne 0 ]]; then
            # Earlier installs saved this host's own catalog pick next to the
            # served model. The helper re-records only that installer-written
            # mismatch, from the served model, and logs the change.
            _native_status=3
            if ! _native_model_env="$("$_native_python" "$_preserve_script" "${_native_args[@]}" \
                    --repair-native-llm "$GGUF_FILE" 2>>"$LOG_FILE")"; then
                error "The saved model selection differs from the model llama-server serves on Windows (${GGUF_FILE}) and was not written by the installer. Activate the model again in the Dashboard, rerun setup from the ODS Portal, or use --reselect-model."
                exit 1
            fi
            ai_warn "Corrected the saved model details to describe the model llama-server serves on Windows; the served model did not change (details in the install log)."
        fi
        if [[ -n "$_native_model_env" ]]; then
            unset MODEL_RUNTIME_PROFILE MODEL_RUNTIME_PROFILE_LABEL MODEL_RUNTIME_PROFILE_SOURCE
            unset LLAMA_SERVER_IMAGE
            load_model_selector_env_from_output <<< "$_native_model_env"
            [[ "$_native_status" -ne 0 ]] \
                || log "Kept the saved host-native model across installer rerun: ${LLM_MODEL} (${GGUF_FILE}) at ${MAX_CONTEXT}, selected by ${MODEL_SELECTION_SOURCE}"
        fi
        unset _native_status _native_args _native_model_env
    elif [[ -f "$_preserve_script" ]]; then
        if [[ -z "${_selector_python:-}" ]]; then
            if [[ -f "$SCRIPT_DIR/lib/python-cmd.sh" ]]; then
                # shellcheck source=/dev/null
                . "$SCRIPT_DIR/lib/python-cmd.sh"
                _selector_python="$(ods_detect_python_cmd || true)"
            elif command -v python3 >/dev/null 2>&1; then
                _selector_python="python3"
            fi
        fi
        if [[ -n "${_selector_python:-}" ]]; then
            _preserve_status=0
            _preserved_model_env="$("$_selector_python" "$_preserve_script" \
                --env "$INSTALL_DIR/.env" \
                --catalog "$SCRIPT_DIR/config/model-library.json" \
                --imports "$INSTALL_DIR/data/model-imports.json" \
                --models-dir "$INSTALL_DIR/data/models" \
                --state "$INSTALL_DIR/data/model-state.json" \
                --backend "${GPU_BACKEND:-unknown}" \
                --memory-type "${GPU_MEMORY_TYPE:-discrete}" \
                --vram-mb "${GPU_VRAM:-0}" \
                --ram-gb "${RAM_GB:-0}" \
                --host-arch "${HOST_ARCH:-unknown}" \
                --local-model \
                2>>"$LOG_FILE")" || _preserve_status=$?
            if [[ -n "$_preserved_model_env" ]] && command -v load_model_selector_env_from_output >/dev/null 2>&1; then
                # Remove every model-selector runtime value before loading the
                # preserved active contract. The helper omits inactive optional
                # LLAMA_* settings intentionally: exporting them as empty makes
                # Compose pass an empty numeric value to llama.cpp.
                unset MODEL_RUNTIME_PROFILE MODEL_RUNTIME_PROFILE_LABEL MODEL_RUNTIME_PROFILE_SOURCE
                unset LLAMA_SERVER_IMAGE LLAMA_SERVER_MEMORY_LIMIT
                unset LLAMA_CPP_RELEASE_TAG_OVERRIDE LLAMA_CPP_SERVER_BINARY
                unset LLAMA_ARG_FLASH_ATTN LLAMA_ARG_CACHE_TYPE_K LLAMA_ARG_CACHE_TYPE_V
                unset LLAMA_ARG_N_CPU_MOE LLAMA_ARG_NO_CACHE_PROMPT
                unset LLAMA_ARG_CHECKPOINT_EVERY_NT LLAMA_ARG_SPEC_TYPE
                unset LLAMA_ARG_CTX_CHECKPOINTS LLAMA_ARG_CACHE_RAM
                unset LLAMA_ARG_SPEC_DRAFT_N_MAX LLAMA_ARG_SPLIT_MODE LLAMA_ARG_TENSOR_SPLIT
                load_model_selector_env_from_output <<< "$_preserved_model_env"
                log "Preserved active model across installer rerun: ${LLM_MODEL} (${GGUF_FILE})"
            fi
            unset _preserve_status
        fi
    fi
elif [[ "${ODS_RESELECT_MODEL:-false}" == "true" ]]; then
    log "Active-model preservation disabled by --reselect-model"
fi

unset _native_python

# Display hardware summary with nice formatting
CPU_INFO=$(grep "model name" /proc/cpuinfo 2>/dev/null | head -1 | cut -d: -f2 | xargs || echo "Unknown")
if [[ "$INTERACTIVE" == "true" ]]; then
    # A host-native llama-server (Windows under WSL) runs the model on a GPU
    # this Linux probe cannot see; show that GPU instead of "None".
    if ods_native_llm_requested && [[ -n "${NATIVE_LLM_GPU_NAME:-}" ]]; then
        show_hardware_summary "${NATIVE_LLM_GPU_NAME} (llama-server on Windows)" "$(ods_format_vram_mib "${NATIVE_LLM_GPU_VRAM_MB:-0}")" "$CPU_INFO" "$_ram_display" "$DISK_AVAIL" "$_host_ram_display" "$RAM_IS_WSL"
    else
        show_hardware_summary "$GPU_NAME" "$(ods_format_vram_mib "$GPU_VRAM")" "$CPU_INFO" "$_ram_display" "$DISK_AVAIL" "$_host_ram_display" "$RAM_IS_WSL"
    fi
    if [[ "$RAM_IS_WSL" == true ]]; then
        ai "WSL has its own RAM limit; model sizing uses the WSL budget. To change it, edit .wslconfig and restart WSL."
    fi

    _shown_model="$LLM_MODEL"
    if [[ -n "${EXTERNAL_LLM_URL:-}" ]]; then
        # The API serves the model; the local pick is not downloaded.
        _shown_model="${EXTERNAL_LLM_MODEL:-the API's model} (API)"
        SPEED_EST="depends on the API"
        USERS_EST="depends on the API"
    elif [[ "$TIER" == "CLOUD" ]]; then
        SPEED_EST="cloud API"
        USERS_EST="depends on API tier"
    else
        SPEED_EST="benchmark after first launch"
        USERS_EST="measured after local benchmark"
    fi
    show_tier_recommendation "$TIER" "$_shown_model" "$SPEED_EST" "$USERS_EST"
    unset _shown_model
else
    success "Configuration: Tier $TIER ($TIER_NAME)"
    if [[ -n "${EXTERNAL_LLM_URL:-}" ]]; then
        log "  Model: ${EXTERNAL_LLM_MODEL:-the API's model}, served by ${EXTERNAL_LLM_URL}"
    else
        log "  Model: $LLM_MODEL"
        log "  Context: ${MAX_CONTEXT} tokens"
    fi
fi
