#!/bin/bash
# shellcheck disable=SC2317
# A Windows Docker Desktop shim can be present on WSL PATH even when integration
# is disabled. It must not suppress installation of a usable native engine.

set -euo pipefail

ODS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE_DIR="$(mktemp -d -t ods-wsl-docker-shim-XXXXXX)"
trap 'rm -rf "$FIXTURE_DIR"' EXIT
mkdir -p "$FIXTURE_DIR/bin"
export DOCKER_INSTALLED_MARKER="$FIXTURE_DIR/native-docker-installed"
export DOCKER_COMPOSE_SHIM_MARKER="$FIXTURE_DIR/docker-compose-shim-works"
export PATH="$FIXTURE_DIR/bin:$PATH"

cat > "$FIXTURE_DIR/bin/docker" <<'STUB'
#!/bin/bash
if [[ ! -f "${DOCKER_INSTALLED_MARKER:?}" ]]; then
    echo "The command 'docker' could not be found in this WSL 2 distro." >&2
    exit 1
fi
case "$*" in
    "--version") echo "Docker version 29.2.1" ;;
    "version --format "*) echo "29.2.1" ;;
    "compose version") echo "Docker Compose version v2.40.0" ;;
    "info --format "*) echo '{}' ;;
    "info"|"version") echo "Docker Engine 29.2.1" ;;
    *) exit 0 ;;
esac
STUB
chmod +x "$FIXTURE_DIR/bin/docker"

cat > "$FIXTURE_DIR/bin/curl" <<'STUB'
#!/bin/bash
output=""
while (($#)); do
    if [[ "$1" == "-o" ]]; then
        output="$2"
        shift 2
    else
        shift
    fi
done
[[ -n "$output" ]] || exit 2
cat > "$output" <<'INSTALLER'
#!/bin/sh
touch "$DOCKER_INSTALLED_MARKER"
INSTALLER
STUB
chmod +x "$FIXTURE_DIR/bin/curl"

cat > "$FIXTURE_DIR/bin/docker-compose" <<'STUB'
#!/bin/bash
if [[ -f "${DOCKER_COMPOSE_SHIM_MARKER:?}" && "$*" == "--version" ]]; then
    echo "docker-compose version 1.29.2"
    exit 0
fi
echo "docker-compose is present but unavailable" >&2
exit 1
STUB
chmod +x "$FIXTURE_DIR/bin/docker-compose"

OUTPUT_FILE="$FIXTURE_DIR/output.txt"
set +e
(
    export SCRIPT_DIR="$ODS_ROOT"
    export SKIP_DOCKER=false
    export DRY_RUN=false
    export INTERACTIVE=false
    export GPU_COUNT=0
    export GPU_BACKEND=none
    export CAP_PLATFORM_ID=wsl
    export LOG_FILE="$FIXTURE_DIR/install.log"
    export MIN_DRIVER_VERSION=570
    export PKG_MANAGER=apt
    export DOCKER_CMD=""
    export DOCKER_COMPOSE_CMD=""
    export USER=odsbeta

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
    ods_sudo_available() { return 0; }
    ods_sudo() { return 0; }
    id() {
        if [[ "${1:-}" == "-nG" ]]; then
            echo docker
            return 0
        fi
        command id "$@"
    }

    # shellcheck disable=SC1091
    source "$ODS_ROOT/installers/phases/05-docker.sh"
) > "$OUTPUT_FILE" 2>&1
source_rc=$?
set -e

if (( source_rc != 0 )); then
    echo "FAIL: Docker phase exited with rc=$source_rc"
    cat "$OUTPUT_FILE"
    exit 1
fi

test -f "$DOCKER_INSTALLED_MARKER" || {
    echo "FAIL: broken WSL docker shim suppressed native Docker installation"
    cat "$OUTPUT_FILE"
    exit 1
}
if grep -qF 'Docker already installed' "$OUTPUT_FILE"; then
    echo "FAIL: unusable WSL docker shim was reported as an installed engine"
    cat "$OUTPUT_FILE"
    exit 1
fi
grep -qF 'AI: Installing Docker...' "$OUTPUT_FILE" || {
    echo "FAIL: installer did not enter the Docker installation path"
    cat "$OUTPUT_FILE"
    exit 1
}

docker_checks=$(grep -cF 'command -v docker &> /dev/null && docker --version &> /dev/null' "$ODS_ROOT/get-ods.sh" || true)
[[ "$docker_checks" == "2" ]] || {
    echo "FAIL: bootstrap must validate both Docker checks behaviorally"
    exit 1
}

# The v1 fallback must also reject a command shim that exists but cannot run.
eval "$(sed -n '/^_docker_compose_detect_cmd() {/,/^}/p' "$ODS_ROOT/installers/phases/05-docker.sh")"
docker_compose_run() { return 1; }
rm -f "$DOCKER_COMPOSE_SHIM_MARKER"
detected="$(_docker_compose_detect_cmd || true)"
[[ -z "$detected" ]] || {
    echo "FAIL: unusable docker-compose shim was accepted as the v1 fallback"
    exit 1
}
touch "$DOCKER_COMPOSE_SHIM_MARKER"
detected="$(_docker_compose_detect_cmd || true)"
[[ "$detected" == "docker-compose" ]] || {
    echo "FAIL: usable docker-compose v1 fallback was not detected"
    exit 1
}

echo "PASS: unusable WSL Docker Desktop shim triggers native Docker installation"
