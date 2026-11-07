#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Contract test: launchd plist log paths (issue #341)
#
# PR #899 moved launchd plist log paths from $INSTALL_DIR/data/ to
# $HOME/Library/Logs/ODS/ to avoid xpcproxy sandbox denials.
# This test validates the installer and generated host-agent plist.
#
# Run: bash tests/contracts/test-plist-log-paths.sh
# ============================================================================

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

INSTALL_MACOS="installers/macos/install-macos.sh"
test -f "$INSTALL_MACOS" || { echo "[FAIL] missing $INSTALL_MACOS"; exit 1; }

PASS=0
FAIL=0

# Extract the plist value (next <string> line) after a given <key>
extract_plist_value() {
    local plist="$1" key="$2"
    echo "$plist" | awk "/<key>${key}<\\/key>/ { getline; print; exit }"
}

assert_contains() {
    local label="$1" value="$2" pattern="$3"
    if echo "$value" | grep -qF "$pattern"; then
        echo "  [PASS] $label"
        PASS=$((PASS + 1))
    else
        echo "  [FAIL] $label (expected to contain '${pattern}')"
        echo "         got: $value"
        FAIL=$((FAIL + 1))
    fi
}

assert_not_contains() {
    local label="$1" value="$2" pattern="$3"
    if echo "$value" | grep -qF "$pattern"; then
        echo "  [FAIL] $label (should NOT contain '${pattern}')"
        echo "         got: $value"
        FAIL=$((FAIL + 1))
    else
        echo "  [PASS] $label"
        PASS=$((PASS + 1))
    fi
}

echo "[contract] launchd plist log paths"

# --- Extract the opencode plist heredoc ---
opencode_plist="$(awk '/<<PLIST_EOF$/,/^PLIST_EOF$/' installers/macos/lib/post-pixel-install.sh)"

[[ -n "$opencode_plist" ]] || { echo "[FAIL] could not extract opencode-web plist heredoc"; exit 1; }

# --- opencode-web plist ---
echo "  opencode-web plist:"
stdout_val="$(extract_plist_value "$opencode_plist" "StandardOutPath")"
stderr_val="$(extract_plist_value "$opencode_plist" "StandardErrorPath")"
workdir_val="$(extract_plist_value "$opencode_plist" "WorkingDirectory")"

assert_contains     "StandardOutPath uses HOME/Library/Logs/ODS"   "$stdout_val"  '${HOME}/Library/Logs/ODS/'
assert_contains     "StandardErrorPath uses HOME/Library/Logs/ODS" "$stderr_val"  '${HOME}/Library/Logs/ODS/'
assert_not_contains "StandardOutPath does not use INSTALL_DIR"             "$stdout_val"  'INSTALL_DIR'
assert_not_contains "StandardErrorPath does not use INSTALL_DIR"           "$stderr_val"  'INSTALL_DIR'
assert_contains     "WorkingDirectory uses INSTALL_DIR"                    "$workdir_val" '${INSTALL_DIR}'

# --- ods-host-agent plist ---
echo "  ods-host-agent plist:"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
(
    source installers/macos/lib/host-agent-install.sh
    HOME="$tmp/home"
    INSTALL_DIR="$tmp/external install"
    AGENT_PYTHON="$(command -v python3)"
    ODS_AGENT_PATH="/usr/bin:/bin"
    ODS_AGENT_PLIST_LABEL="com.ods.test-agent"
    ODS_AGENT_PLIST="$tmp/agent.plist"
    _write_macos_host_agent_plist
    python3 - "$ODS_AGENT_PLIST" "$HOME" "$INSTALL_DIR" <<'PY'
import pathlib
import plistlib
import sys

with open(sys.argv[1], "rb") as stream:
    job = plistlib.load(stream)
log = str(pathlib.Path(sys.argv[2]) / "Library/Logs/ODS/ods-host-agent.log")
assert job["StandardOutPath"] == job["StandardErrorPath"] == log
assert job["WorkingDirectory"] == sys.argv[3]
PY
)
echo "  [PASS] generated host-agent log paths and working directory"
PASS=$((PASS + 1))

echo ""
echo "==============================="
echo "Results: $PASS passed, $FAIL failed"
echo "==============================="

[[ $FAIL -eq 0 ]] && exit 0 || exit 1
