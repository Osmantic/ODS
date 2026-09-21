#!/bin/bash
# Contract: the macOS installer keeps the restored six-phase ODSGATE identity
# while redirected/non-interactive output remains stable and control-code free.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TEST_HOME="$(mktemp -d)"
trap 'rm -rf "$TEST_HOME"' EXIT

export ODS_HOME="$TEST_HOME/ods"
export ODS_UI_MODE="plain"
export NON_INTERACTIVE="true"
export NO_COLOR="1"
export ODS_INSTALLER_GUI=""
export CLOUD_MODE="false"
mkdir -p "$ODS_HOME"
printf 'BIND_ADDRESS=127.0.0.1\n' > "$ODS_HOME/.env"

# shellcheck source=/dev/null
. "$ODS_ROOT/installers/macos/lib/constants.sh"
# shellcheck source=/dev/null
. "$ODS_ROOT/installers/macos/lib/ui.sh"

# show_success_card only uses this command to offer an optional LAN URL.
ipconfig() { return 1; }

rendered="$({
    show_ods_banner
    phase=1
    while [[ "$phase" -le 6 ]]; do
        show_phase "$phase" 6 "TEST PHASE $phase" "test"
        phase=$((phase + 1))
    done
    show_success_card 3000 3001
} 2>&1)"

case "$rendered" in
    *"O D S G A T E"*) ;;
    *) echo "[FAIL] Restored ODSGATE identity is missing from the macOS banner" >&2; exit 1 ;;
esac
case "$rendered" in
    *"THE ODS GATEWAY IS OPEN"*) ;;
    *) echo "[FAIL] Restored gateway completion line is missing" >&2; exit 1 ;;
esac
case "$rendered" in
    *$'\033'*) echo "[FAIL] Plain macOS output contains an ANSI escape" >&2; exit 1 ;;
esac
case "$rendered" in
    *$'\r'*) echo "[FAIL] Plain macOS output contains a carriage return" >&2; exit 1 ;;
esac

phases="$(printf '%s\n' "$rendered" | sed -n 's/.*PHASE \([1-6]\)\/6.*/\1/p' | paste -sd, -)"
if [[ "$phases" != "1,2,3,4,5,6" ]]; then
    echo "[FAIL] Expected stable macOS phases 1,2,3,4,5,6; got: ${phases:-none}" >&2
    exit 1
fi

echo "[PASS] macOS installer presentation preserves the six-phase plain-output contract"
