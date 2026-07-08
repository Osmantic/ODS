#!/bin/bash
# ============================================================================
# ODS Installer — Host Architecture Detection
# ============================================================================
# Part of: installers/lib/
# Purpose: Detect host CPU architecture for arch-specific image selection
#
# Expects: (nothing — can be sourced independently)
# Provides: detect_host_arch()
#
# Modder notes:
#   Output uses Docker/OCI naming (amd64, arm64) rather than uname -m
#   names (x86_64, aarch64) so the value can be substituted directly into
#   `platform: linux/${HOST_ARCH}` and matched against image manifest
#   `architecture` fields.
# ============================================================================

# Echoes one of: amd64, arm64, unknown
detect_host_arch() {
    local m
    m="$(uname -m 2>/dev/null || echo unknown)"
    case "$m" in
        x86_64|amd64) echo "amd64" ;;
        aarch64|arm64) echo "arm64" ;;
        *) echo "unknown" ;;
    esac
}
