#!/bin/bash
# ============================================================================
# Dream Server Installer — Packaging
# ============================================================================
# Part of: installers/lib/
# Purpose: Distro-agnostic package manager abstraction
#
# Expects: LOG_FILE, log(), warn(), error()
# Provides: detect_pkg_manager(), pkg_install(), pkg_update(), pkg_available(),
#           PKG_MANAGER, DISTRO_ID, DISTRO_ID_LIKE
#
# Modder notes:
#   Add new distro support by extending the case blocks below.
#   Distro detection reads /etc/os-release (standard on all systemd distros).
# ============================================================================

PKG_MANAGER=""
DISTRO_ID=""
DISTRO_ID_LIKE=""

# Use sudo only when not already root (e.g. Docker containers run as root)
_SUDO=""
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    if declare -f ds_sudo >/dev/null 2>&1; then
        _SUDO="ds_sudo"
    else
        _SUDO="sudo"
    fi
fi

# Detect the system's package manager from /etc/os-release
# Sets: PKG_MANAGER, DISTRO_ID, DISTRO_ID_LIKE
detect_pkg_manager() {
    if [[ -f /etc/os-release ]]; then
        local _saved_version="${VERSION:-}"
        # shellcheck source=/dev/null
        source /etc/os-release
        VERSION="$_saved_version"
        DISTRO_ID="${ID:-unknown}"
        DISTRO_ID_LIKE="${ID_LIKE:-}"
    else
        DISTRO_ID="unknown"
        DISTRO_ID_LIKE=""
    fi

    # Normalize: check ID first, then ID_LIKE for derivatives
    case "$DISTRO_ID" in
        ubuntu|debian|linuxmint|pop|elementary|zorin|kali|raspbian)
            PKG_MANAGER="apt" ;;
        fedora|rhel|centos|rocky|alma|nobara)
            PKG_MANAGER="dnf" ;;
        arch|cachyos|manjaro|endeavouros|garuda|artix)
            PKG_MANAGER="pacman" ;;
        opensuse*|sles)
            PKG_MANAGER="zypper" ;;
        void)
            PKG_MANAGER="xbps" ;;
        alpine)
            PKG_MANAGER="apk" ;;
        *)
            # Fallback: check ID_LIKE for derivative distros
            case "$DISTRO_ID_LIKE" in
                *debian*|*ubuntu*)  PKG_MANAGER="apt" ;;
                *fedora*|*rhel*)    PKG_MANAGER="dnf" ;;
                *arch*)             PKG_MANAGER="pacman" ;;
                *suse*)             PKG_MANAGER="zypper" ;;
                *)
                    # Last resort: check what's actually installed
                    if command -v apt-get &>/dev/null; then
                        PKG_MANAGER="apt"
                    elif command -v dnf &>/dev/null; then
                        PKG_MANAGER="dnf"
                    elif command -v pacman &>/dev/null; then
                        PKG_MANAGER="pacman"
                    elif command -v zypper &>/dev/null; then
                        PKG_MANAGER="zypper"
                    else
                        PKG_MANAGER="unknown"
                    fi
                    ;;
            esac
            ;;
    esac

    log "Detected distro: ${DISTRO_ID} (like: ${DISTRO_ID_LIKE:-none}, pkg: ${PKG_MANAGER})"
}

# Update the package index
pkg_update() {
    case "$PKG_MANAGER" in
        apt)    $_SUDO apt-get update -qq 2>>"$LOG_FILE" ;;
        dnf)    $_SUDO dnf check-update -q 2>>"$LOG_FILE" || true ;;  # returns 100 if updates available
        pacman) $_SUDO pacman -Syu --noconfirm 2>>"$LOG_FILE" ;;      # full sync+upgrade (partial -Sy is unsafe)
        zypper) $_SUDO zypper --non-interactive refresh 2>>"$LOG_FILE" ;;
        xbps)   $_SUDO xbps-install -S 2>>"$LOG_FILE" ;;
        apk)    $_SUDO apk update 2>>"$LOG_FILE" ;;
        *)      warn "Cannot update package index: unknown package manager '$PKG_MANAGER'" ;;
    esac
}

# Install one or more packages
# Usage: pkg_install curl jq rsync
pkg_install() {
    local pkgs=("$@")
    [[ ${#pkgs[@]} -eq 0 ]] && return 0

    log "Installing packages (${PKG_MANAGER}): ${pkgs[*]}"
    case "$PKG_MANAGER" in
        apt)    $_SUDO apt-get install -y -qq "${pkgs[@]}" 2>>"$LOG_FILE" ;;
        dnf)    $_SUDO dnf install -y -q "${pkgs[@]}" 2>>"$LOG_FILE" ;;
        pacman) $_SUDO pacman -S --noconfirm --needed "${pkgs[@]}" 2>>"$LOG_FILE" ;;
        zypper) $_SUDO zypper --non-interactive install "${pkgs[@]}" 2>>"$LOG_FILE" ;;
        xbps)   $_SUDO xbps-install -y "${pkgs[@]}" 2>>"$LOG_FILE" ;;
        apk)    $_SUDO apk add --no-progress "${pkgs[@]}" 2>>"$LOG_FILE" ;;
        *)      warn "Cannot install packages: unknown package manager '$PKG_MANAGER'. Install manually: ${pkgs[*]}" ; return 1 ;;
    esac
}

# Check if a package is available in the repos
# Usage: if pkg_available jq; then ...
pkg_available() {
    local pkg="$1"
    case "$PKG_MANAGER" in
        apt)    apt-cache show "$pkg" &>/dev/null ;;
        dnf)    dnf info "$pkg" &>/dev/null ;;
        pacman) pacman -Si "$pkg" &>/dev/null ;;
        zypper) zypper info "$pkg" &>/dev/null ;;
        xbps)   xbps-query -Rs "$pkg" &>/dev/null ;;
        apk)    apk info -e "$pkg" &>/dev/null || apk search -q "$pkg" &>/dev/null ;;
        *)      return 1 ;;
    esac
}

# Map a canonical package name to distro-specific name where needed
# Usage: pkg_name=$(pkg_resolve docker-compose-plugin)
pkg_resolve() {
    local canonical="$1"
    case "$PKG_MANAGER" in
        apt)
            case "$canonical" in
                docker-compose-plugin) echo "docker-compose-plugin" ;;
                python3-pyyaml)        echo "python3-yaml" ;;
                *) echo "$canonical" ;;
            esac
            ;;
        dnf)
            case "$canonical" in
                docker-compose-plugin) echo "docker-compose-plugin" ;;
                python3-pyyaml)        echo "python3-pyyaml" ;;
                build-essential)       echo "gcc gcc-c++ make" ;;
                *) echo "$canonical" ;;
            esac
            ;;
        pacman)
            case "$canonical" in
                docker-compose-plugin) echo "docker-compose" ;;
                python3-pyyaml)        echo "python-yaml" ;;
                build-essential)       echo "base-devel" ;;
                *) echo "$canonical" ;;
            esac
            ;;
        zypper)
            case "$canonical" in
                docker-compose-plugin) echo "docker-compose" ;;
                python3-pyyaml)        echo "python3-PyYAML" ;;
                build-essential)       echo "devel_basis" ;;
                *) echo "$canonical" ;;
            esac
            ;;
        xbps)
            case "$canonical" in
                docker-compose-plugin) echo "docker-compose" ;;
                python3-pyyaml)        echo "python3-yaml" ;;
                build-essential)       echo "base-devel" ;;
                *) echo "$canonical" ;;
            esac
            ;;
        apk)
            case "$canonical" in
                docker-compose-plugin) echo "docker-cli-compose" ;;
                python3-pyyaml)        echo "py3-yaml" ;;
                build-essential)       echo "build-base" ;;
                *) echo "$canonical" ;;
            esac
            ;;
        *)
            echo "$canonical" ;;
    esac
}
