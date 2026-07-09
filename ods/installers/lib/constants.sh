#!/bin/bash
# ============================================================================
# ODS Installer — Constants
# ============================================================================
# Part of: installers/lib/
# Purpose: Colors, paths, version string, timezone detection
#
# Expects: (nothing — first file sourced)
# Provides: VERSION, SCRIPT_DIR, INSTALL_DIR, LOG_FILE, color codes,
#           SYSTEM_TZ, CAPABILITY_PROFILE_FILE, PREFLIGHT_REPORT_FILE,
#           INSTALL_START_EPOCH, _sed_i()
#
# Modder notes:
#   Change VERSION for custom builds. Add new color codes here.
# ============================================================================

VERSION="2.5.3"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Source path utilities for cross-platform path resolution
if [[ -f "$SCRIPT_DIR/installers/lib/path-utils.sh" ]]; then
    . "$SCRIPT_DIR/installers/lib/path-utils.sh"
    INSTALL_DIR="$(resolve_install_dir)"
else
    # Fallback if path-utils.sh not available
    INSTALL_DIR="${INSTALL_DIR:-$HOME/ods}"
fi

LOG_FILE="${LOG_FILE:-/tmp/ods-install.log}"
CAPABILITY_PROFILE_FILE="${CAPABILITY_PROFILE_FILE:-/tmp/ods-capabilities.json}"
PREFLIGHT_REPORT_FILE="${PREFLIGHT_REPORT_FILE:-/tmp/ods-preflight-report.json}"
INSTALL_START_EPOCH=$(date +%s)

# Auto-detect system timezone (fallback to UTC)
if [[ -f /etc/timezone ]]; then
    SYSTEM_TZ="$(cat /etc/timezone)"
elif [[ -L /etc/localtime ]]; then
    SYSTEM_TZ="$(readlink /etc/localtime | sed 's|.*/zoneinfo/||')"
else
    SYSTEM_TZ="UTC"
fi

#=============================================================================
# Colors — green phosphor CRT theme
#=============================================================================
RED='\033[0;31m'
GRN='\033[0;32m'         # Standard green — body text
BGRN='\033[1;32m'        # Bright green — emphasis, success, headings
DGRN='\033[2;32m'        # Dim green — secondary text, lore
AMB='\033[0;33m'         # Amber — warnings, ETA labels
WHT='\033[1;37m'         # White — key URLs
DIM='\033[2;37m'         # Dim white
NC='\033[0m'             # Reset
CURSOR='█'               # Block cursor for typing

#=============================================================================
# Cross-platform helpers
#=============================================================================

# BSD sed (macOS) requires `sed -i ''` while GNU sed uses `sed -i`.
# Usage: _sed_i "s/old/new/g" file
_sed_i() {
    if sed --version 2>/dev/null | grep -q GNU; then
        sed -i "$@"
    else
        sed -i '' "$@"
    fi
}


#=============================================================================
# Rootless Docker — non-root container service UID map
#=============================================================================
# Each entry is "service:container_uid". Used by the installer, ods-cli, and
# ods-preflight to fix data directory ownership when Docker runs in rootless mode.
ROOTLESS_SERVICE_UIDS=(
    "n8n:1000"
    "whisper:1000"
    "tts:1000"
    "token-spy:1000"
    "privacy-shield:1000"
    "ape:100"
    "langfuse:1001"
    "langfuse/postgres:70"
    "langfuse/clickhouse:101"
    "hermes:10000"
    "comfyui:1000"
)