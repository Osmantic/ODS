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

LOG_FILE="${LOG_FILE:-${TMPDIR:-/tmp}/ods-install.log}"
CAPABILITY_PROFILE_FILE="${CAPABILITY_PROFILE_FILE:-${TMPDIR:-/tmp}/ods-capabilities.json}"
PREFLIGHT_REPORT_FILE="${PREFLIGHT_REPORT_FILE:-${TMPDIR:-/tmp}/ods-preflight-report.json}"
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
