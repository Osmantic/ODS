#!/usr/bin/env bash
# Portal (Pixel) needs Linux Node.js 20+ and npm even when the optional
# developer CLIs are off. A fresh Ubuntu (including a new WSL distro) has
# neither, so phase 07 must provision Node.js for Pixel and stop with a clear
# reason when it cannot, instead of failing late in phase 11.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT/installers/phases/07-devtools.sh"
TEST_ROOT="$(mktemp -d)"
cleanup() { rm -rf -- "$TEST_ROOT"; }
trap cleanup EXIT

# A PATH that holds no node/npm but the few tools the phase calls here.
SANDBOX="$TEST_ROOT/bin"
mkdir -p "$SANDBOX"
for tool in bash tee readlink cp chmod; do
    ln -s "$(command -v "$tool")" "$SANDBOX/$tool"
done

run_phase() {
    (
        # shellcheck disable=SC2034  # read by the sourced phase
        SCRIPT_DIR="$ROOT"
        # shellcheck disable=SC2034  # read by the sourced phase
        LOG_FILE="$TEST_ROOT/install.log"
        PATH="$SANDBOX"
        ods_progress() { :; }
        log() { printf 'LOG %s\n' "$*"; }
        ai() { printf 'AI %s\n' "$*"; }
        ai_warn() { printf 'WARN %s\n' "$*"; }
        ai_bad() { printf 'BAD %s\n' "$*"; }
        # The first success message is the observation point; stop before the
        # host-agent and OpenCode steps that follow in the same phase.
        ai_ok() { printf 'OK %s\n' "$*"; exit 0; }
        ods_sudo_available() { [[ "${FAKE_SUDO:-yes}" == yes ]]; }
        # Simulate the package manager installing Linux Node.js 22 + npm.
        ods_sudo() {
            printf 'SUDO %s\n' "$*"
            cp "$ROOT/tests/fixtures/node-runtime/node22" "$SANDBOX/node"
            printf '#!/bin/sh\nexit 0\n' > "$SANDBOX/npm"
            chmod 0755 "$SANDBOX/node" "$SANDBOX/npm"
        }
        # shellcheck source=../installers/phases/07-devtools.sh
        source "$PHASE"
    )
}

# 1) Dry run: Pixel without developer CLIs still plans the Node.js install.
dry="$(DRY_RUN=true ENABLE_DEVTOOLS=false ENABLE_PIXEL_RUNTIME=true run_phase)"
[[ "$dry" == *'Would install Linux Node.js 22 for Portal (Pixel)'* ]]
[[ "$dry" == *'Developer CLIs disabled; existing binaries would be preserved'* ]]
printf '%s\n' 'PASS: dry run plans Node.js for Pixel without developer CLIs'

no_pixel="$(DRY_RUN=true ENABLE_DEVTOOLS=false ENABLE_PIXEL_RUNTIME=false run_phase)"
[[ "$no_pixel" != *'Would install Linux Node.js 22'* ]]
printf '%s\n' 'PASS: dry run plans no Node.js when Pixel and developer CLIs are off'

# 2) Real run: Pixel without developer CLIs installs Node.js via the package manager.
real="$(DRY_RUN=false ENABLE_DEVTOOLS=false ENABLE_PIXEL_RUNTIME=true PKG_MANAGER=pacman run_phase)"
[[ "$real" == *'SUDO pacman -S --noconfirm --needed nodejs npm'* ]]
[[ "$real" == *'OK Linux Node.js 22 ready for Portal (Pixel)'* ]]
printf '%s\n' 'PASS: Pixel provisions Linux Node.js without developer CLIs'
rm -f -- "$SANDBOX/node" "$SANDBOX/npm"

# 3) Real run without usable sudo: stop in phase 07 with a clear reason.
set +e
blocked="$(DRY_RUN=false ENABLE_DEVTOOLS=false ENABLE_PIXEL_RUNTIME=true PKG_MANAGER=apt FAKE_SUDO=no run_phase)"
rc=$?
set -e
[[ "$rc" -ne 0 ]]
[[ "$blocked" == *'BAD Portal (Pixel) requires Linux Node.js 20+ and npm'* ]]
[[ "$blocked" != *'SUDO '* ]]
printf '%s\n' 'PASS: Pixel stops early when Linux Node.js cannot be installed'

printf '%s\n' '[PASS] Portal (Pixel) provisions Linux Node.js independently of developer CLIs'
