#!/usr/bin/env bash

# A dry run must never acquire missing host packages as a side effect.
ods_preflight_require_jq() {
    if ! command -v jq >/dev/null 2>&1; then
        if [[ "${DRY_RUN:-false}" == true ]]; then
            error "jq is required for a complete dry run. Install jq yourself and retry; dry run will not install packages."
            return 1
        fi
        log "jq not found - attempting auto-install..."
        if ! ods_sudo_available; then
            error "jq is required but not installed and privileged package installation is unavailable. Install jq first, then re-run ODS."
            return 1
        fi
        case "$PKG_MANAGER" in
            dnf)    ods_sudo dnf install -y jq ;;
            pacman) ods_sudo pacman -S --noconfirm jq ;;
            zypper) ods_sudo zypper install -y jq ;;
            apk)    ods_sudo apk add jq ;;
            apt)    ods_sudo apt-get update -qq && ods_sudo apt-get install -y jq ;;
            *)      ods_sudo apt-get install -y jq ;;
        esac
        if ! command -v jq >/dev/null 2>&1; then
            error "Failed to install jq automatically. Install it manually and re-run."
            return 1
        fi
    fi
    log "jq: $(jq --version 2>/dev/null)"
}
