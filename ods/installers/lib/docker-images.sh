#!/bin/bash
# ============================================================================
# ODS Installer — Docker Image Validation
# ============================================================================
# Part of: installers/lib/
# Purpose: Fail early when a configured remote image tag does not exist.
#
# Expects: DOCKER_CMD, LOG_FILE, ai(), ai_ok(), ai_warn(), ai_bad()
# Provides: docker_image_available(), validate_docker_image_or_fallback()
#
# Modder notes:
#   Use validate_docker_image_or_fallback before compose up for configurable
#   runtime images. Do not silently substitute images; model/runtime
#   compatibility matters.
# ============================================================================

docker_image_available() {
    local image="${1:-}"
    local timeout_seconds="${DOCKER_IMAGE_CHECK_TIMEOUT:-45}"

    [[ -n "$image" ]] || return 1

    if ${DOCKER_CMD:-docker} image inspect "$image" >/dev/null 2>&1; then
        return 0
    fi

    if command -v timeout >/dev/null 2>&1; then
        timeout "$timeout_seconds" ${DOCKER_CMD:-docker} manifest inspect "$image" >/dev/null 2>&1
    else
        ${DOCKER_CMD:-docker} manifest inspect "$image" >/dev/null 2>&1
    fi
}

validate_docker_image_or_fallback() {
    local result_var="$1"
    local image="$2"
    local label="$3"
    local fallback_env="${4:-}"
    local primary_env="${5:-LLAMA_SERVER_IMAGE}"
    local fallback=""

    if docker_image_available "$image"; then
        printf -v "$result_var" '%s' "$image"
        ai_ok "$label image available: $image"
        return 0
    fi

    if [[ -n "$fallback_env" ]]; then
        fallback="${!fallback_env:-}"
    fi

    if [[ -n "$fallback" ]]; then
        ai_warn "$label image unavailable: $image"
        ai_warn "Trying explicit fallback from $fallback_env: $fallback"
        if docker_image_available "$fallback"; then
            printf -v "$result_var" '%s' "$fallback"
            ai_ok "$label fallback image available: $fallback"
            return 0
        fi
        ai_bad "$label fallback image is also unavailable: $fallback"
    else
        ai_bad "$label image is unavailable: $image"
    fi

    ai "Docker cannot resolve this image tag before service startup."
    ai "Check the tag, registry access, and Docker Desktop/daemon network."
    ai "To override intentionally, set $primary_env to a valid image."
    if [[ -n "$fallback_env" ]]; then
        ai "To permit an explicit fallback, set $fallback_env to a valid image."
    fi
    return 1
}
