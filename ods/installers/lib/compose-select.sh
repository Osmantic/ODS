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

[[ -f "${SCRIPT_DIR:-}/lib/safe-env.sh" ]] && . "${SCRIPT_DIR}/lib/safe-env.sh"

resolve_compose_config() {
    COMPOSE_FILE="docker-compose.yml"
    COMPOSE_FLAGS=""

    if [[ -n "${CAP_COMPOSE_OVERLAYS:-}" ]]; then
        IFS=',' read -r -a profile_overlays <<< "$CAP_COMPOSE_OVERLAYS"
        compose_overlay_ok=true
        for overlay in "${profile_overlays[@]}"; do
            if [[ -f "$SCRIPT_DIR/$overlay" ]]; then
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
        if [[ "$TIER" == "NV_ULTRA" ]]; then
            if [[ -f "$SCRIPT_DIR/docker-compose.base.yml" && -f "$SCRIPT_DIR/docker-compose.nvidia.yml" ]]; then
                COMPOSE_FLAGS="-f docker-compose.base.yml -f docker-compose.nvidia.yml"
                COMPOSE_FILE="docker-compose.nvidia.yml"
            fi
        elif [[ "$TIER" == "CLOUD" ]]; then
            if [[ -f "$SCRIPT_DIR/docker-compose.base.yml" ]]; then
                COMPOSE_FLAGS="-f docker-compose.base.yml"
                COMPOSE_FILE="docker-compose.base.yml"
            fi
        elif [[ "$GPU_BACKEND" == "cpu" ]]; then
            if [[ -f "$SCRIPT_DIR/docker-compose.base.yml" && -f "$SCRIPT_DIR/docker-compose.cpu.yml" ]]; then
                COMPOSE_FLAGS="-f docker-compose.base.yml -f docker-compose.cpu.yml"
                COMPOSE_FILE="docker-compose.cpu.yml"
            fi
        elif [[ "$TIER" == "SH_LARGE" || "$TIER" == "SH_COMPACT" ]]; then
            if [[ -f "$SCRIPT_DIR/docker-compose.base.yml" && -f "$SCRIPT_DIR/docker-compose.amd.yml" ]]; then
                COMPOSE_FLAGS="-f docker-compose.base.yml -f docker-compose.amd.yml"
                COMPOSE_FILE="docker-compose.amd.yml"
            fi
        elif [[ "$TIER" == "ARC" || "$TIER" == "ARC_LITE" || "$GPU_BACKEND" == "intel" || "$GPU_BACKEND" == "sycl" ]]; then
            # Prefer docker-compose.arc.yml (oneAPI build-from-source) when present;
            # fall back to docker-compose.intel.yml (pre-built image) if arc.yml is absent.
            if [[ -f "$SCRIPT_DIR/docker-compose.base.yml" && -f "$SCRIPT_DIR/docker-compose.arc.yml" ]]; then
                COMPOSE_FLAGS="-f docker-compose.base.yml -f docker-compose.arc.yml"
                COMPOSE_FILE="docker-compose.arc.yml"
            elif [[ -f "$SCRIPT_DIR/docker-compose.base.yml" && -f "$SCRIPT_DIR/docker-compose.intel.yml" ]]; then
                COMPOSE_FLAGS="-f docker-compose.base.yml -f docker-compose.intel.yml"
                COMPOSE_FILE="docker-compose.intel.yml"
            fi
        else
            if [[ -f "$SCRIPT_DIR/docker-compose.base.yml" && -f "$SCRIPT_DIR/docker-compose.nvidia.yml" ]]; then
                COMPOSE_FLAGS="-f docker-compose.base.yml -f docker-compose.nvidia.yml"
                COMPOSE_FILE="docker-compose.nvidia.yml"
            elif [[ -f "$SCRIPT_DIR/docker-compose.yml" ]]; then
                COMPOSE_FLAGS="-f docker-compose.yml"
            fi
        fi
    fi

    if [[ -z "$COMPOSE_FLAGS" ]]; then
        COMPOSE_FLAGS="-f $COMPOSE_FILE"
    fi

    if [[ -x "$SCRIPT_DIR/scripts/resolve-compose-stack.sh" ]]; then
        COMPOSE_ENV="$("$SCRIPT_DIR/scripts/resolve-compose-stack.sh" \
            --script-dir "$SCRIPT_DIR" \
            --tier "$TIER" \
            --gpu-backend "$GPU_BACKEND" \
            --profile-overlays "${CAP_COMPOSE_OVERLAYS:-}" \
            --gpu-count "${GPU_COUNT:-1}" \
            --ods-mode "${ODS_MODE:-local}" \
            --env 2>>"$LOG_FILE")"
        load_env_from_output <<< "$COMPOSE_ENV"
    fi

    # Layer Tier 0 memory overlay for low-RAM machines
    if [[ "$TIER" == "0" && -f "$SCRIPT_DIR/docker-compose.tier0.yml" ]]; then
        COMPOSE_FLAGS="$COMPOSE_FLAGS -f docker-compose.tier0.yml"
        log "Including docker-compose.tier0.yml (Tier 0 memory limits)"
    fi

    # Auto-include docker-compose.override.yml if present (standard Docker convention).
    # This lets modders add services without editing core compose files.
    if [[ -f "$SCRIPT_DIR/docker-compose.override.yml" ]]; then
        COMPOSE_FLAGS="$COMPOSE_FLAGS -f docker-compose.override.yml"
        log "Including docker-compose.override.yml (user overrides)"
    fi

    log "Compose selection: $COMPOSE_FLAGS"
}
