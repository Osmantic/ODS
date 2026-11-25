#!/usr/bin/env bash
# Contract: `ods-macos.sh status` checks OpenCode only when its LaunchAgent is
# installed. Lean installs never create it, so an unconditional probe printed
# "OpenCode (IDE): not responding" on every healthy macOS install.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT_DIR/installers/macos/ods-macos.sh"

fail() { echo "[FAIL] $*"; exit 1; }

status_src="$(sed -n '/^cmd_status()/,/^}/p' "$CLI")"
[[ -n "$status_src" ]] || fail "could not extract cmd_status from ods-macos.sh"

grep -q 'local ep_names=("$CLI_LLM_NAME" "Dashboard")$' <<<"$status_src" \
    || fail "OpenCode must not be part of the unconditional health-check list"
block="$(sed -n '/if \[\[ -e "\$OPENCODE_PLIST" \]\]; then/,/^    fi$/p' <<<"$status_src")"
grep -q 'ep_names+=("OpenCode (IDE)")' <<<"$block" \
    || fail "OpenCode health check must be added only when its LaunchAgent plist exists"
grep -q 'OPENCODE_PLIST=' "$ROOT_DIR/installers/macos/lib/constants.sh" \
    || fail "OPENCODE_PLIST must come from the shared macOS constants"

echo "[PASS] macOS status checks OpenCode only when its LaunchAgent is installed"
