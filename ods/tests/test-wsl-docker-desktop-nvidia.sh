#!/bin/bash
# =============================================================================
# Test: WSL2 must reuse Docker Desktop's advertised NVIDIA runtime
# =============================================================================

set -euo pipefail

ODS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE_DIR="$(mktemp -d -t ods-wsl-docker-nvidia-XXXXXX)"
trap 'rm -rf "$FIXTURE_DIR"' EXIT
mkdir -p "$FIXTURE_DIR/bin"

cat > "$FIXTURE_DIR/bin/docker" <<'STUB'
#!/bin/bash
case "$*" in
    "--version")
        echo "Docker version 29.3.1"
        ;;
    "version --format "*)
        echo "29.3.1"
        ;;
    "compose version")
        echo "Docker Compose version v5.1.1"
        ;;
    "info --format "*)
        echo '{"runc":{},"nvidia":{}}'
        ;;
    "info")
        echo "Docker Desktop"
        ;;
    "version")
        echo "Docker version 29.3.1"
        ;;
    *)
        exit 0
        ;;
esac
STUB
chmod +x "$FIXTURE_DIR/bin/docker"

OUTPUT_FILE="$FIXTURE_DIR/output.txt"

(
    export PATH="$FIXTURE_DIR/bin:$PATH"
    export SCRIPT_DIR="$ODS_ROOT"
    export SKIP_DOCKER=true
    export DRY_RUN=false
    export INTERACTIVE=false
    export GPU_COUNT=1
    export GPU_BACKEND=nvidia
    export CAP_PLATFORM_ID=wsl
    export LOG_FILE="$FIXTURE_DIR/install.log"
    export MIN_DRIVER_VERSION=570
    export PKG_MANAGER=apt
    export DOCKER_CMD=""
    export DOCKER_COMPOSE_CMD=""

    ods_progress() { :; }
    show_phase() { :; }
    ai() { printf 'AI: %s\n' "$*"; }
    ai_ok() { printf 'OK: %s\n' "$*"; }
    ai_warn() { printf 'WARN: %s\n' "$*"; }
    log() { printf 'LOG: %s\n' "$*"; }
    warn() { printf 'WARN: %s\n' "$*"; }
    error() { printf 'ERROR: %s\n' "$*"; return 1; }
    detect_pkg_manager() { PKG_MANAGER=apt; }
    pkg_install() { printf 'UNEXPECTED package install: %s\n' "$*"; return 1; }
    pkg_update() { printf 'UNEXPECTED package update\n'; return 1; }
    pkg_resolve() { printf '%s\n' "$1"; }
    ods_sudo_available() { return 1; }
    ods_sudo() { printf 'UNEXPECTED sudo: %s\n' "$*"; return 1; }

    # Model the fresh WSL case where Docker Desktop has the runtime but the
    # distro itself has no toolkit binary, even if the test host does.
    command() {
        if [[ "${1:-}" == "-v" && "${2:-}" == "nvidia-container-cli" ]]; then
            return 1
        fi
        builtin command "$@"
    }

    # shellcheck disable=SC1091
    source "$ODS_ROOT/installers/phases/05-docker.sh"
) > "$OUTPUT_FILE" 2>&1

if ! grep -qF 'OK: Docker Desktop NVIDIA runtime available' "$OUTPUT_FILE"; then
    echo "FAIL: Docker Desktop runtime was not accepted"
    cat "$OUTPUT_FILE"
    exit 1
fi

if grep -qE 'Installing NVIDIA Container Toolkit|UNEXPECTED|ERROR:' "$OUTPUT_FILE"; then
    echo "FAIL: WSL path attempted distro-level toolkit mutation"
    cat "$OUTPUT_FILE"
    exit 1
fi

echo "PASS: WSL2 reused Docker Desktop NVIDIA runtime without package or daemon mutation"
