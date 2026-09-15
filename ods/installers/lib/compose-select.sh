#!/bin/bash
# ============================================================================
# ODS Installer — Compose Selection
# ============================================================================
# Part of: installers/lib/
# Purpose: Resolve which docker-compose overlay files to use based on tier,
#          GPU backend, and capability profile
#
# Expects: SCRIPT_DIR, TIER, GPU_BACKEND, CAP_COMPOSE_OVERLAYS, LOG_FILE,
#          GPU_COUNT, log(), warn()
# Provides: resolve_compose_config() → sets COMPOSE_FILE, COMPOSE_FLAGS
#
# Modder notes:
#   Add new compose overlay mappings or backends here.
# ============================================================================

[[ -f "${SCRIPT_DIR:-}/lib/safe-env.sh" ]] && . "${SCRIPT_DIR:-}/lib/safe-env.sh"

if ! declare -F log >/dev/null 2>&1; then
    log() { :; }
fi
if ! declare -F warn >/dev/null 2>&1; then
    warn() { :; }
fi
if ! declare -F load_env_from_output >/dev/null 2>&1; then
    load_env_from_output() { :; }
fi

resolve_compose_config() {
    COMPOSE_FILE="docker-compose.yml"
    COMPOSE_FLAGS=""

    local _script_dir="${SCRIPT_DIR:-.}"
    local _tier="${TIER:-}"
    local _gpu_backend="${GPU_BACKEND:-}"
    local _log_file="${LOG_FILE:-/dev/null}"

    if [[ -n "${CAP_COMPOSE_OVERLAYS:-}" ]]; then
        IFS=',' read -r -a profile_overlays <<< "$CAP_COMPOSE_OVERLAYS"
        compose_overlay_ok=true
        for overlay in "${profile_overlays[@]}"; do
            if [[ -f "$_script_dir/$overlay" ]]; then
                COMPOSE_FLAGS="$COMPOSE_FLAGS -f $overlay"
            else
                compose_overlay_ok=false
                break
            fi
        done
        if [[ "$compose_overlay_ok" == "true" && ${#profile_overlays[@]} -gt 0 ]]; then
            COMPOSE_FLAGS="${COMPOSE_FLAGS# }"
            COMPOSE_FILE="${profile_overlays[${#profile_overlays[@]}-1]}"
        else
            COMPOSE_FLAGS=""
        fi
    fi

    # Backward compatibility default if no flags were set.
    if [[ -z "$COMPOSE_FLAGS" ]]; then
        if [[ "$_tier" == "NV_ULTRA" ]]; then
            if [[ -f "$_script_dir/docker-compose.base.yml" && -f "$_script_dir/docker-compose.nvidia.yml" ]]; then
                COMPOSE_FLAGS="-f docker-compose.base.yml -f docker-compose.nvidia.yml"
                COMPOSE_FILE="docker-compose.nvidia.yml"
            fi
        elif [[ "$_tier" == "CLOUD" ]]; then
            if [[ -f "$_script_dir/docker-compose.base.yml" ]]; then
                COMPOSE_FLAGS="-f docker-compose.base.yml"
                COMPOSE_FILE="docker-compose.base.yml"
            fi
        elif [[ "$_gpu_backend" == "cpu" ]]; then
            if [[ -f "$_script_dir/docker-compose.base.yml" && -f "$_script_dir/docker-compose.cpu.yml" ]]; then
                COMPOSE_FLAGS="-f docker-compose.base.yml -f docker-compose.cpu.yml"
                COMPOSE_FILE="docker-compose.cpu.yml"
            fi
        elif [[ "$_tier" == "SH_LARGE" || "$_tier" == "SH_COMPACT" ]]; then
            if [[ -f "$_script_dir/docker-compose.base.yml" && -f "$_script_dir/docker-compose.amd.yml" ]]; then
                COMPOSE_FLAGS="-f docker-compose.base.yml -f docker-compose.amd.yml"
                COMPOSE_FILE="docker-compose.amd.yml"
            fi
        elif [[ "$_tier" == "ARC" || "$_tier" == "ARC_LITE" || "$_gpu_backend" == "intel" || "$_gpu_backend" == "sycl" ]]; then
            # Prefer docker-compose.arc.yml (oneAPI build-from-source) when present;
            # fall back to docker-compose.intel.yml (pre-built image) if arc.yml is absent.
            if [[ -f "$_script_dir/docker-compose.base.yml" && -f "$_script_dir/docker-compose.arc.yml" ]]; then
                COMPOSE_FLAGS="-f docker-compose.base.yml -f docker-compose.arc.yml"
                COMPOSE_FILE="docker-compose.arc.yml"
            elif [[ -f "$_script_dir/docker-compose.base.yml" && -f "$_script_dir/docker-compose.intel.yml" ]]; then
                COMPOSE_FLAGS="-f docker-compose.base.yml -f docker-compose.intel.yml"
                COMPOSE_FILE="docker-compose.intel.yml"
            fi
        else
            if [[ -f "$_script_dir/docker-compose.base.yml" && -f "$_script_dir/docker-compose.nvidia.yml" ]]; then
                COMPOSE_FLAGS="-f docker-compose.base.yml -f docker-compose.nvidia.yml"
                COMPOSE_FILE="docker-compose.nvidia.yml"
            elif [[ -f "$_script_dir/docker-compose.yml" ]]; then
                COMPOSE_FLAGS="-f docker-compose.yml"
            fi
        fi
    fi

    if [[ -z "$COMPOSE_FLAGS" ]]; then
        COMPOSE_FLAGS="-f $COMPOSE_FILE"
    fi

    if [[ -x "$_script_dir/scripts/resolve-compose-stack.sh" ]]; then
        COMPOSE_ENV="$("$_script_dir/scripts/resolve-compose-stack.sh" \
            --script-dir "$_script_dir" \
            --tier "$_tier" \
            --gpu-backend "$_gpu_backend" \
            --profile-overlays "${CAP_COMPOSE_OVERLAYS:-}" \
            --gpu-count "${GPU_COUNT:-1}" \
            --ods-mode "${ODS_MODE:-local}" \
            --env 2>>"$_log_file")"
        load_env_from_output <<< "$COMPOSE_ENV"
    fi

    # Layer Tier 0 memory overlay for low-RAM machines
    if [[ "$_tier" == "0" && -f "$_script_dir/docker-compose.tier0.yml" ]]; then
        COMPOSE_FLAGS="$COMPOSE_FLAGS -f docker-compose.tier0.yml"
        log "Including docker-compose.tier0.yml (Tier 0 memory limits)"
    fi

    # Auto-include docker-compose.override.yml if present (standard Docker convention).
    # This lets modders add services without editing core compose files.
    if [[ -f "$_script_dir/docker-compose.override.yml" ]]; then
        COMPOSE_FLAGS="$COMPOSE_FLAGS -f docker-compose.override.yml"
        log "Including docker-compose.override.yml (user overrides)"
    fi

    log "Compose selection: $COMPOSE_FLAGS"
}
