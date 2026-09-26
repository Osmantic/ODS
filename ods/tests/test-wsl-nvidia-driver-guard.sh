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

echo "PASS: old WSL NVIDIA driver stops with Windows instructions and no in-distro install"
