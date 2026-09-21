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

VERSION="2.6.0"
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
MAG='\033[0;35m'         # Magenta — CRT signal/static accents
BMAG='\033[1;35m'        # Bright magenta — sequence and finale accents
AMB='\033[0;33m'         # Amber — warnings, ETA labels
WHT='\033[1;37m'         # White — key URLs
DIM='\033[2;37m'         # Dim white
NC='\033[0m'             # Reset
CURSOR='█'               # Block cursor for typing

# Machine-readable and redirected output must never contain ANSI escapes.
# `ODS_UI_MODE=cinematic` may force the layout for screenshots, but color still
# follows the standard NO_COLOR contract.
if [[ -n "${NO_COLOR:-}" || "${TERM:-}" == "dumb" || ! -t 1 ]]; then
    RED='' GRN='' BGRN='' DGRN='' MAG='' BMAG='' AMB='' WHT='' DIM='' NC=''
fi

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

# Reach the installing user's systemd manager from both interactive shells and
# unattended SSH/service contexts.  systemctl --user normally inherits these
# variables from a login session; fleet installs and automation often do not.
ods_systemctl_user() {
    local user_uid user_runtime_dir user_bus_address
    user_uid="$(id -u)"
    user_runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$user_uid}"
    user_bus_address="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$user_runtime_dir/bus}"
    env XDG_RUNTIME_DIR="$user_runtime_dir" \
        DBUS_SESSION_BUS_ADDRESS="$user_bus_address" \
        systemctl --user "$@"
}
