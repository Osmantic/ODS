#!/bin/bash
# ============================================================================
# ODS Installer — sudo helpers
# ============================================================================
# Part of: installers/lib/
# Purpose: Keep privileged installer commands from hanging invisibly in
#          --non-interactive mode, while still allowing normal interactive sudo.
#
# Expects: INTERACTIVE, DRY_RUN, ai(), ai_bad(), ai_warn(), error()
# Provides: ods_sudo(), ods_prepare_sudo()
# ============================================================================

# ODS_SUDO_AVAILABLE is set by ods_prepare_sudo(). It is "true" only when we can
# run privileged commands without an interactive prompt (either we are root, or
# sudo is cached / passwordless). Anything else is "false" and the installer
# proceeds rootless, skipping the root-only extras. Default to unset → treated
# as available by ods_sudo() for backward-compat when prepare was never called.
export ODS_SUDO_AVAILABLE="${ODS_SUDO_AVAILABLE:-}"

# ods_sudo_available: true when privileged commands can run without a prompt.
ods_sudo_available() {
    [[ ${EUID:-$(id -u)} -eq 0 ]] && return 0
    [[ "${ODS_SUDO_AVAILABLE:-true}" == "true" ]] && return 0
    return 1
}

ods_sudo() {
    if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
        "$@"
        return $?
    fi

    # Friction-free: if we determined up front that sudo is unusable, skip the
    # privileged command rather than prompting (which hangs without a TTY) or
    # failing under `set -e`. Core ODS runs rootless; root-only steps are
    # optional. Log the skip so it is visible in the install log, never silent.
    if [[ "${ODS_SUDO_AVAILABLE:-true}" == "false" ]]; then
        if declare -f log >/dev/null 2>&1; then
            log "Skipping privileged command (sudo unavailable): $*"
        fi
        return 0
    fi

    if [[ "${INTERACTIVE:-true}" != "true" ]]; then
        sudo -n "$@"
    else
        sudo "$@"
    fi
}

ods_prepare_sudo() {
    local reason="${1:-installer setup}"

    if [[ "${DRY_RUN:-false}" == "true" ]]; then
        export ODS_SUDO_AVAILABLE=true
        return 0
    fi
    if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
        export ODS_SUDO_AVAILABLE=true
        return 0
    fi

    export ODS_SUDO_AVAILABLE=false

    if ! command -v sudo >/dev/null 2>&1; then
        ai_warn "sudo not found — continuing without privileged steps (${reason})."
        ai "Root-only extras (system package installs, systemd units, GPU tuning)"
        ai "will be skipped. Core ODS runs rootless via Docker/Podman."
        return 0
    fi

    # sudo present — is it usable without an interactive prompt?
    if sudo -n true 2>/dev/null; then
        export ODS_SUDO_AVAILABLE=true
        return 0
    fi

    if [[ "${INTERACTIVE:-true}" == "true" ]]; then
        ai "Requesting sudo access for privileged setup steps (you may be prompted)..."
        if sudo -v 2>/dev/null; then
            export ODS_SUDO_AVAILABLE=true
            return 0
        fi
        ai_warn "sudo authentication unavailable — continuing without privileged steps."
    else
        ai_warn "sudo needs a password and this run is --non-interactive — continuing rootless."
    fi
    ai "Root-only extras (system package installs, systemd units, GPU tuning)"
    ai "will be skipped. Core ODS runs rootless via Docker/Podman."
    return 0
}
