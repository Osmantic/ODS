#!/bin/bash
# ============================================================================
# ODS Installer — Phase 01: Pre-flight Checks
# ============================================================================
# Part of: installers/phases/
# Purpose: Root/OS/tools checks, existing installation detection
#
# Expects: SCRIPT_DIR, INSTALL_DIR, LOG_FILE, INTERACTIVE, DRY_RUN,
#           PKG_MANAGER,
#           show_phase(), ai(), ai_ok(), signal(), log(), warn(), error()
# Provides: OS sourced from /etc/os-release, OPTIONAL_TOOLS_MISSING
#
# Modder notes:
#   Add new pre-flight checks (e.g., kernel version) here.
# ============================================================================

ods_progress 5 "preflight" "Running preflight checks"
show_phase 1 6 "Pre-flight Checks" "~30 seconds"
ai "I'm scanning your system for required components..."

# Root check
if [[ $EUID -eq 0 ]]; then
    error "Do not run as root. Run as regular user with sudo access."
fi

# OS check
if [[ ! -f /etc/os-release ]]; then
    error "Unsupported OS. This installer requires Linux."
fi

_installer_version="$VERSION"
source /etc/os-release
VERSION="$_installer_version"
log "Detected OS: $PRETTY_NAME"

# Check for required tools
if ! command -v curl &> /dev/null; then
    case "$PKG_MANAGER" in
        dnf)    error "curl is required but not installed. Install with: sudo dnf install curl" ;;
        pacman) error "curl is required but not installed. Install with: sudo pacman -S curl" ;;
        zypper) error "curl is required but not installed. Install with: sudo zypper install curl" ;;
        *)      error "curl is required but not installed. Install with: sudo apt install curl" ;;
    esac
fi
log "curl: $(curl --version 2>/dev/null | sed -n '1p')"

if ! command -v jq &> /dev/null; then
    log "jq not found - attempting auto-install..."
    # In non-interactive mode, fail fast if sudo requires a password
    # (otherwise the bare `sudo <pkgmgr>` below hangs on a TTY prompt that never comes).
    if [[ "${INTERACTIVE:-true}" != "true" ]] && ! sudo -n true 2>/dev/null; then
        error "Cannot install jq: sudo password required but running in --non-interactive mode. Re-run interactively or configure NOPASSWD sudo for the package manager."
    fi
    case "$PKG_MANAGER" in
        dnf)    sudo dnf install -y jq ;;
        pacman) sudo pacman -S --noconfirm jq ;;
        zypper) sudo zypper install -y jq ;;
        apk)    sudo apk add jq ;;
        *)      sudo apt-get install -y jq ;;
    esac
    command -v jq &> /dev/null || error "Failed to install jq automatically. Install it manually and re-run."
fi
log "jq: $(jq --version 2>/dev/null)"

# Check optional tools (warn but don't fail)
OPTIONAL_TOOLS_MISSING=""
if ! command -v rsync &> /dev/null; then
    OPTIONAL_TOOLS_MISSING="$OPTIONAL_TOOLS_MISSING rsync"
fi
if [[ -n "$OPTIONAL_TOOLS_MISSING" ]]; then
    warn "Optional tools missing:$OPTIONAL_TOOLS_MISSING"
    echo "  These are needed for update/backup scripts. Install with:"
    case "$PKG_MANAGER" in
        dnf)    echo "  sudo dnf install$OPTIONAL_TOOLS_MISSING" ;;
        pacman) echo "  sudo pacman -S$OPTIONAL_TOOLS_MISSING" ;;
        zypper) echo "  sudo zypper install$OPTIONAL_TOOLS_MISSING" ;;
        *)      echo "  sudo apt install$OPTIONAL_TOOLS_MISSING" ;;
    esac
fi

# Warn about host firewalls that commonly block LAN access to the dashboard or
# WebUI. This is informational: loopback-only installs are fine, and users may
# intentionally keep a firewall active.
if command -v ufw >/dev/null 2>&1 && command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet ufw; then
    warn "UFW is active and may block ODS dashboard/WebUI ports."
    echo "  Allow the configured ODS ports in UFW or keep BIND_ADDRESS on loopback."
elif command -v firewall-cmd >/dev/null 2>&1 && command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet firewalld; then
    warn "firewalld is active and may block ODS dashboard/WebUI ports."
    echo "  Allow the configured ODS ports in firewalld or keep BIND_ADDRESS on loopback."
else
    log "Host firewall: no active UFW/firewalld detected"
fi

# Check source files exist
if [[ ! -f "$SCRIPT_DIR/docker-compose.yml" ]] && [[ ! -f "$SCRIPT_DIR/docker-compose.base.yml" ]]; then
    error "No compose files found in $SCRIPT_DIR. Please run from the ods directory."
fi

_ods_truthy() {
    case "${1:-}" in
        1|true|TRUE|yes|YES|y|Y) return 0 ;;
        *) return 1 ;;
    esac
}

_ods_is_install_backup_dir() {
    local candidate_name="${1%/}"
    candidate_name="${candidate_name##*/}"

    case "$candidate_name" in
        *.backup-[0-9]*|backup-[0-9]*) return 0 ;;
        *) return 1 ;;
    esac
}

_ods_is_related_install_dir() {
    local candidate="$1"
    local compose_file=""

    [[ -d "$candidate" ]] || return 1
    [[ -f "$candidate/.env" || -d "$candidate/data" ]] || return 1

    if [[ -f "$candidate/docker-compose.base.yml" ]]; then
        compose_file="$candidate/docker-compose.base.yml"
    elif [[ -f "$candidate/docker-compose.yml" ]]; then
        compose_file="$candidate/docker-compose.yml"
    else
        return 1
    fi

    grep -Eq '^[[:space:]]{2}open-webui:[[:space:]]*$' "$compose_file" || return 1
    grep -Eq '^[[:space:]]{2}dashboard-api:[[:space:]]*$' "$compose_file" || return 1
    grep -Eq '^[[:space:]]{2}(llama-server|litellm):[[:space:]]*$' "$compose_file"
}

_ods_related_compose_containers() {
    command -v docker >/dev/null 2>&1 || return 0

    docker ps -a \
        --format '{{.Names}}|{{.Label "com.docker.compose.project"}}|{{.Label "com.docker.compose.service"}}' \
        2>/dev/null |
        awk -F '|' '
            $2 != "" {
                project = $2
                if (names[project] == "") {
                    names[project] = $1
                } else {
                    names[project] = names[project] " " $1
                }
                if ($3 == "open-webui") open_webui[project] = 1
                if ($3 == "dashboard-api") dashboard_api[project] = 1
                if ($3 == "llama-server" || $3 == "litellm") inference[project] = 1
            }
            END {
                for (project in names) {
                    if (open_webui[project] && dashboard_api[project] && inference[project]) {
                        print names[project]
                    }
                }
            }
        '
}

if [[ ! -d "$INSTALL_DIR" ]] && ! _ods_truthy "${ODS_ALLOW_LEGACY_PARALLEL:-}"; then
    _pre_ods_install_dir="${ODS_LEGACY_INSTALL_DIR:-}"
    _pre_ods_findings=()
    if [[ -n "$_pre_ods_install_dir" && -d "$_pre_ods_install_dir" ]] && {
        [[ -f "$_pre_ods_install_dir/.env" ]] ||
        [[ -f "$_pre_ods_install_dir/docker-compose.yml" ]] ||
        [[ -d "$_pre_ods_install_dir/data" ]]
    }; then
        _pre_ods_findings+=("install directory: $_pre_ods_install_dir")
    fi
    while IFS= read -r -d '' _pre_ods_candidate; do
        [[ "${_pre_ods_candidate%/}" == "${INSTALL_DIR%/}" ]] && continue
        [[ "${_pre_ods_candidate%/}" == "${SCRIPT_DIR%/}" ]] && continue
        [[ -n "$_pre_ods_install_dir" && "${_pre_ods_candidate%/}" == "${_pre_ods_install_dir%/}" ]] && continue
        _ods_is_install_backup_dir "$_pre_ods_candidate" && continue
        if _ods_is_related_install_dir "$_pre_ods_candidate"; then
            _pre_ods_findings+=("related install directory: $_pre_ods_candidate")
        fi
    done < <(find "$HOME" -mindepth 1 -maxdepth 1 \( -type d -o -type l \) -print0 2>/dev/null)
    _pre_ods_containers="$(_ods_related_compose_containers || true)"
    if [[ -n "$_pre_ods_containers" ]]; then
        _pre_ods_findings+=("related Compose containers: $(printf '%s\n' "$_pre_ods_containers" | tr '\n' ' ')")
    fi
    if (( ${#_pre_ods_findings[@]} > 0 )); then
        printf '%s\n' "${_pre_ods_findings[@]}" | sed 's/^/[related install] /' >&2
        error "Existing related install detected before first ODS install. Stop, uninstall, or migrate the older stack first. To run both intentionally, set ODS_ALLOW_LEGACY_PARALLEL=1 and choose non-conflicting ports/install paths."
    fi
    unset _pre_ods_install_dir _pre_ods_findings _pre_ods_candidate _pre_ods_containers
fi

# Existing installation — update in place (secrets and data are preserved)
if [[ -d "$INSTALL_DIR" ]]; then
    log "Existing installation found at $INSTALL_DIR — updating in place"
    signal "Existing install detected. Secrets and data will be preserved."
fi

# Filesystem POSIX-permission check.
# Phase 06 runs `chmod 600 $INSTALL_DIR/.env` to lock down secrets. On exFAT,
# FAT32, fuseblk (NTFS via ntfs-3g), 9p and DrvFs that chmod is a silent no-op
# — the secrets file ends up world-readable. Refuse install up front so the
# user can pick a POSIX-native path.
check_install_dir_filesystem() {
    local probe="$INSTALL_DIR"
    while [[ -n "$probe" && ! -e "$probe" ]]; do
        probe="$(dirname "$probe")"
    done
    [[ -z "$probe" ]] && probe="/"

    local fs_type=""
    fs_type=$(stat -fc %T "$probe" 2>/dev/null || true)
    fs_type="${fs_type,,}"  # lowercase
    INSTALL_FS_TYPE="${fs_type:-unknown}"

    case "$fs_type" in
        fuseblk|msdos|exfat|vfat|fat|9p|drvfs|ntfs|ntfs-3g)
            error "INSTALL_DIR ($INSTALL_DIR) is on a ${fs_type} filesystem.

ODS stores secrets in $INSTALL_DIR/.env and locks them with
chmod 600. ${fs_type} silently ignores POSIX permissions, which would
leave the secrets file world-readable.

Pick a path on a POSIX-native filesystem (ext4, btrfs, xfs, zfs) and
re-run, e.g.:  INSTALL_DIR=\"\$HOME/ods\" $0"
            ;;
    esac

    # Networked filesystems honour chmod 600 locally, but the real access
    # control lives in the share's server-side ACL. Warn only — installs
    # to network-mounted homes are common and not always insecure.
    case "$fs_type" in
        nfs|nfs4|cifs|fuse.smbnetfs|fuse.glusterfs|ocfs2)
            warn "INSTALL_DIR ($INSTALL_DIR) is on a networked filesystem ($fs_type)."
            warn ".env permissions (chmod 600) are advisory — actual access control is governed by the share's ACL on the server."
            warn "If this share is exposed to other clients, sensitive credentials may be readable from those hosts."
            ;;
    esac
    log "INSTALL_DIR filesystem: ${INSTALL_FS_TYPE}"
}

# Docker Desktop file-sharing probe — only meaningful when Docker Desktop
# is in use (most Linux installs use the native daemon and skip this).
check_docker_desktop_sharing() {
    command -v docker >/dev/null 2>&1 || return 0

    local os_string=""
    os_string=$(docker info --format '{{json .OperatingSystem}}' 2>/dev/null || true)
    case "$os_string" in
        *"Docker Desktop"*) ;;
        *) return 0 ;;
    esac

    local probe="$INSTALL_DIR"
    while [[ -n "$probe" && ! -e "$probe" ]]; do
        probe="$(dirname "$probe")"
    done
    [[ -z "$probe" ]] && probe="/"

    local out=""
    out=$(docker run --rm -v "${probe}:/check:ro" alpine true 2>&1) || true
    if echo "$out" | grep -qiE "not shared from the host|Mounts denied|file sharing|filesharing"; then
        error "Docker Desktop cannot bind-mount $INSTALL_DIR.

Add the path to Docker Desktop > Settings > Resources > File Sharing,
apply, then re-run this installer.

Probe output:
$(printf '%s\n' "$out" | sed 's/^/    /')"
    fi
    log "Docker Desktop file sharing OK"
}

check_install_dir_filesystem
check_docker_desktop_sharing

# Firewall check: warn if UFW or firewalld is active. The dashboard host agent
# binds to the ods-network gateway by default on Linux, and compose containers
# reach it via the host INPUT chain. Default-DROP firewalls block that traffic
# and the dashboard reports "Host agent is offline". Warn-only; never abort
# install.
if command -v ufw >/dev/null 2>&1 && systemctl is-active --quiet ufw 2>/dev/null; then
    ai_warn "UFW is active. The dashboard host agent must be reachable from compose subnets."
    ai_warn "The installer will auto-add a scoped rule for the actual ods-network subnet after compose starts."
elif command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld 2>/dev/null; then
    ai_warn "firewalld is active. The dashboard host agent must be reachable from compose subnets."
    ai_warn "The installer will auto-add a scoped rule for the actual ods-network subnet after compose starts."
fi

ai_ok "Pre-flight checks passed."
signal "No cloud dependencies required for core operation."
