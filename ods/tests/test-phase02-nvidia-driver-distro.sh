#!/usr/bin/env bash
# Phase 02 must not use Debian package tools or remediation instructions on
# supported non-Debian Linux distributions when an NVIDIA driver is too old.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT_DIR/installers/phases/02-detection.sh"

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

run_phase() {
    local pkg_manager="$1" distro="$2" blackwell="$3" out rc
    set +e
    out="$(NVIDIA_TEST_BLACKWELL="$blackwell" NVIDIA_TEST_PKG_MANAGER="$pkg_manager" NVIDIA_TEST_DISTRO_ID="$distro" bash -c '
SCRIPT_DIR="$1"
INSTALL_DIR="$1"
LOG_FILE=/dev/null
INTERACTIVE=false
DRY_RUN=false
TIER=1
GPU_BACKEND=nvidia
GPU_COUNT=1
GPU_VRAM=8192
GPU_NAME="Mock Fedora GPU"
GPU_MEMORY_TYPE=discrete
MIN_DRIVER_VERSION=570
PKG_MANAGER="$NVIDIA_TEST_PKG_MANAGER"
DISTRO_ID="$NVIDIA_TEST_DISTRO_ID"
ODS_MODE=local
ods_progress() { :; }
chapter() { :; }
ai() { printf "INFO %s\n" "$*"; }
ai_bad() { printf "ERROR %s\n" "$*"; }
ai_ok() { printf "OK %s\n" "$*"; }
ai_warn() { printf "WARN %s\n" "$*"; }
log() { :; }
warn() { :; }
success() { :; }
load_capability_profile() { return 0; }
detect_gpu() { return 0; }
load_backend_contract() { return 0; }
detect_host_arch() { echo x86_64; }
fix_nvidia_secure_boot() { return 0; }
validate_nvidia_blackwell_open_modules() { :; }
nvidia_blackwell_hardware_detected() { [[ "$NVIDIA_TEST_BLACKWELL" == true ]]; }
amd_gpu_runtime_devices_available() { return 1; }
amd_gpu_missing_devices_csv() { echo ""; }
show_amd_gpu_device_guidance() { :; }
apply_cpu_gpu_fallback() { :; }
resolve_tier_config() { :; }
resolve_compose_config() { :; }
show_hardware_summary() { :; }
show_tier_recommendation() { :; }
normalize_profile_tier() { :; }
tier_rank() { echo 1; }
command_not_found_handle() {
    if [[ "$1" == nvidia-smi ]]; then printf "535.154.05\n"; return 0; fi
    printf "unexpected command: %s\n" "$1" >&2
    return 127
}
ods_sudo_available() { return 0; }
ods_sudo() {
    if [[ "$PKG_MANAGER" == apt && "$1" == apt-get ]]; then
        printf "PRIVILEGED apt-get install -y nvidia-driver-570\n"
        return 0
    fi
    printf "unexpected privileged command: %s\n" "$*"
    exit 98
}
dpkg() { printf "ii  nvidia-driver-570 570.0 installed\n"; }
error() { printf "INSTALLER ABORT: %s\n" "$*"; exit 42; }
source "$SCRIPT_DIR/installers/phases/02-detection.sh"
' _ "$ROOT_DIR" 2>&1)"
    rc=$?
    set -e
    [[ "$rc" -eq 42 ]] || fail "phase must stop with its compatible-driver error (exit 42); got $rc: $out"
    if [[ "$pkg_manager" == apt ]]; then
        grep -Fq "PRIVILEGED apt-get install -y nvidia-driver-570" <<<"$out" \
            || fail "Debian-family repair must keep its automatic apt upgrade; got: $out"
        grep -Fq "Reboot required to load NVIDIA driver 570" <<<"$out" \
            || fail "successful apt upgrade must still require reboot; got: $out"
        return
    fi
    if [[ "$blackwell" == true ]]; then
        grep -Fq "Install an NVIDIA open kernel module driver >= 570 from your distribution's configured driver source." <<<"$out" \
            || fail "Blackwell recovery must name the required distro-neutral open driver; got: $out"
    else
        grep -Fq "Automatic NVIDIA driver repair is not supported for fedora (dnf package manager)." <<<"$out" \
            || fail "phase must identify unsupported automatic repair on Fedora; got: $out"
        grep -Fq "Upgrade the NVIDIA driver to >= 570 using your distribution's configured driver source, reboot, then re-run ODS." <<<"$out" \
            || fail "phase must give distro-neutral manual recovery steps; got: $out"
    fi
    ! grep -Eq 'apt(-get)?|dpkg' <<<"$out" \
        || fail "Fedora recovery must not mention Debian package tools; got: $out"
    ! grep -Fq "Attempting to install a compatible driver" <<<"$out" \
        || fail "Fedora path must not claim it attempted an automatic driver install; got: $out"
}

run_phase dnf fedora false
pass "Fedora old-driver path gives distro-appropriate recovery without Debian commands"
run_phase dnf fedora true
pass "Fedora Blackwell path avoids Debian-only NVIDIA package instructions"
run_phase apt ubuntu false
pass "Debian-family driver repair keeps automatic package upgrade and reboot gate"
