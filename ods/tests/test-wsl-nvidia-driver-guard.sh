#!/bin/bash
# =============================================================================
# Test: an old NVIDIA driver on WSL2 is a Windows-side fix, never an apt install
# =============================================================================
# WSL receives libcuda and nvidia-smi from the Windows driver. Installing
# nvidia-driver-* inside the distro breaks passthrough, so phase 02 must stop
# with Windows instructions before reaching its native Linux upgrade path.

set -euo pipefail

ODS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ODS_ROOT/installers/phases/02-detection.sh"

run_guard() {
    (
        export SCRIPT_DIR="$ODS_ROOT"
        ai() { printf 'AI: %s\n' "$*"; }
        ai_bad() { printf 'BAD: %s\n' "$*"; }
        ai_ok() { :; }
        ai_warn() { :; }
        log() { :; }
        warn() { :; }
        error() { printf 'ERROR: %s\n' "$*"; exit 1; }
        ods_sudo() { printf 'UNEXPECTED sudo: %s\n' "$*"; return 1; }
        # shellcheck disable=SC1091
        source "$ODS_ROOT/installers/lib/detection.sh"
        ods_wsl_nvidia_driver_too_old 566
    ) 2>&1
}

set +e
output="$(run_guard)"
status=$?
set -e

if [[ $status -eq 0 ]]; then
    echo "FAIL: WSL driver guard did not stop the installer"
    echo "$output"
    exit 1
fi
for expected in 'ERROR: NVIDIA driver 566 on Windows is below 570.' 'wsl --shutdown' 'Do not install NVIDIA drivers inside WSL'; do
    if ! grep -qF "$expected" <<< "$output"; then
        echo "FAIL: missing guidance: $expected"
        echo "$output"
        exit 1
    fi
done
if grep -q 'UNEXPECTED' <<< "$output"; then
    echo "FAIL: guard attempted a privileged operation"
    echo "$output"
    exit 1
fi

# The guard must run before the native Linux driver upgrade (apt/ubuntu-drivers)
# and before the Blackwell advice, which also suggests an in-distro apt install.
guard_line="$(grep -n 'ods_wsl_nvidia_driver_too_old "\$DRIVER_VERSION"' "$PHASE" | head -1 | cut -d: -f1)"
blackwell_line="$(grep -n 'if nvidia_blackwell_hardware_detected; then' "$PHASE" | head -1 | cut -d: -f1)"
upgrade_line="$(grep -n 'ubuntu-drivers install' "$PHASE" | head -1 | cut -d: -f1)"
if [[ -z "$guard_line" || -z "$blackwell_line" || -z "$upgrade_line" ]] ||
   (( guard_line > blackwell_line || guard_line > upgrade_line )); then
    echo "FAIL: phase 02 does not stop WSL before the in-distro driver upgrade"
    exit 1
fi
if ! sed -n "$((guard_line - 1))p" "$PHASE" | grep -qF 'if ods_is_wsl_host; then'; then
    echo "FAIL: WSL driver guard is not scoped to WSL hosts"
    exit 1
fi

echo "[contract] missing WSL GPU never starts Linux driver or Secure Boot repair"
set +e
missing_gpu_output="$(ODS_WSL_HOST_OVERRIDE=true bash -c '
    set -euo pipefail
    SCRIPT_DIR="$1"
    LOG_FILE=/dev/null
    ai_warn() { printf "WARN: %s\n" "$*"; }
    lspci() { echo "NVIDIA Corporation Blackwell"; }
    ods_sudo_available() { echo "UNEXPECTED sudo check"; return 42; }
    ods_sudo() { echo "UNEXPECTED privileged action: $*"; return 42; }
    source "$SCRIPT_DIR/installers/lib/detection.sh"
    fix_nvidia_secure_boot
' bash "$ODS_ROOT" 2>&1)"
missing_gpu_status=$?
set -e
if [[ $missing_gpu_status -eq 0 ]] ||
   ! grep -qF 'check the Windows NVIDIA driver and WSL GPU access' <<< "$missing_gpu_output" ||
   ! grep -qF 'do not install a Linux NVIDIA driver in the distro' <<< "$missing_gpu_output" ||
   grep -qF 'UNEXPECTED' <<< "$missing_gpu_output"; then
    echo "FAIL: missing WSL GPU reached Linux driver repair or lacked Windows guidance"
    echo "$missing_gpu_output"
    exit 1
fi

# WSL commonly hides GPU PCI devices. Even with an empty lspci result, the
# installer must give conditional Windows-side guidance and avoid Linux repair.
set +e
no_pci_output="$(ODS_WSL_HOST_OVERRIDE=true bash -c '
    set -euo pipefail
    SCRIPT_DIR="$1"
    LOG_FILE=/dev/null
    ai_warn() { printf "WARN: %s\n" "$*"; }
    lspci() { :; }
    ods_sudo_available() { echo "UNEXPECTED sudo check"; return 42; }
    ods_sudo() { echo "UNEXPECTED privileged action: $*"; return 42; }
    source "$SCRIPT_DIR/installers/lib/detection.sh"
    fix_nvidia_secure_boot
' bash "$ODS_ROOT" 2>&1)"
no_pci_status=$?
set -e
if [[ $no_pci_status -eq 0 ]] ||
   ! grep -qF 'If you expected one, check the Windows NVIDIA driver and WSL GPU access' <<< "$no_pci_output" ||
   ! grep -qF 'do not install a Linux NVIDIA driver in the distro' <<< "$no_pci_output" ||
   grep -qF 'UNEXPECTED' <<< "$no_pci_output"; then
    echo "FAIL: WSL without PCI visibility lacked conditional Windows guidance"
    echo "$no_pci_output"
    exit 1
fi

echo "PASS: old WSL NVIDIA driver stops with Windows instructions and no in-distro install"
