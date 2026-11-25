#!/bin/bash
# =============================================================================
# Test: phase 05 never installs a second NVIDIA toolkit under Docker Desktop
# =============================================================================
# Docker Desktop runs its engine in its own VM. Without an "nvidia" runtime
# from Docker Desktop, installing nvidia-container-toolkit in the distro and
# running `systemctl restart docker` cannot help: phase 05 must stop with the
# cause, the recovery and the Discord link instead. A native Docker Engine, a
# distro that already has the toolkit, and Docker Desktop with its NVIDIA
# runtime keep their current paths.
#
# Run: bash tests/test-phase05-docker-desktop-nvidia-refusal.sh
# =============================================================================

set -euo pipefail

ODS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE_DIR="$(mktemp -d -t ods-phase05-desktop-XXXXXX)"
trap 'rm -rf "$FIXTURE_DIR"' EXIT

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# Usage: run_phase05 <name> <platform> <os> <runtimes-json> <toolkit yes|no> <dry-run>
run_phase05() {
    local name="$1" platform="$2" os="$3" runtimes="$4" toolkit="$5" dry_run="$6"
    local bin="$FIXTURE_DIR/$name/bin"
    mkdir -p "$bin"
    cat > "$bin/docker" <<STUB
#!/bin/bash
case "\$*" in
    "--version"|"version") echo "Docker version 29.8.2" ;;
    "version --format "*) echo "29.8.2" ;;
    "compose version") echo "Docker Compose version v5.1.1" ;;
    *"Runtimes"*) echo '$runtimes' ;;
    *"OperatingSystem"*) echo '$os' ;;
    "info") echo "$os" ;;
    *) exit 0 ;;
esac
STUB
    chmod +x "$bin/docker"
    local status=0
    (
        export PATH="$bin:$PATH"
        export SCRIPT_DIR="$ODS_ROOT"
        export SKIP_DOCKER=true
        export DRY_RUN="$dry_run"
        export INTERACTIVE=false
        export GPU_COUNT=1
        export GPU_BACKEND=nvidia
        export CAP_PLATFORM_ID="$platform"
        export LOG_FILE="$FIXTURE_DIR/$name/install.log"
        export MIN_DRIVER_VERSION=570
        export PKG_MANAGER=apt
        export DOCKER_CMD=""
        export DOCKER_COMPOSE_CMD=""
        export ODS_HELP_DISCORD_URL="https://discord.gg/4ntNp9MAwC"

        ods_progress() { :; }
        show_phase() { :; }
        ai() { printf 'AI: %s\n' "$*"; }
        ai_ok() { printf 'OK: %s\n' "$*"; }
        ai_warn() { printf 'WARN: %s\n' "$*"; }
        log() { printf 'LOG: %s\n' "$*"; }
        warn() { printf 'WARN: %s\n' "$*"; }
        # installers/lib/logging.sh error() exits; so does this one.
        error() { printf 'ERROR: %s\n' "$*"; exit 1; }
        detect_pkg_manager() { PKG_MANAGER=apt; }
        pkg_install() { printf 'UNEXPECTED package install: %s\n' "$*"; return 1; }
        pkg_update() { printf 'UNEXPECTED package update\n'; return 1; }
        pkg_resolve() { printf '%s\n' "$1"; }
        ods_sudo_available() { return 1; }
        ods_sudo() { printf 'UNEXPECTED sudo: %s\n' "$*"; return 1; }
        systemctl() { printf 'UNEXPECTED systemctl: %s\n' "$*"; return 1; }
        nvidia-ctk() { printf 'UNEXPECTED nvidia-ctk: %s\n' "$*"; return 1; }

        # The distro's own toolkit binary, independent of the test host.
        command() {
            if [[ "${1:-}" == "-v" && ( "${2:-}" == "nvidia-container-cli" || "${2:-}" == "nvidia-ctk" ) ]]; then
                if [[ "$toolkit" == yes && "${2:-}" == "nvidia-container-cli" ]]; then
                    printf '%s\n' /usr/bin/nvidia-container-cli
                    return 0
                fi
                return 1
            fi
            builtin command "$@"
        }

        # shellcheck disable=SC1091
        source "$ODS_ROOT/installers/phases/05-docker.sh"
    ) > "$FIXTURE_DIR/$name/output.txt" 2>&1 || status=$?
    printf '%s\n' "$status" > "$FIXTURE_DIR/$name/status"
}

output_of() { cat "$FIXTURE_DIR/$1/output.txt"; }
status_of() { cat "$FIXTURE_DIR/$1/status"; }

assert_refused() {
    local name="$1" label="$2" recovery="$3"
    local out
    out="$(output_of "$name")"
    if [[ "$(status_of "$name")" != 0 ]]; then pass "$label: setup stops"; else fail "$label: setup continued"; fi
    if grep -qF 'ERROR: ' <<< "$out" && grep -qF 'ODS never installs' <<< "$out"; then
        pass "$label: says why"
    else
        fail "$label: no refusal cause"; echo "$out"
    fi
    if grep -qF "$recovery" <<< "$out" && grep -qF 'GPU_BACKEND=cpu' <<< "$out"; then
        pass "$label: gives the recovery and the CPU alternative"
    else
        fail "$label: recovery missing"; echo "$out"
    fi
    if grep -qF 'Need help? Ask on the ODS Discord: https://discord.gg/4ntNp9MAwC' <<< "$out"; then
        pass "$label: links the ODS Discord"
    else
        fail "$label: no Discord link"; echo "$out"
    fi
    if grep -qE 'Installing NVIDIA Container Toolkit|UNEXPECTED|Would install' <<< "$out"; then
        fail "$label: tried to install a toolkit or restart Docker"; echo "$out"
    else
        pass "$label: installs nothing and restarts nothing"
    fi
}

echo "=== Docker Desktop without an NVIDIA runtime refuses ==="
run_phase05 wsl-arm wsl 'Docker Desktop (containerized)' '{"io.containerd.runc.v2":{},"runc":{}}' no false
assert_refused wsl-arm "WSL, Docker Desktop on Windows on Arm" 'Use the WSL 2 based engine'
run_phase05 wsl-x64 wsl 'Docker Desktop' '{"runc":{}}' no false
assert_refused wsl-x64 "WSL, Docker Desktop" 'restart Docker Desktop, then rerun the ODS installer'
run_phase05 linux-desktop linux 'Docker Desktop' '{"runc":{}}' no false
assert_refused linux-desktop "Linux, Docker Desktop" 'docker context use default'
# A dry run reports the same stop instead of "Would install".
run_phase05 wsl-dry wsl 'Docker Desktop' '{"runc":{}}' no true
assert_refused wsl-dry "WSL, Docker Desktop, dry run" 'Use the WSL 2 based engine'

echo "=== Every other host keeps its path ==="
run_phase05 wsl-runtime wsl 'Docker Desktop (containerized)' '{"io.containerd.runc.v2":{},"nvidia":{},"runc":{}}' no false
if [[ "$(status_of wsl-runtime)" == 0 ]] && grep -qF 'OK: Docker Desktop NVIDIA runtime available' <<< "$(output_of wsl-runtime)"; then
    pass "Docker Desktop with its NVIDIA runtime is accepted"
else
    fail "Docker Desktop with its NVIDIA runtime was not accepted"; output_of wsl-runtime
fi

run_phase05 desktop-toolkit wsl 'Docker Desktop' '{"runc":{}}' yes false
if [[ "$(status_of desktop-toolkit)" == 0 ]] && grep -qF 'OK: NVIDIA Container Toolkit installed' <<< "$(output_of desktop-toolkit)" \
    && ! grep -qF 'ERROR: ' <<< "$(output_of desktop-toolkit)"; then
    pass "a distro that already has the toolkit continues as before"
else
    fail "a distro with the toolkit changed behaviour"; output_of desktop-toolkit
fi

run_phase05 native-engine linux 'Ubuntu 24.04.3 LTS' '{"runc":{}}' no true
if [[ "$(status_of native-engine)" == 0 ]] && grep -qF 'Installing NVIDIA Container Toolkit' <<< "$(output_of native-engine)" \
    && grep -qF '[DRY RUN] Would install NVIDIA Container Toolkit' <<< "$(output_of native-engine)" \
    && ! grep -qF 'ERROR: ' <<< "$(output_of native-engine)"; then
    pass "a native Docker Engine still gets the toolkit"
else
    fail "the native Docker Engine path changed"; output_of native-engine
fi

run_phase05 no-engine linux '' '' no true
if [[ "$(status_of no-engine)" == 0 ]] && grep -qF '[DRY RUN] Would install NVIDIA Container Toolkit' <<< "$(output_of no-engine)"; then
    pass "an engine that does not answer is not treated as Docker Desktop"
else
    fail "an unanswering engine was refused"; output_of no-engine
fi

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
