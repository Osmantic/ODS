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

# Compose overlays append profile lists. Verify the effective service set, not
# only the selected files, before an API-only gateway pulls or starts images.
ods_gateway_assert_no_managed_inference() {
    local services compose_root="${INSTALL_DIR:-$PWD}"
    # Phase 08 still runs from the source checkout during an upgrade. Resolve
    # against the installed project, whose generated .env supplies Compose
    # interpolation values and reflects the runtime selection being changed.
    services="$(cd "$compose_root" && $DOCKER_COMPOSE_CMD "$@" config --services)" || return 1
    if grep -Eq '^(llama-server|model-router)$' <<< "$services"; then
        printf 'Gateway-only Compose includes ODS-managed inference. Clear COMPOSE_PROFILES and retry.\n' >&2
        return 1
    fi
    return 0
}

# Check both installer selectors, including the accepted truthy marker values.
ods_external_lemonade_requested() {
    local external="${LEMONADE_EXTERNAL:-false}" runtime="${AMD_INFERENCE_RUNTIME:-}" managed="${AMD_INFERENCE_MANAGED:-}"
    case "${external,,}" in true|1|yes|on) return 0 ;; esac
    [[ "${runtime,,}" == lemonade && "${managed,,}" == false ]]
}

# An external Lemonade install may still run the ODS model-router for Pixel,
# but it must never pull or launch the ODS-owned llama-server. Check the
# effective service set because inherited profiles can override an overlay.
ods_external_lemonade_assert_no_managed_llama() {
    local services compose_root="${INSTALL_DIR:-$PWD}"
    services="$(cd "$compose_root" && $DOCKER_COMPOSE_CMD "$@" config --services)" || return 1
    if grep -qx 'llama-server' <<< "$services"; then
        printf 'External Lemonade Compose includes ODS-managed llama-server. Check ODS_MODE and clear COMPOSE_PROFILES.\n' >&2
        return 1
    fi
    return 0
}

# A caller can inherit COMPOSE_PROFILES=gateway-webui. Check the effective
# service set before pulling or starting a Portal-only stack.
ods_compose_assert_no_webui() {
    local services compose_root="${INSTALL_DIR:-$PWD}"
    services="$(cd "$compose_root" && $DOCKER_COMPOSE_CMD "$@" config --services)" || return 1
    if grep -qx 'open-webui' <<< "$services"; then
        printf 'No-WebUI Compose still enables Open WebUI. Clear COMPOSE_PROFILES and retry.\n' >&2
        return 1
    fi
    return 0
}

# Phase 08 checks the selected service list before image pulls, while Pixel's
# private ingress group is created in Phase 11. Compose interpolates every
# selected service even for `config --services`, so supply a numeric GID only
# within this read-only early check. Phase 11 validates the real installed GID
# before starting containers and repeats the no-WebUI check.
ods_compose_assert_no_webui_before_pixel_identity() (
    if [[ -z "${PIXEL_INGRESS_GID:-}" ]]; then
        export PIXEL_INGRESS_GID=1
    fi
    ods_compose_assert_no_webui "$@"
)
