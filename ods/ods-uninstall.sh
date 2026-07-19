#!/bin/bash
# ods-uninstall.sh - ODS Clean Uninstaller
# Removes all ODS components, data, and system modifications.
# Usage: ./ods-uninstall.sh [--keep-models] [--keep-data] [--force]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$HOME/ods}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

resolve_compose_flags() {
    local flags=""

    if [[ -f "$INSTALL_DIR/.compose-flags" ]]; then
        flags="$(tr '\n' ' ' < "$INSTALL_DIR/.compose-flags" | xargs 2>/dev/null || true)"
    fi

    if [[ -z "$flags" && -x "$INSTALL_DIR/scripts/resolve-compose-stack.sh" ]]; then
        flags="$("$INSTALL_DIR/scripts/resolve-compose-stack.sh" \
            --script-dir "$INSTALL_DIR" \
            --tier "${TIER:-1}" \
            --gpu-backend "${GPU_BACKEND:-nvidia}" \
            --gpu-count "${GPU_COUNT:-1}" \
            --ods-mode "${ODS_MODE:-local}" 2>/dev/null || true)"
    fi

    if [[ -z "$flags" && -f "$INSTALL_DIR/docker-compose.base.yml" ]]; then
        flags="-f docker-compose.base.yml"
        case "${GPU_BACKEND:-}" in
            amd|nvidia|intel|apple|arc|cpu)
                [[ -f "$INSTALL_DIR/docker-compose.${GPU_BACKEND}.yml" ]] && flags="$flags -f docker-compose.${GPU_BACKEND}.yml"
                ;;
        esac
    fi

    printf '%s\n' "$flags"
}

KEEP_MODELS=false
KEEP_DATA=false
FORCE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-models) KEEP_MODELS=true; shift ;;
        --keep-data)   KEEP_DATA=true; shift ;;
        --force)       FORCE=true; shift ;;
        -h|--help)
            cat << EOF
ODS Uninstaller

Usage: $(basename "$0") [OPTIONS]

Options:
    --keep-models   Keep downloaded AI models (saves re-download time)
    --keep-data     Keep user data (chat history, n8n workflows, etc.)
    --force         Skip confirmation prompts
    -h, --help      Show this help

This will remove:
    - Docker containers, images, and volumes for ODS
    - Installation directory ($INSTALL_DIR)
    - Systemd user services (opencode-web, openclaw timers)
    - macOS LaunchAgents (com.ods.host-agent, com.ods.opencode-web, legacy agents)
    - CLI symlinks (/usr/local/bin/ods, ~/.local/bin/ods, legacy /usr/local/bin/ods-cli)
    - Backup directory (~/.ods)

EOF
            exit 0
            ;;
        *) log_error "Unknown option: $1"; exit 1 ;;
    esac
done

echo ""
echo -e "${RED}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${RED}║         ODS UNINSTALLER                ║${NC}"
echo -e "${RED}╚══════════════════════════════════════════════════╝${NC}"
echo ""

# Detect install dir
if [[ -d "$SCRIPT_DIR" && -f "$SCRIPT_DIR/ods-cli" ]]; then
    INSTALL_DIR="$SCRIPT_DIR"
fi

if [[ ! -d "$INSTALL_DIR" ]]; then
    log_error "Install directory not found: $INSTALL_DIR"
    exit 1
fi

log_info "Install directory: $INSTALL_DIR"
$KEEP_MODELS && log_info "Keeping models (--keep-models)"
$KEEP_DATA && log_info "Keeping user data (--keep-data)"
echo ""

if [[ -f "$INSTALL_DIR/.env" ]]; then
    if [[ -f "$INSTALL_DIR/lib/safe-env.sh" ]]; then
        # shellcheck source=lib/safe-env.sh
        . "$INSTALL_DIR/lib/safe-env.sh"
        load_env_file "$INSTALL_DIR/.env"
    else
        log_warn "safe-env.sh not found; using uninstall defaults without loading .env"
    fi
fi

if [[ "$FORCE" != "true" ]]; then
    echo -e "${YELLOW}This will permanently remove ODS and its components.${NC}"
    read -rp "Are you sure? Type 'yes' to confirm: " confirm
    if [[ "$confirm" != "yes" ]]; then
        log_info "Uninstall cancelled."
        exit 0
    fi
    echo ""
fi

# 1. Stop and remove Docker containers
log_info "Stopping Docker containers..."
cd "$INSTALL_DIR" 2>/dev/null || true
if command -v docker &>/dev/null; then
    # Use ODS's resolved compose stack. The repo does not ship a
    # top-level docker-compose.yml, so bare `docker compose down` can fail with
    # "no configuration file provided" even from the correct install dir.
    compose_flags="$(resolve_compose_flags)"
    compose_down_args=(down)
    if [[ "$KEEP_DATA" != "true" ]]; then
        compose_down_args+=(-v)
    fi
    compose_down_args+=(--remove-orphans)

    if [[ -n "$compose_flags" ]]; then
        read -ra compose_args <<< "$compose_flags"
        docker compose "${compose_args[@]}" "${compose_down_args[@]}" 2>/dev/null || \
            log_warn "docker compose cleanup failed; falling back to container/volume discovery"
    else
        log_warn "No compose files resolved; falling back to container/volume discovery"
    fi

    # Remove any remaining ods-* containers
    ods_containers=$(docker ps -a --filter "name=ods-" --format "{{.Names}}" 2>/dev/null || true)
    if [[ -n "$ods_containers" ]]; then
        log_info "Removing ODS containers..."
        echo "$ods_containers" | xargs docker rm -f 2>/dev/null || true
    fi

    # Remove ods-specific Docker volumes unless data preservation was requested.
    if [[ "$KEEP_DATA" == "true" ]]; then
        log_info "Keeping Docker volumes (--keep-data)"
    else
        ods_volumes=$(docker volume ls --filter "name=ods" --format "{{.Name}}" 2>/dev/null || true)
        if [[ -n "$ods_volumes" ]]; then
            log_info "Removing Docker volumes..."
            echo "$ods_volumes" | xargs docker volume rm 2>/dev/null || true
        fi
    fi

    log_ok "Docker cleanup complete"
else
    log_warn "Docker not found — skipping container cleanup"
fi

# 2. Stop and remove systemd user services
log_info "Removing systemd user services..."
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
for unit in opencode-web.service openclaw-session-cleanup.timer \
            memory-shepherd-workspace.timer memory-shepherd-memory.timer \
            openclaw-session-cleanup.service \
            memory-shepherd-workspace.service memory-shepherd-memory.service \
            ods-host-agent.service; do
    if [[ -f "$SYSTEMD_USER_DIR/$unit" ]]; then
        systemctl --user disable --now "$unit" 2>/dev/null || true
        rm -f "$SYSTEMD_USER_DIR/$unit"
    fi
done
systemctl --user daemon-reload 2>/dev/null || true

# 2a. Remove macOS LaunchAgents (#1882). install-macos.sh creates
# com.ods.host-agent and com.ods.opencode-web as RunAtLoad+KeepAlive agents;
# without bootout they respawn forever against the deleted install directory.
# The legacy labels match the installer's own stale-agent cleanup. This runs
# BEFORE orphan reaping so KeepAlive cannot resurrect what we kill below.
# Missing labels/plists are tolerated; failed cleanup only warns.
if [[ "$(uname -s)" == "Darwin" ]]; then
    log_info "Removing macOS LaunchAgents..."
    _ods_uid="$(id -u)"
    _ods_agents_cleaned=true
    for _ods_agent_label in com.ods.host-agent com.ods.opencode-web \
                            com.ods.llama-server com.ods.full-model-download; do
        _ods_agent_plist="$HOME/Library/LaunchAgents/${_ods_agent_label}.plist"
        if launchctl print "gui/${_ods_uid}/${_ods_agent_label}" >/dev/null 2>&1; then
            if launchctl bootout "gui/${_ods_uid}/${_ods_agent_label}" 2>/dev/null; then
                log_info "  Booted out ${_ods_agent_label}"
            else
                _ods_agents_cleaned=false
                log_warn "Could not boot out LaunchAgent ${_ods_agent_label}"
            fi
        fi
        if [[ -f "$_ods_agent_plist" ]]; then
            rm -f "$_ods_agent_plist" 2>/dev/null || true
            if [[ -f "$_ods_agent_plist" ]]; then
                _ods_agents_cleaned=false
                log_warn "Could not remove $_ods_agent_plist"
            else
                log_info "  Removed ${_ods_agent_plist##*/}"
            fi
        fi
    done
    if $_ods_agents_cleaned; then
        log_ok "macOS LaunchAgents removed"
    else
        log_warn "macOS LaunchAgent cleanup incomplete"
    fi
    unset _ods_agent_label _ods_agent_plist _ods_uid _ods_agents_cleaned
fi

# Reap orphan host-managed processes that survive `systemctl --user stop`.
# These were observed in the fleet test surviving multiple uninstall/reinstall
# cycles, holding their ports and serving stale state to users on the next
# install:
#
#   - `opencode web` and `opencode serve`: the systemd unit's ExecStart has
#     changed across versions (ODS moved from the legacy `web`
#     subcommand to the newer `serve` subcommand). `systemctl stop` reaps
#     whatever process the CURRENT unit definition started, but a leftover
#     process from a PRIOR unit's ExecStart keeps its port. The next install
#     rewrites the unit, the new systemd-managed process fails to bind, and
#     the user ends up hitting the 19-hour-old orphan with no DB wired up.
#
#   - Native macOS llama-server: the bootstrap-upgrade.sh and install-macos.sh
#     spawn the Metal binary directly and track it via a PID file. If the file
#     gets out of sync (PID reused, kill not flushed, prior install path
#     changed), the kill misses and a stale llama-server keeps port 8080.
#     `ods-uninstall` currently has no path that catches this.
#
# Two passes: SIGTERM first (let the process flush state), then SIGKILL for
# anything still alive after 2s. Patterns are scoped to the per-user OpenCode
# binary path and this install's `bin/llama-server` so we don't touch unrelated
# `opencode` or `llama-server` binaries the user may have elsewhere.
log_info "Reaping any orphan host-managed processes..."
_ods_uninstall_orphan_pids=()
if command -v pgrep >/dev/null 2>&1; then
    # opencode (both subcommands; bin path is per-user, not per-install)
    while IFS= read -r _pid; do
        [[ -n "$_pid" ]] && _ods_uninstall_orphan_pids+=("$_pid")
    done < <(pgrep -f '\.opencode/bin/opencode (web|serve)' 2>/dev/null || true)
    # macOS-native llama-server: only matches this install's shipped binary
    while IFS= read -r _pid; do
        [[ -n "$_pid" ]] && _ods_uninstall_orphan_pids+=("$_pid")
    done < <(pgrep -f "$INSTALL_DIR/bin/llama-server" 2>/dev/null || true)
fi
if (( ${#_ods_uninstall_orphan_pids[@]} > 0 )); then
    log_info "  Sending SIGTERM to ${#_ods_uninstall_orphan_pids[@]} orphan PID(s): ${_ods_uninstall_orphan_pids[*]}"
    for _pid in "${_ods_uninstall_orphan_pids[@]}"; do kill "$_pid" 2>/dev/null || true; done
    sleep 2
    for _pid in "${_ods_uninstall_orphan_pids[@]}"; do
        if kill -0 "$_pid" 2>/dev/null; then
            log_info "  PID $_pid still alive, sending SIGKILL"
            kill -9 "$_pid" 2>/dev/null || true
        fi
    done
fi
unset _ods_uninstall_orphan_pids _pid

# Remove system-mode ods-host-agent unit (migrated from --user mode).
# Idempotent — no-op if the unit was never installed (e.g. older user-mode installs).
if systemctl is-enabled ods-host-agent.service >/dev/null 2>&1; then
    if ! timeout 20s sudo systemctl disable --now ods-host-agent.service 2>/dev/null; then
        log_warn "ods-host-agent did not stop cleanly; forcing service shutdown"
        sudo systemctl kill -s SIGKILL ods-host-agent.service 2>/dev/null || true
        timeout 10s sudo systemctl disable ods-host-agent.service 2>/dev/null || true
    fi
fi
sudo rm -f /etc/systemd/system/ods-host-agent.service 2>/dev/null || true
sudo systemctl daemon-reload 2>/dev/null || true
log_ok "Systemd services removed"

# 3. Remove CLI symlinks
_removed_cli_symlink=false
for _ods_cli_link in "/usr/local/bin/ods" "$HOME/.local/bin/ods" "/usr/local/bin/ods-cli"; do
    if [[ -L "$_ods_cli_link" ]]; then
        log_info "Removing CLI symlink: $_ods_cli_link"
        case "$_ods_cli_link" in
            /usr/local/bin/*)
                sudo rm -f "$_ods_cli_link" 2>/dev/null || rm -f "$_ods_cli_link" 2>/dev/null || true
                ;;
            *)
                rm -f "$_ods_cli_link" 2>/dev/null || true
                ;;
        esac
        _removed_cli_symlink=true
    fi
done
if $_removed_cli_symlink; then
    log_ok "CLI symlinks removed"
fi
unset _removed_cli_symlink _ods_cli_link

# 4. Remove desktop file
DESKTOP_FILE="$HOME/.local/share/applications/ods.desktop"
if [[ -f "$DESKTOP_FILE" ]]; then
    rm -f "$DESKTOP_FILE"
    log_ok "Desktop entry removed"
fi

# 5. Remove install directory (with optional data/model preservation)
log_info "Removing installation directory..."
INSTALL_DIR_CLEANED=true
if $KEEP_MODELS && [[ -d "$INSTALL_DIR/data/models" ]]; then
    MODELS_BACKUP="$HOME/.ods-models-backup"
    mkdir -p "$MODELS_BACKUP"
    mv "$INSTALL_DIR/data/models"/* "$MODELS_BACKUP/" 2>/dev/null || true
    log_info "Models preserved at: $MODELS_BACKUP"
fi

if $KEEP_DATA; then
    # Remove everything except data/. Container-UID files under data/ stay
    # untouched (--keep-data implies preserving them anyway).
    find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 ! -name 'data' -exec rm -rf {} + 2>/dev/null || true
    log_info "User data preserved at: $INSTALL_DIR/data/"
else
    # Containers (open-webui, qdrant, baserow, searxng, ...) write into
    # $INSTALL_DIR/data/ as their own UIDs, not the host user's. Plain
    # `rm -rf` then fails on every file with Permission denied and exits
    # with $INSTALL_DIR still present — which silently turns "uninstall"
    # into "uninstall everything but the install directory."
    #
    # Reclaim ownership before the rm. The installer itself does the same
    # dance at `installers/phases/06-directories.sh` after picking up
    # container-owned dirs from a prior run, so the pattern is already
    # blessed for this codebase. If sudo is unavailable, fall back to a
    # best-effort rm and let the operator see the failures explicitly.
    if command -v sudo >/dev/null 2>&1; then
        sudo chown -R "$(id -u):$(id -g)" "$INSTALL_DIR" 2>/dev/null || \
            log_warn "Could not chown $INSTALL_DIR (container-UID files may remain)"
    else
        log_warn "sudo not available; attempting non-privileged removal of $INSTALL_DIR"
    fi
    rm -rf "$INSTALL_DIR" || \
        log_warn "Could not fully remove $INSTALL_DIR"
    if [[ -d "$INSTALL_DIR" ]]; then
        INSTALL_DIR_CLEANED=false
        log_warn "Install dir still present at $INSTALL_DIR - likely container-UID files that need: sudo rm -rf \"$INSTALL_DIR\""
    fi
fi
if $INSTALL_DIR_CLEANED; then
    log_ok "Installation directory cleaned"
else
    log_warn "Installation directory cleanup incomplete"
fi

# 6. Remove backup directory
if [[ -d "$HOME/.ods" ]]; then
    log_info "Removing backup directory..."
    rm -rf "$HOME/.ods"
    log_ok "Backups removed"
fi

# 7. Remove OpenCode config (if we created it)
OPENCODE_CONFIG="$HOME/.config/opencode/opencode.json"
if [[ -f "$OPENCODE_CONFIG" ]] && grep -q "llama-server" "$OPENCODE_CONFIG" 2>/dev/null; then
    rm -f "$OPENCODE_CONFIG"
    log_ok "OpenCode config removed"
fi

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     ODS has been uninstalled.           ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
if $KEEP_MODELS; then
    echo "Your models were saved to: $HOME/.ods-models-backup"
    echo "To reuse them on reinstall, move them back to ~/ods/data/models/"
fi
if $KEEP_DATA; then
    echo "Your user data was preserved at: $INSTALL_DIR/data/"
fi
