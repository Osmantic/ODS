#!/bin/bash
# ============================================================================
# Dream Server Installer — Phase 05: Docker Setup
# ============================================================================
# Part of: installers/phases/
# Purpose: Install Docker, Docker Compose, and NVIDIA Container Toolkit
#
# Expects: SKIP_DOCKER, DRY_RUN, INTERACTIVE, GPU_COUNT, GPU_BACKEND,
#           LOG_FILE, MIN_DRIVER_VERSION, PKG_MANAGER,
#           show_phase(), ai(), ai_ok(), ai_warn(), log(), warn(), error(),
#           detect_pkg_manager(), pkg_install(), pkg_update(), pkg_resolve()
# Provides: DOCKER_CMD, DOCKER_COMPOSE_CMD
#
# Modder notes:
#   Change Docker installation method or add Podman support here.
#   Multi-distro: uses packaging.sh for distro-agnostic package installs.
# ============================================================================

dream_progress 30 "docker" "Setting up Docker"
show_phase 3 6 "Docker Setup" "~2 minutes"
ai "Preparing container runtime..."

# Ensure package manager is detected
[[ -z "${PKG_MANAGER:-}" ]] && detect_pkg_manager

# Helper wrappers that are safe even if DOCKER_CMD contains spaces (e.g. "sudo docker")
_docker_cmd_arr() {
    case "${DOCKER_CMD:-docker}" in
        "sudo docker") printf '%s %s\n' "sudo" "docker" ;;
        *)             printf '%s\n' "docker" ;;
    esac
}

docker_run() {
    # shellcheck disable=SC2207
    local -a cmd=($(_docker_cmd_arr))
    "${cmd[@]}" "$@"
}

docker_compose_run() {
    # shellcheck disable=SC2207
    local -a cmd=($(_docker_cmd_arr))
    "${cmd[@]}" compose "$@"
}

# Track whether docker group membership is active in this shell
DOCKER_NEEDS_SUDO=false

if [[ "$SKIP_DOCKER" == "true" ]]; then
    log "Skipping Docker installation (--skip-docker)"
elif command -v docker &> /dev/null; then
    ai_ok "Docker already installed: $(docker --version)"
else
    dream_progress 31 "docker" "Installing Docker engine"
    ai "Installing Docker..."

    if $DRY_RUN; then
        log "[DRY RUN] Would install Docker via official script"
    else
        # In non-interactive mode, fail fast if sudo requires a password
        if [[ "${INTERACTIVE:-true}" != "true" ]] && ! sudo -n true 2>/dev/null; then
            ai_bad "Docker is not installed and sudo requires a password."
            ai_bad "In non-interactive mode, either:"
            ai "  1. Run with passwordless sudo (NOPASSWD in sudoers)"
            ai "  2. Install Docker manually first, then re-run with --skip-docker"
            ai "  3. Run the installer interactively (without --non-interactive)"
            error "Cannot install Docker without sudo in non-interactive mode."
        fi

        case "$PKG_MANAGER" in
            apt|dnf|zypper)
                # Docker CE via get.docker.com (supports Debian/Ubuntu/Fedora/RHEL/SLES)
                tmpfile=$(mktemp /tmp/install-docker.XXXXXX.sh)
                if ! curl -fsSL --max-time 300 https://get.docker.com -o "$tmpfile" || ! sh "$tmpfile"; then
                    rm -f "$tmpfile"
                    error "Docker installation failed. Check network connectivity and try again."
                fi
                rm -f "$tmpfile"
                ;;
            pacman)
                # Arch/Manjaro/CachyOS/EndeavourOS -- Docker is in the official repos
                pkg_install docker
                ds_sudo systemctl enable --now docker.service 2>>"$LOG_FILE" || true
                ;;
            xbps)
                # Void Linux -- runit service management
                pkg_install docker
                ds_sudo ln -s /etc/sv/docker /var/service/ 2>/dev/null || true
                ;;
            apk)
                # Alpine -- OpenRC service management
                pkg_install docker
                ds_sudo rc-update add docker boot 2>>"$LOG_FILE" || true
                ds_sudo service docker start 2>>"$LOG_FILE" || true
                ;;
            *)
                # Unknown distro -- try get.docker.com as best-effort fallback
                tmpfile=$(mktemp /tmp/install-docker.XXXXXX.sh)
                if ! curl -fsSL --max-time 300 https://get.docker.com -o "$tmpfile" || ! sh "$tmpfile"; then
                    rm -f "$tmpfile"
                    error "Docker installation failed. Your distro (${DISTRO_ID:-unknown}) may not be supported. Install Docker manually and re-run with --skip-docker."
                fi
                rm -f "$tmpfile"
                ;;
        esac

        # Add the invoking user (not root) to the docker group
        target_user="${SUDO_USER:-$USER}"
        ds_sudo usermod -aG docker "$target_user"

        # In most cases group membership won't take effect until a new login shell.
        if ! id -nG "$target_user" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
            DOCKER_NEEDS_SUDO=true
        fi
    fi
fi

# Docker 29.3.x has a bug with /dev/dri device passthrough on AMD GPUs.
# Containers fail with: "error gathering device information while adding custom device /dev/dri"
# Pin to 29.2.x until this is resolved upstream.
# See: https://github.com/moby/moby/issues — device passthrough regression in 29.3.0
if command -v docker &>/dev/null && ! $DRY_RUN; then
    _docker_ver=$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo "0.0.0")
    if [[ "$_docker_ver" == 29.3.* ]] && [[ "${GPU_BACKEND:-}" == "amd" ]]; then
        ai_warn "Docker $_docker_ver has a known bug with AMD GPU device passthrough."
        ai "Downgrading to Docker 29.2.1 for AMD GPU compatibility..."
        # Detect package format
        if command -v apt-get &>/dev/null; then
            _distro_codename=$(. /etc/os-release 2>/dev/null && echo "$VERSION_CODENAME" || echo "noble")
            ds_sudo apt-get install -y --allow-downgrades \
                docker-ce=5:29.2.1-1~ubuntu."$(. /etc/os-release && echo "$VERSION_ID")"~"$_distro_codename" \
                docker-ce-cli=5:29.2.1-1~ubuntu."$(. /etc/os-release && echo "$VERSION_ID")"~"$_distro_codename" \
                >> "$LOG_FILE" 2>&1 && \
                ai_ok "Docker downgraded to 29.2.1 (AMD GPU fix)" || \
                ai_warn "Could not downgrade Docker. GPU containers may fail. Manual fix: sudo apt install docker-ce=5:29.2.1-1~ubuntu.24.04~noble"
        elif command -v dnf &>/dev/null; then
            ds_sudo dnf downgrade -y docker-ce-29.2.1 docker-ce-cli-29.2.1 >> "$LOG_FILE" 2>&1 && \
                ai_ok "Docker downgraded to 29.2.1 (AMD GPU fix)" || \
                ai_warn "Could not downgrade Docker. GPU containers may fail."
        elif command -v pacman &>/dev/null; then
            # pacman does not support version pinning from official repos.
            # Warn the user with clear manual instructions.
            ai_warn "Automatic Docker downgrade is not supported on pacman-based distros."
            ai_warn "GPU containers may fail with device passthrough errors."
            ai "  Manual fix: downgrade Docker from the Arch Linux Archive:"
            ai "    sudo pacman -U https://archive.archlinux.org/packages/d/docker/docker-1%3A29.2.1-1-x86_64.pkg.tar.zst"
            ai "  Or wait for Docker to fix the bug in a future release."
        else
            ai_warn "Could not downgrade Docker on this system. GPU containers may fail."
        fi
        ds_sudo systemctl restart docker 2>/dev/null || true
    fi
fi

# Decide whether to use sudo for the rest of this installer session
if [[ "${DOCKER_CMD:-}" == "" ]]; then
    if $DOCKER_NEEDS_SUDO; then
        warn "Docker installed, but group membership may not be active yet (re-login required)."
        if [[ "${INTERACTIVE:-true}" == "true" ]]; then
            echo ""
            read -p "  Continue this installer using 'sudo docker' for now? [Y/n] " -r < /dev/tty
            if [[ $REPLY =~ ^[Nn]$ ]]; then
                log "Please re-run after logging out and back in (or after 'newgrp docker')."
                exit 0
            fi
        else
            warn "Non-interactive mode: continuing with 'sudo docker'."
        fi
        DOCKER_CMD="sudo docker"
        DOCKER_COMPOSE_CMD="sudo docker compose"
    fi
fi

# Set docker command (use sudo if needed)
DOCKER_CMD="${DOCKER_CMD:-docker}"
DOCKER_COMPOSE_CMD="${DOCKER_COMPOSE_CMD:-docker compose}"

# Docker Compose check (v2 preferred, v1 fallback)
dream_progress 33 "docker" "Checking Docker Compose"
if docker_compose_run version &> /dev/null 2>&1; then
    ai_ok "Docker Compose v2 available"
elif command -v docker-compose &> /dev/null; then
    if [[ "$DOCKER_CMD" == "sudo docker" ]]; then
        DOCKER_COMPOSE_CMD="sudo docker-compose"
    else
        DOCKER_COMPOSE_CMD="docker-compose"
    fi
    ai_ok "Docker Compose v1 available (using docker-compose)"
else
    if [[ "$SKIP_DOCKER" == "true" ]]; then
        warn "Docker Compose not found (docker compose / docker-compose). Install manually or re-run without --skip-docker."
    elif $DRY_RUN; then
        log "[DRY RUN] Would install Docker Compose plugin"
    else
        ai "Installing Docker Compose plugin..."
        pkg_update
        # shellcheck disable=SC2046
        pkg_install $(pkg_resolve docker-compose-plugin)
    fi
fi

# ---------------------------------------------------------------------------
# Runtime sanity checks
# ---------------------------------------------------------------------------
# Goal: make this phase resilient across:
# - fresh installs (daemon not started yet)
# - non-interactive runs (can't prompt)
# - sudo-required sessions (docker group not applied yet)
# - systems where docker exists but user permissions are wrong

_docker_try_with_optional_sudo() {
    # If DOCKER_CMD isn't already sudo docker, try docker first and fall back to sudo docker.
    if docker_run "$@" &>/dev/null; then
        return 0
    fi

    if [[ "$DOCKER_CMD" != "sudo docker" ]] && command -v sudo &>/dev/null; then
        DOCKER_CMD="sudo docker"
        DOCKER_COMPOSE_CMD="sudo docker compose"
        if docker_run "$@" &>/dev/null; then
            return 0
        fi
    fi

    return 1
}

_docker_daemon_start_hint() {
    warn "Docker daemon does not appear to be running or accessible."
    warn "Common fixes:"
    warn "  - Linux (systemd): sudo systemctl enable --now docker"
    warn "  - Linux (non-systemd): start dockerd using your init system"
    warn "  - WSL2: ensure Docker Desktop is running"
}

_docker_ensure_daemon() {
    # Returns 0 if docker responds to 'info' (with optional sudo fallback).
    # If not, tries to start docker on systemd systems, then retries.

    if _docker_try_with_optional_sudo info; then
        return 0
    fi

    # Try to start docker service if systemd is present
    if command -v systemctl &>/dev/null; then
        ai_warn "Docker not responding; attempting to start docker service..."
        if ! $DRY_RUN; then
            # Clear start-limit-hit if docker has been restarted too many times
            if systemctl is-failed docker &>/dev/null; then
                ai_warn "Docker is in failed state (possible start-limit-hit). Resetting..."
                ds_sudo systemctl reset-failed docker 2>>"$LOG_FILE" || true
            fi
            ds_sudo systemctl start docker 2>>"$LOG_FILE" || true
            # Give the daemon a moment to initialize
            sleep 2
        fi
        if _docker_try_with_optional_sudo info; then
            return 0
        fi
    fi

    _docker_daemon_start_hint
    return 1
}

_docker_compose_detect_cmd() {
    # Prefer v2 (docker compose). If docker requires sudo, wrapper will handle it.
    if docker_compose_run version &>/dev/null 2>&1; then
        echo "docker compose"
        return 0
    fi

    # v1 fallback
    if command -v docker-compose &>/dev/null; then
        echo "docker-compose"
        return 0
    fi

    echo ""
    return 1
}

_docker_compose_verify() {
    local detected
    detected="$(_docker_compose_detect_cmd || true)"

    if [[ -n "$detected" ]]; then
        ai_ok "Compose detected: $detected"
        return 0
    fi

    return 1
}

_docker_post_install_checks() {
    # Best-effort checks. Should not hard-fail in dry-run.
    if $DRY_RUN; then
        log "[DRY RUN] Skipping docker runtime checks"
        return 0
    fi

    # Ensure daemon is reachable (and set DOCKER_CMD to sudo docker if needed)
    if ! _docker_ensure_daemon; then
        error "Docker is installed but not usable (daemon not running or permissions issue)."
    fi

    # Show effective docker command (for later phases)
    log "Using docker command: $DOCKER_CMD"

    # Verify compose availability; if missing, install compose plugin when possible.
    if ! _docker_compose_verify; then
        if [[ "$SKIP_DOCKER" == "true" ]]; then
            warn "Compose missing and --skip-docker set; cannot install compose automatically."
            return 0
        fi

        ai_warn "Docker Compose not detected; attempting install of compose plugin..."
        pkg_update
        # shellcheck disable=SC2046
        pkg_install $(pkg_resolve docker-compose-plugin) || true

        if ! _docker_compose_verify; then
            warn "Compose still not detected after install attempt."
            warn "You may need to install Docker Compose manually for your distro."
        fi
    fi

    # Basic engine health
    if docker_run version &>/dev/null 2>&1; then
        ai_ok "Docker engine responding"
    else
        warn "Docker engine did not respond to 'docker version'"
    fi

    # Optional: give the user a clear hint if they are likely missing group perms
    if [[ "$DOCKER_CMD" == "sudo docker" ]]; then
        warn "Docker commands are running via sudo in this installer session."
        warn "After the install finishes, log out/in (or run 'newgrp docker') to use docker without sudo."
    fi
}

dream_progress 35 "docker" "Running Docker post-install checks"
_docker_post_install_checks

# NVIDIA Container Toolkit (skip for AMD — uses /dev/dri + /dev/kfd passthrough)
if [[ $GPU_COUNT -gt 0 && "$GPU_BACKEND" == "nvidia" ]]; then
    dream_progress 36 "docker" "Checking NVIDIA Container Toolkit"
    if command -v nvidia-container-cli &> /dev/null 2>&1; then
        ai_ok "NVIDIA Container Toolkit installed"
        # Always regenerate CDI spec — driver version may have changed since last run
        if command -v nvidia-ctk &>/dev/null && ! $DRY_RUN; then
            ds_sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml 2>>"$LOG_FILE" || true
        fi
    else
        ai "Installing NVIDIA Container Toolkit..."
        if ! $DRY_RUN; then
            # Distro-aware repo setup + install
            case "$PKG_MANAGER" in
                apt)
                    # Add NVIDIA GPG key (apt's signed-by trust anchor for the toolkit repo)
                    curl -fsSL --max-time 60 https://nvidia.github.io/libnvidia-container/gpgkey | \
                        ds_sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
                    if [[ ! -s /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg ]]; then
                        error "NVIDIA Container Toolkit keyring download failed (empty or missing /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg). Check network connectivity to nvidia.github.io."
                    fi
                    # Fingerprint pin: defense against MITM at the CDN/DNS edge serving
                    # nvidia.github.io. NVIDIA does not publish this fingerprint in their
                    # install docs (industry-wide gap), but the value is derivable from
                    # the public key file at the canonical URL.
                    # Source: extracted from the gpgkey blob at
                    # https://nvidia.github.io/libnvidia-container/gpgkey on 2026-05-03
                    # via: gpg --with-colons --import-options show-only --import.
                    # If NVIDIA rotates the key, update this constant and bump the date.
                    _nvidia_expected_fp="C95B321B61E88C1809C4F759DDCAE044F796ECB0"
                    # Read keyring without sudo — /usr/share/keyrings/*.gpg is world-readable
                    # (dearmor output of a public key). show-only avoids touching any keyring.
                    _nvidia_actual_fp=$(gpg --with-colons --import-options show-only --import \
                        /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg 2>/dev/null \
                        | awk -F: '/^fpr:/ {print $10; exit}')
                    if [[ -z "$_nvidia_actual_fp" ]]; then
                        warn "Could not extract fingerprint from downloaded NVIDIA keyring — proceeding without pin verification."
                    elif [[ "$_nvidia_actual_fp" != "$_nvidia_expected_fp" ]]; then
                        warn "NVIDIA Container Toolkit GPG fingerprint MISMATCH:"
                        warn "  expected: $_nvidia_expected_fp"
                        warn "  got:      $_nvidia_actual_fp"
                        warn "Possible MITM, or NVIDIA rotated the key. Manually verify against NVIDIA's published fingerprint before trusting."
                        warn "Proceeding because operator chose pin-and-warn over hard-fail. Set DS_GPG_HARD_FAIL=1 to error instead."
                        if [[ "${DS_GPG_HARD_FAIL:-}" == "1" ]]; then
                            error "Aborting due to GPG fingerprint mismatch (DS_GPG_HARD_FAIL=1)."
                        fi
                    else
                        log "NVIDIA Container Toolkit GPG fingerprint verified: $_nvidia_expected_fp"
                    fi
                    unset _nvidia_expected_fp _nvidia_actual_fp
                    curl -s -L --max-time 60 https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
                        sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
                        ds_sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list > /dev/null
                    # Verify we got a valid repo file, not an HTML 404
                    if grep -q '<html' /etc/apt/sources.list.d/nvidia-container-toolkit.list 2>/dev/null; then
                        warn "Failed to download NVIDIA Container Toolkit repo list. Trying fallback..."
                        echo "deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://nvidia.github.io/libnvidia-container/stable/deb/\$(ARCH) /" | \
                            ds_sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list > /dev/null
                    fi
                    ds_sudo apt-get update
                    if ! ds_sudo apt-get install -y nvidia-container-toolkit; then
                        error "Failed to install NVIDIA Container Toolkit. Check network connectivity and GPU drivers."
                    fi
                    ;;
                dnf)
                    curl -s -L --max-time 60 https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo | \
                        ds_sudo tee /etc/yum.repos.d/nvidia-container-toolkit.repo > /dev/null
                    if ! ds_sudo dnf install -y nvidia-container-toolkit 2>>"$LOG_FILE"; then
                        error "Failed to install NVIDIA Container Toolkit. Check network connectivity and NVIDIA repo configuration."
                    fi
                    ;;
                pacman)
                    # nvidia-container-toolkit is in AUR; check for common AUR helpers
                    if command -v yay &>/dev/null; then
                        yay -S --noconfirm nvidia-container-toolkit 2>>"$LOG_FILE" || \
                            error "Failed to install nvidia-container-toolkit via yay."
                    elif command -v paru &>/dev/null; then
                        paru -S --noconfirm nvidia-container-toolkit 2>>"$LOG_FILE" || \
                            error "Failed to install nvidia-container-toolkit via paru."
                    else
                        warn "nvidia-container-toolkit requires an AUR helper (yay or paru)."
                        warn "Install one, then run: yay -S nvidia-container-toolkit"
                        error "No AUR helper found. Install yay or paru first."
                    fi
                    ;;
                zypper)
                    ds_sudo zypper addrepo https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo 2>/dev/null || true
                    ds_sudo zypper --non-interactive --gpg-auto-import-keys refresh 2>>"$LOG_FILE"
                    if ! ds_sudo zypper --non-interactive install nvidia-container-toolkit 2>>"$LOG_FILE"; then
                        error "Failed to install NVIDIA Container Toolkit."
                    fi
                    ;;
                *)
                    error "Cannot install NVIDIA Container Toolkit: unsupported package manager '${PKG_MANAGER}'. Install it manually: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html"
                    ;;
            esac

            ds_sudo nvidia-ctk runtime configure --runtime=docker
            ds_sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml 2>>"$LOG_FILE" || true
            ds_sudo systemctl restart docker
        fi
        if command -v nvidia-container-cli &> /dev/null 2>&1; then
            ai_ok "NVIDIA Container Toolkit installed"
        else
            $DRY_RUN && ai_ok "[DRY RUN] Would install NVIDIA Container Toolkit" || error "NVIDIA Container Toolkit installation failed — nvidia-container-cli not found after install."
        fi
    fi
fi
