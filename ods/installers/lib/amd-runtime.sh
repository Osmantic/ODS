#!/bin/bash
# ============================================================================
# ODS Installer — AMD llama.cpp runtime policy
# ============================================================================
# Part of: installers/lib/
# Purpose: Decide how an AMD GPU runs the upstream llama.cpp container: the
#          Vulkan image (default) or the ROCm image, and the one case where
#          ROCm needs HSA_OVERRIDE_GFX_VERSION.
#
# Expects: nothing (pure functions)
# Provides: ODS_AMD_ROCM_IMAGE_TARGETS, ods_amd_normalize_gfx_target(),
#           ods_amd_target_is_cdna(), ods_amd_select_inference_backend(),
#           ods_amd_hsa_override_for_target()
#
# Modder notes:
#   Keep ODS_AMD_ROCM_IMAGE_TARGETS equal to the AMDGPU_TARGETS the pinned
#   server-rocm image was built with (docker-compose.amd-rocm.yml).
# ============================================================================

# GPU targets compiled into the pinned server-rocm-b9014 image (upstream
# .devops/rocm.Dockerfile at b9014).
ODS_AMD_ROCM_IMAGE_TARGETS="gfx908 gfx90a gfx942 gfx1030 gfx1100 gfx1101 gfx1102 gfx1150 gfx1151 gfx1200 gfx1201"

# Lowercase a reported target and drop feature suffixes
# ("gfx90a:sramecc+:xnack-" -> "gfx90a"). Prints nothing for an unknown value.
ods_amd_normalize_gfx_target() {
    local target="${1:-}"
    target="${target,,}"
    target="${target%%:*}"
    target="${target//[[:space:]]/}"
    [[ "$target" =~ ^gfx[0-9a-f]{3,4}$ ]] || return 0
    printf '%s\n' "$target"
}

# Instinct (CDNA) accelerators have no graphics engine, so no Vulkan driver.
ods_amd_target_is_cdna() {
    case "$(ods_amd_normalize_gfx_target "${1:-}")" in
        gfx908|gfx90a|gfx940|gfx941|gfx942|gfx950) return 0 ;;
        *) return 1 ;;
    esac
}

# Usage: ods_amd_select_inference_backend REQUESTED RETAINED [GFX_TARGET...]
#   REQUESTED  AMD_INFERENCE_BACKEND from the caller's environment, or empty
#   RETAINED   AMD_INFERENCE_BACKEND from the installation's .env, or empty
# Prints vulkan or rocm. Returns 1 for a requested value that is neither.
ods_amd_select_inference_backend() {
    local requested="${1:-}" retained="${2:-}" target
    shift 2 || shift $#
    requested="${requested,,}"
    retained="${retained,,}"
    case "$requested" in
        vulkan|rocm) printf '%s\n' "$requested"; return 0 ;;
        "") ;;
        *) return 1 ;;
    esac
    for target in "$@"; do
        if ods_amd_target_is_cdna "$target"; then
            printf '%s\n' rocm
            return 0
        fi
    done
    case "$retained" in
        vulkan|rocm) printf '%s\n' "$retained" ;;
        *) printf '%s\n' vulkan ;;
    esac
}

# Usage: ods_amd_hsa_override_for_target BACKEND GFX_TARGET
# Prints the HSA_OVERRIDE_GFX_VERSION value ROCm needs for a GPU the pinned
# ROCm image was not built for, or nothing. Vulkan never needs one. Only the
# RDNA2 and RDNA3 siblings of a compiled target are mapped; a wrong override
# can hang the GPU, so every other target is left unset.
ods_amd_hsa_override_for_target() {
    local backend="${1:-}" target
    target="$(ods_amd_normalize_gfx_target "${2:-}")"
    [[ "${backend,,}" == rocm && -n "$target" ]] || return 0
    [[ " $ODS_AMD_ROCM_IMAGE_TARGETS " == *" $target "* ]] && return 0
    case "$target" in
        gfx1031|gfx1032|gfx1033|gfx1034|gfx1035|gfx1036) printf '%s\n' 10.3.0 ;;
        gfx1103) printf '%s\n' 11.0.0 ;;
        *) ;;
    esac
}
