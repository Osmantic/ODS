#!/bin/bash
# Minimal mobile dispatch target. The Android and iOS preview installers land in
# follow-up PRs so this infrastructure change stays reviewable on its own.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$SCRIPT_DIR/installers/common.sh"

platform="$(detect_platform)"

case "$platform" in
    android-termux|ios-ashell)
        echo "[INFO] ODS detected mobile platform: $platform" >&2
        echo "[ERROR] Mobile preview installer is split into a follow-up PR." >&2
        echo "        This PR only adds the dispatcher and platform detection contract." >&2
        exit 1
        ;;
    *)
        echo "[ERROR] install-mobile.sh only handles Android Termux or iOS a-Shell." >&2
        exit 1
        ;;
esac
