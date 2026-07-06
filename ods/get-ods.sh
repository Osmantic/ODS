#!/bin/bash
# ODS Bootstrap Installer
# curl -fsSL https://raw.githubusercontent.com/Osmantic/ODS/main/ods/get-ods.sh | bash
#
# Detects OS, clones repo, runs installer.

set -euo pipefail

# Anchor CWD to a known-good directory. Without this, a user who just
# uninstalled ODS and immediately re-runs the bootstrap from the
# same terminal will land here with a deleted working directory — `git
# clone` then fails with `fatal: Unable to read current working directory`
# and the user sees a misleading "check your internet connection" message.
ODS_BOOTSTRAP_ROOT="${HOME:-/tmp}"
if ! cd "$ODS_BOOTSTRAP_ROOT" 2>/dev/null; then
    ODS_BOOTSTRAP_ROOT="/tmp"
    cd "$ODS_BOOTSTRAP_ROOT" 2>/dev/null || {
        echo "[error] Cannot find a usable working directory (\$HOME and /tmp both inaccessible)." >&2
        exit 1
    }
fi

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

REPO_URL="${ODS_REPO_URL:-https://github.com/Osmantic/ODS.git}"
INSTALL_DIR="${ODS_INSTALL_DIR:-$ODS_BOOTSTRAP_ROOT/ods}"
LEGACY_DREAMSERVER_DIR="${DREAMSERVER_INSTALL_DIR:-$ODS_BOOTSTRAP_ROOT/dream-server}"
ODS_REF="${ODS_REF:-${ODS_BOOTSTRAP_REF:-}}"
BOOTSTRAP_FORCE=false
BOOTSTRAP_NON_INTERACTIVE=false

for _arg in "$@"; do
    case "$_arg" in
        --force) BOOTSTRAP_FORCE=true ;;
        --non-interactive) BOOTSTRAP_NON_INTERACTIVE=true ;;
    esac
done

log()     { echo -e "${CYAN}[ods]${NC} $1"; }
success() { echo -e "${GREEN}[  ok ]${NC} $1"; }
warn()    { echo -e "${YELLOW}[warn ]${NC} $1"; }
error()   { echo -e "${RED}[error]${NC} $1"; exit 1; }

remove_install_dir() {
    local target_dir="$1"

    if rm -rf -- "$target_dir" 2>/dev/null; then
        return 0
    fi

    if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        warn "Normal removal failed; retrying with sudo for root-owned container data."
        sudo -n rm -rf -- "$target_dir" && return 0
    fi

    return 1
}

is_truthy() {
    case "${1:-}" in
        1|true|TRUE|yes|YES|y|Y) return 0 ;;
        *) return 1 ;;
    esac
}

refuse_legacy_dreamserver_install() {
    is_truthy "${ODS_ALLOW_DREAMSERVER_PARALLEL:-}" && return 0

    local findings=()
    if [[ -d "$LEGACY_DREAMSERVER_DIR" ]] && {
        [[ -f "$LEGACY_DREAMSERVER_DIR/.env" ]] ||
        [[ -f "$LEGACY_DREAMSERVER_DIR/dream-cli" ]] ||
        [[ -f "$LEGACY_DREAMSERVER_DIR/docker-compose.yml" ]] ||
        [[ -d "$LEGACY_DREAMSERVER_DIR/data" ]]
    }; then
        findings+=("install directory: $LEGACY_DREAMSERVER_DIR")
    fi

    if command -v docker >/dev/null 2>&1; then
        local legacy_containers
        legacy_containers=$(docker ps -a --filter "name=^/dream-" --format '{{.Names}}' 2>/dev/null || true)
        [[ -n "$legacy_containers" ]] && findings+=("containers: $(echo "$legacy_containers" | tr '\n' ' ')")
    fi

    if (( ${#findings[@]} > 0 )); then
        echo ""
        warn "Existing DreamServer install detected before first ODS install."
        echo ""
        echo "ODS uses the same default ports and service roles as DreamServer."
        echo "Resolve the old install intentionally before installing ODS, or run in"
        echo "parallel only after choosing separate ports and an explicit install dir."
        echo ""
        echo "Detected:"
        printf '  - %s\n' "${findings[@]}"
        echo ""
        echo "To proceed after you have isolated the old stack:"
        echo "  ODS_ALLOW_DREAMSERVER_PARALLEL=1 ODS_INSTALL_DIR=\"$INSTALL_DIR\" bash get-ods.sh"
        echo ""
        exit 1
    fi
}

# ── Banner ──────────────────────────────────────
echo ""
echo -e "${BOLD}${BLUE}"
cat << 'BANNER'
   OOOOO  DDDD   SSSSS
  OO   OO DD DD SS
  OO   OO DD DD  SSS
  OO   OO DD DD    SS
   OOOOO  DDDD  SSSS
BANNER
echo -e "${NC}"
echo -e "${BOLD}  Osmantic Deployment System - Local AI for Everyone${NC}"
echo ""

# ── Detect OS ──────────────────────────────────────
detect_os() {
    if [[ -f /proc/version ]] && grep -qi microsoft /proc/version 2>/dev/null; then
        echo "wsl"
    elif [[ "${OSTYPE:-}" == "darwin"* ]]; then
        echo "macos"
    elif [[ "${OSTYPE:-}" == linux* ]]; then
        echo "linux"
    else
        echo "unknown"
    fi
}

OS=$(detect_os)
log "Detected OS: $OS"

case "$OS" in
    linux|wsl)
        success "Linux/WSL detected — full support"
        ;;
    macos)
        warn "macOS detected — limited GPU support (Apple Silicon MLX coming soon)"
        ;;
    unknown)
        error "Unsupported OS. ODS requires Linux, WSL, or macOS."
        ;;
esac

# ── Check prerequisites ──────────────────────────────
log "Checking prerequisites..."

# Docker check (informational — the installer auto-installs Docker if missing)
if command -v docker &> /dev/null; then
    success "Docker found: $(docker --version | head -1)"
else
    warn "Docker not found — the installer will attempt to install it"
fi

# GPU check (early info — real detection happens in the installer)
_gpu_found=false
for _v in /sys/class/drm/card*/device/vendor; do
    case "$(cat "$_v" 2>/dev/null)" in
        0x10de) # NVIDIA
            if command -v nvidia-smi &> /dev/null; then
                # Capture all output then take the first line in-shell. Piping
                # `... | head -1` SIGPIPEs nvidia-smi (~17% on multi-GPU hosts):
                # head closes the pipe after line 1, nvidia-smi exits 141, and
                # pipefail propagates the failure → `set -e` aborts the bootstrap.
                _info=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null) || _info=""
                _info=${_info%%$'\n'*}
                [[ -n "$_info" ]] && success "NVIDIA GPU detected: $_info" && _gpu_found=true
            else
                success "NVIDIA GPU detected (driver not yet installed — installer will handle it)"
                _gpu_found=true
            fi ;;
        0x1002) # AMD
            success "AMD GPU detected"
            _gpu_found=true ;;
        0x8086) # Intel — only flag if it looks like Arc (discrete)
            if lspci 2>/dev/null | grep -qi 'VGA.*Intel.*Arc'; then
                success "Intel Arc GPU detected"
                _gpu_found=true
            fi ;;
    esac
    $_gpu_found && break
done
if ! $_gpu_found; then
    warn "No GPU detected — CPU-only mode will be used (slow but functional)"
fi

# git
if command -v git &> /dev/null; then
    success "git found: $(git --version | head -1)"
else
    log "Installing git..."
    if [[ "$OS" == "macos" ]]; then
        xcode-select --install 2>/dev/null || true
        command -v git &> /dev/null || error "Please install git: https://git-scm.com"
    else
        if command -v apt-get &> /dev/null; then
            sudo apt-get update -qq && sudo apt-get install -y -qq git
        elif command -v dnf &> /dev/null; then
            sudo dnf install -y -q git
        elif command -v yum &> /dev/null; then
            sudo yum install -y -q git
        elif command -v pacman &> /dev/null; then
            sudo pacman -Sy --noconfirm git
        elif command -v zypper &> /dev/null; then
            sudo zypper --non-interactive --gpg-auto-import-keys refresh
            sudo zypper --non-interactive install -y git
        else
            error "Cannot install git automatically. Please install git and re-run."
        fi
    fi
    success "git installed"
fi

# curl
if command -v curl &> /dev/null; then
    success "curl found"
else
    log "Installing curl..."
    if command -v apt-get &> /dev/null; then
        sudo apt-get install -y -qq curl
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y -q curl
    elif command -v yum &> /dev/null; then
        sudo yum install -y -q curl
    elif command -v pacman &> /dev/null; then
        sudo pacman -Sy --noconfirm curl
    elif command -v zypper &> /dev/null; then
        sudo zypper --non-interactive --gpg-auto-import-keys refresh
        sudo zypper --non-interactive install -y curl
    else
        error "Please install curl and re-run."
    fi
    success "curl installed"
fi

# docker (the installer auto-installs Docker if missing — don't block here)
if command -v docker &> /dev/null; then
    success "docker found: $(docker --version | head -1)"
    if docker compose version &> /dev/null || docker-compose --version &> /dev/null; then
        success "docker compose found"
    else
        warn "Docker Compose not found — the installer will attempt to set it up"
    fi
else
    warn "Docker not found — the installer will attempt to install it"
fi

# GPU pre-check already done above — real detection happens in the installer

# ── Check for existing installation ──────────────────
if [[ -d "$INSTALL_DIR" ]]; then
    if [[ -f "$INSTALL_DIR/.env" ]]; then
        warn "ODS already installed at $INSTALL_DIR"
        echo ""
        echo "  To start:     cd $INSTALL_DIR && docker compose up -d"
        echo "  To reinstall: rm -rf $INSTALL_DIR && re-run this script"
        echo "  To update:    cd $INSTALL_DIR && ./ods-cli update"
        echo ""
        exit 0
    else
        warn "Directory exists but incomplete install at $INSTALL_DIR"
        echo ""
        if [[ "$BOOTSTRAP_FORCE" == "true" ]]; then
            echo "  Removing incomplete install because --force was provided."
            remove_install_dir "$INSTALL_DIR" || error "Failed to remove incomplete install at $INSTALL_DIR. Try: sudo rm -rf \"$INSTALL_DIR\""
        elif [[ "$BOOTSTRAP_NON_INTERACTIVE" == "true" ]]; then
            echo "  Aborting. Re-run with --force to remove it automatically, or remove manually with: rm -rf $INSTALL_DIR"
            exit 1
        else
            echo -n "  Remove and reinstall? [y/N] "
            read -r response
            if [[ "$response" =~ ^[Yy]$ ]]; then
                remove_install_dir "$INSTALL_DIR" || error "Failed to remove incomplete install at $INSTALL_DIR. Try: sudo rm -rf \"$INSTALL_DIR\""
            else
                echo "  Aborting. Remove manually with: rm -rf $INSTALL_DIR"
                exit 1
            fi
        fi
    fi
fi

# ── Clone repository ──────────────────────────────
refuse_legacy_dreamserver_install

log "Cloning ODS..."
if [[ -n "$ODS_REF" ]]; then
    log "Using repository ref: $ODS_REF"
fi

if [[ "$REPO_URL" == file://* ]]; then
    _repo_path="${REPO_URL#file://}"
    git config --global --add safe.directory "$_repo_path" 2>/dev/null || true
    git config --global --add safe.directory "$_repo_path/.git" 2>/dev/null || true
fi

# Clone just the ods subdirectory using sparse checkout
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

clone_args=(--depth 1 --filter=blob:none --sparse)
if [[ -n "$ODS_REF" ]]; then
    clone_args+=(--branch "$ODS_REF")
fi

_clone_err=$(git clone "${clone_args[@]}" "$REPO_URL" "$TEMP_DIR/repo" 2>&1) || {
    case "$_clone_err" in
        *"Unable to read current working directory"*|*"getcwd"*)
            error "git could not read the current working directory. This usually means the directory you launched from has been deleted (e.g. you uninstalled ODS and re-ran the bootstrap from the same shell). Run \`cd ~\` and re-run the bootstrap." ;;
        *"Could not resolve host"*|*"Failed to connect"*|*"Connection refused"*|*"Network is unreachable"*)
            error "Failed to reach github.com. Check your internet connection or proxy settings.\n  git said: $_clone_err" ;;
        *"Permission denied"*|*"could not create"*)
            error "git failed to write to $TEMP_DIR (permissions). Check that /tmp is writable.\n  git said: $_clone_err" ;;
        *)
            error "Failed to clone repository.\n  git said: $_clone_err" ;;
    esac
}
echo "$_clone_err" | tail -1

cd "$TEMP_DIR/repo"
git sparse-checkout set ods 2>/dev/null || {
    # Fallback: full clone if sparse checkout fails
    cd "$ODS_BOOTSTRAP_ROOT"
    rm -rf "$TEMP_DIR/repo"
    fallback_clone_args=(--depth 1)
    if [[ -n "$ODS_REF" ]]; then
        fallback_clone_args+=(--branch "$ODS_REF")
    fi
    git clone "${fallback_clone_args[@]}" "$REPO_URL" "$TEMP_DIR/repo" 2>&1 | tail -1 || \
        error "Failed to clone repository (fallback full clone also failed)."
    cd "$TEMP_DIR/repo"
}

# Move ods to install location (exclude dev-only files)
if [[ -d "$TEMP_DIR/repo/ods" ]]; then
    # Use rsync to exclude development files not needed at runtime
    if command -v rsync >/dev/null 2>&1; then
        rsync -a \
            --exclude='tests/' \
            --exclude='docs/' \
            --exclude='examples/' \
            --exclude='.github/' \
            --exclude='*.md' \
            --exclude='.shellcheckrc' \
            --exclude='PSScriptAnalyzerSettings.psd1' \
            --exclude='test-stack.sh' \
            --exclude='.gitignore' \
            --exclude='__pycache__/' \
            --exclude='*.pyc' \
            --exclude='.pytest_cache/' \
            --exclude='node_modules/' \
            --include='LICENSE' \
            "$TEMP_DIR/repo/ods/" "$INSTALL_DIR/"
    else
        # Fallback to cp if rsync not available
        cp -r "$TEMP_DIR/repo/ods" "$INSTALL_DIR"
        # Remove dev-only files after copy
        rm -rf "$INSTALL_DIR/tests" "$INSTALL_DIR/docs" "$INSTALL_DIR/examples" "$INSTALL_DIR/.github" 2>/dev/null || true
        rm -f "$INSTALL_DIR"/*.md "$INSTALL_DIR/.shellcheckrc" "$INSTALL_DIR/PSScriptAnalyzerSettings.psd1" "$INSTALL_DIR/test-stack.sh" "$INSTALL_DIR/.gitignore" 2>/dev/null || true
        # Keep LICENSE file
        [[ -f "$TEMP_DIR/repo/ods/LICENSE" ]] && cp "$TEMP_DIR/repo/ods/LICENSE" "$INSTALL_DIR/" 2>/dev/null || true
    fi
else
    error "ods directory not found in repository."
fi

success "Cloned to $INSTALL_DIR"

# ── Bundle extensions-library templates ──────────────
# The dashboard's Extensions page reads from data/extensions-library/. The
# source library now ships inside ods/extensions/library/, which the
# rsync above copies as part of the product tree. Without it, dashboard-api returns:
#   503 {"detail":"Extensions library is unavailable"}
# on every install. Bundle the templates inside the install dir so the
# installer can find them deterministically regardless of where it's invoked.
if [[ -d "$TEMP_DIR/repo/ods/extensions/library" ]]; then
    if rm -rf "$INSTALL_DIR/extensions-library-bundle" \
        && mkdir -p "$INSTALL_DIR/extensions-library-bundle" \
        && cp -R "$TEMP_DIR/repo/ods/extensions/library/." "$INSTALL_DIR/extensions-library-bundle/"; then
        success "Bundled extensions-library templates"
    else
        warn "Failed to bundle extensions-library — Extensions page may 503"
    fi
else
    warn "ods/extensions/library not in clone — Extensions page will 503"
fi

# ── Make scripts executable ──────────────────────────
chmod +x "$INSTALL_DIR/install.sh" 2>/dev/null || true
chmod +x "$INSTALL_DIR/ods-cli" 2>/dev/null || true
chmod +x "$INSTALL_DIR/scripts/"*.sh 2>/dev/null || true
# Note: tests/ directory excluded from installation

# ── Run installer ──────────────────────────────
echo ""
log "Launching ODS installer..."
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

cd "$INSTALL_DIR"
exec ./install.sh "$@"
