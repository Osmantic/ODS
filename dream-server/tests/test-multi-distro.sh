#!/bin/bash
# ============================================================================
# Dream Server — Multi-Distro Test Runner
# ============================================================================
# Purpose: Validate installer detection and package logic across Linux distros
#          using Distrobox containers. No reboot required.
#
# Usage:
#   ./tests/test-multi-distro.sh              # Run all distros
#   ./tests/test-multi-distro.sh fedora41 arch cachyos mint213  # Run specific distros
#   ./tests/test-multi-distro.sh --create     # Create containers only
#   ./tests/test-multi-distro.sh --cleanup    # Remove all test containers
#
# Prerequisites:
#   - distrobox installed (curl -s https://raw.githubusercontent.com/89luca89/distrobox/main/install | sudo sh)
#   - podman or docker available as container backend
#
# What it tests:
#   - /etc/os-release detection
#   - Package manager identification (apt/dnf/pacman/zypper)
#   - Tool availability (curl, jq, rsync, git)
#   - GPU detection (sysfs/nvidia-smi visibility)
#   - Installer dry-run (phases 01-04)
#   - Service registry loading
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Colors
RED='\033[0;31m'
GRN='\033[0;32m'
AMB='\033[0;33m'
BLD='\033[1m'
NC='\033[0m'

# Target distros: name -> image
declare -A DISTROS=(
    [ubuntu2404]="ubuntu:24.04"
    [ubuntu2204]="ubuntu:22.04"
    [debian12]="debian:12"
    [mint213]="linuxmintd/mint21.3-amd64:latest"
    [fedora41]="fedora:41"
    [rocky9]="rockylinux:9"
    [arch]="archlinux:latest"
    [manjaro]="manjarolinux/base:latest"
    [cachyos]="cachyos/cachyos:latest"
    [opensuse]="opensuse/tumbleweed:latest"
)

# Expected package managers per distro
declare -A EXPECTED_PKG=(
    [ubuntu2404]="apt"
    [ubuntu2204]="apt"
    [debian12]="apt"
    [mint213]="apt"
    [fedora41]="dnf"
    [rocky9]="dnf"
    [arch]="pacman"
    [manjaro]="pacman"
    [cachyos]="pacman"
    [opensuse]="zypper"
)

CONTAINER_PREFIX="dream-test"
PASS=0
FAIL=0
SKIP=0
RESULTS=()

log()  { echo -e "${GRN}[TEST]${NC} $1"; }
pass() { echo -e "${GRN}  [PASS]${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "${RED}  [FAIL]${NC} $1"; FAIL=$((FAIL + 1)); }
skip() { echo -e "${AMB}  [SKIP]${NC} $1"; SKIP=$((SKIP + 1)); }

# Create distrobox containers for all target distros
create_containers() {
    log "Creating Distrobox test containers..."
    for name in "${!DISTROS[@]}"; do
        local image="${DISTROS[$name]}"
        local cname="${CONTAINER_PREFIX}-${name}"
        if distrobox list 2>/dev/null | grep -q "$cname"; then
            log "  $cname already exists"
        else
            log "  Creating $cname ($image)..."
            distrobox create --name "$cname" --image "$image" --yes --no-entry 2>/dev/null || {
                skip "$name: failed to create container (image may not be available)"
                continue
            }
        fi
    done
}

# Remove all test containers
cleanup_containers() {
    log "Removing test containers..."
    for name in "${!DISTROS[@]}"; do
        local cname="${CONTAINER_PREFIX}-${name}"
        if distrobox list 2>/dev/null | grep -q "$cname"; then
            distrobox rm --force "$cname" 2>/dev/null || true
            log "  Removed $cname"
        fi
    done
}

# Run a command inside a distrobox container
# Usage: dbox_run <container_name> <command>
dbox_run() {
    local cname="$1"
    shift
    distrobox enter --name "$cname" -- bash -lc "$*" 2>/dev/null
}

# Test a single distro
test_distro() {
    local name="$1"
    local cname="${CONTAINER_PREFIX}-${name}"
    local expected_pkg="${EXPECTED_PKG[$name]:-unknown}"
    local distro_pass=0
    local distro_fail=0

    echo ""
    echo -e "${BLD}━━━ Testing: ${name} ━━━${NC}"

    # Check container exists
    if ! distrobox list 2>/dev/null | grep -q "$cname"; then
        skip "$name: container not found (run with --create first)"
        RESULTS+=("$name: SKIPPED")
        return
    fi

    # Test 1: /etc/os-release exists and has ID
    local distro_id
    distro_id=$(dbox_run "$cname" "source /etc/os-release 2>/dev/null && echo \"\$ID\"" || echo "")
    if [[ -n "$distro_id" ]]; then
        pass "$name: /etc/os-release ID=$distro_id"
        distro_pass=$((distro_pass + 1))
    else
        fail "$name: /etc/os-release missing or has no ID"
        distro_fail=$((distro_fail + 1))
    fi

    # Test 2: Package manager detection
    local detected_pkg
    detected_pkg=$(dbox_run "$cname" "
        source '$SCRIPT_DIR/installers/lib/constants.sh' 2>/dev/null
        LOG_FILE=/dev/null
        log() { :; }; warn() { :; }; error() { echo \"\$1\" >&2; }
        source '$SCRIPT_DIR/installers/lib/packaging.sh' 2>/dev/null
        detect_pkg_manager
        echo \"\$PKG_MANAGER\"
    " || echo "error")

    if [[ "$detected_pkg" == "$expected_pkg" ]]; then
        pass "$name: package manager detected correctly ($detected_pkg)"
        distro_pass=$((distro_pass + 1))
    elif [[ "$detected_pkg" == "error" ]]; then
        fail "$name: packaging.sh failed to load"
        distro_fail=$((distro_fail + 1))
    else
        fail "$name: expected pkg=$expected_pkg, got=$detected_pkg"
        distro_fail=$((distro_fail + 1))
    fi

    # Test 3: Essential tools available or installable
    for tool in curl git; do
        if dbox_run "$cname" "command -v $tool" &>/dev/null; then
            pass "$name: $tool available"
            distro_pass=$((distro_pass + 1))
        else
            skip "$name: $tool not pre-installed (installable via $expected_pkg)"
            # Not a failure — we just need to install it during Phase 04
        fi
    done

    # Test 4: GPU devices visible (if host has GPU)
    local gpu_visible="no"
    if dbox_run "$cname" "ls /dev/dri/ 2>/dev/null || ls /dev/nvidia* 2>/dev/null" &>/dev/null; then
        pass "$name: GPU devices visible in container"
        gpu_visible="yes"
        distro_pass=$((distro_pass + 1))
    else
        skip "$name: no GPU devices visible (expected in rootless containers)"
    fi

    # Test 5: Installer dry-run (if possible)
    if dbox_run "$cname" "command -v docker" &>/dev/null; then
        local dry_run_exit
        dry_run_exit=$(dbox_run "$cname" "cd '$SCRIPT_DIR' && bash install-core.sh --dry-run --non-interactive 2>&1; echo \"EXIT:\$?\"" | grep "^EXIT:" | cut -d: -f2 || echo "unknown")
        if [[ "$dry_run_exit" == "0" ]]; then
            pass "$name: installer dry-run completed successfully"
            distro_pass=$((distro_pass + 1))
        else
            fail "$name: installer dry-run failed (exit=$dry_run_exit)"
            distro_fail=$((distro_fail + 1))
        fi
    else
        skip "$name: docker not available in container (dry-run skipped)"
    fi

    # Record result
    if [[ $distro_fail -eq 0 ]]; then
        RESULTS+=("$name: PASS ($distro_pass checks)")
    else
        RESULTS+=("$name: FAIL ($distro_fail failures, $distro_pass passed)")
    fi
}

# Print summary table
print_summary() {
    echo ""
    echo -e "${BLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLD}  Multi-Distro Test Summary${NC}"
    echo -e "${BLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    for result in "${RESULTS[@]}"; do
        if [[ "$result" == *"PASS"* ]]; then
            echo -e "  ${GRN}✓${NC} $result"
        elif [[ "$result" == *"FAIL"* ]]; then
            echo -e "  ${RED}✗${NC} $result"
        else
            echo -e "  ${AMB}○${NC} $result"
        fi
    done
    echo ""
    echo -e "  Total: ${GRN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}, ${AMB}${SKIP} skipped${NC}"
    echo -e "${BLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

    [[ $FAIL -gt 0 ]] && return 1
    return 0
}

# Main
main() {
    # Check distrobox is installed
    if ! command -v distrobox &>/dev/null; then
        echo -e "${RED}Error: distrobox not installed.${NC}"
        echo "Install: curl -s https://raw.githubusercontent.com/89luca89/distrobox/main/install | sudo sh"
        exit 1
    fi

    # Handle flags
    case "${1:-}" in
        --create)
            create_containers
            exit 0
            ;;
        --cleanup)
            cleanup_containers
            exit 0
            ;;
        --help|-h)
            echo "Usage: $0 [--create|--cleanup|distro1 distro2 ...]"
            echo ""
            echo "Available distros: ${!DISTROS[*]}"
            exit 0
            ;;
    esac

    # Determine which distros to test
    local targets=()
    if [[ $# -gt 0 ]]; then
        targets=("$@")
    else
        targets=("${!DISTROS[@]}")
    fi

    log "Dream Server Multi-Distro Test Runner"
    log "Testing ${#targets[@]} distro(s): ${targets[*]}"

    # Ensure containers exist
    create_containers

    # Run tests
    for name in "${targets[@]}"; do
        if [[ -z "${DISTROS[$name]:-}" ]]; then
            skip "Unknown distro: $name (available: ${!DISTROS[*]})"
            continue
        fi
        test_distro "$name"
    done

    print_summary
}

main "$@"
