#!/bin/bash
# ============================================================================
# Dream Server Installer — sudo helpers
# ============================================================================
# Part of: installers/lib/
# Purpose: Keep privileged installer commands from hanging invisibly in
#          --non-interactive mode, while still allowing normal interactive sudo.
#
# Expects: INTERACTIVE, DRY_RUN, ai(), ai_bad(), ai_warn(), error()
# Provides: ds_sudo(), ds_prepare_sudo()
# ============================================================================

ds_sudo() {
    if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
        "$@"
        return $?
    fi

    if [[ "${INTERACTIVE:-true}" != "true" ]]; then
        sudo -n "$@"
    else
        sudo "$@"
    fi
}

ds_prepare_sudo() {
    local reason="${1:-installer setup}"

    [[ "${DRY_RUN:-false}" == "true" ]] && return 0
    [[ ${EUID:-$(id -u)} -eq 0 ]] && return 0
    command -v sudo >/dev/null 2>&1 || error "sudo is required for ${reason}."

    if [[ "${INTERACTIVE:-true}" != "true" ]]; then
        if ! sudo -n true 2>/dev/null; then
            ai_bad "sudo requires a password, but this run is --non-interactive."
            ai_bad "The installer would otherwise appear to hang at the first hidden sudo prompt."
            ai "Run one of:"
            ai "  sudo -v && ./install.sh --non-interactive ..."
            ai "  ./install.sh ..."
            ai "  configure NOPASSWD sudo for this install user"
            error "Cannot continue non-interactively without cached or passwordless sudo."
        fi
    else
        ai "Checking sudo access up front so later setup steps do not stall..."
        sudo -v || error "sudo authentication failed."
    fi
}
