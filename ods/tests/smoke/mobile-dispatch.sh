#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

echo "[smoke] mobile dispatch contract"

for file in installers/common.sh installers/dispatch.sh installers/mobile/install-mobile.sh tests/smoke/mobile-dispatch.sh; do
    bash -n "$file"
done

grep -q "android-termux|ios-ashell" installers/dispatch.sh

android_target="$(ODS_PLATFORM_OVERRIDE=android-termux bash -c 'source installers/dispatch.sh; resolve_installer_target')"
ios_target="$(ODS_PLATFORM_OVERRIDE=ios-ashell bash -c 'source installers/dispatch.sh; resolve_installer_target')"
linux_target="$(ODS_PLATFORM_OVERRIDE=linux bash -c 'source installers/dispatch.sh; resolve_installer_target')"

test "$android_target" = "$ROOT_DIR/installers/mobile/install-mobile.sh"
test "$ios_target" = "$ROOT_DIR/installers/mobile/install-mobile.sh"
test "$linux_target" = "$ROOT_DIR/install-core.sh"

ashell_detected="$(TERM_PROGRAM=a-Shell HOME=/private/var/mobile/Containers/Data/Application/123/Documents bash -c 'source installers/common.sh; detect_platform')"
ashell_false_positive="$(ASHELL=1 HOME=/Users/example bash -c 'source installers/common.sh; detect_platform')"

test "$ashell_detected" = "ios-ashell"
test "$ashell_false_positive" != "ios-ashell"

echo "[smoke] PASS mobile-dispatch"
