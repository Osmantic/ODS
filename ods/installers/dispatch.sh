#!/bin/bash
# Platform installer dispatch.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$SCRIPT_DIR/installers/common.sh"

resolve_installer_target() {
    local platform
    platform="$(detect_platform)"

    case "$platform" in
        android-termux|ios-ashell)
            echo "$SCRIPT_DIR/installers/mobile/install-mobile.sh"
            ;;
        linux|wsl)
            echo "$SCRIPT_DIR/install-core.sh"
            ;;
        windows)
            echo "$SCRIPT_DIR/installers/windows/install-windows.ps1"
            ;;
        macos)
            echo "$SCRIPT_DIR/installers/macos/install-macos.sh"
            ;;
        *)
            echo "unsupported:unknown"
            ;;
    esac
}
