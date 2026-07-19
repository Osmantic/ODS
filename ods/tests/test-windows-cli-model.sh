#!/bin/bash
# Tests for ods.ps1 model command parity (issue #1757)
# These are hermetic shell tests that exercise the static configuration
# and verify command routing in the Windows CLI helper.
#
# Run: bash ods/tests/test-windows-cli-model.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ODS_PS1="$ROOT_DIR/installers/windows/ods.ps1"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

pass() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }
info() { echo -e "${BLUE}ℹ${NC} $1"; }

info "Static: ods.ps1 exists and is non-empty"
[[ -f "$ODS_PS1" ]] || fail "ods.ps1 not found at $ODS_PS1"
[[ -s "$ODS_PS1" ]] || fail "ods.ps1 is empty"
pass "ods.ps1 exists"

info "Static: header comment documents model"
grep -q 'model.*current|list|swap' "$ODS_PS1" \
    || fail "Header comment missing 'model' usage line"
pass "Header comment documents model subcommand"

info "Static: tier-map.ps1 library is sourced in imports block"
grep -q 'tier-map\.ps1' "$ODS_PS1" \
    || fail "tier-map.ps1 not sourced in library imports"
pass "tier-map.ps1 is sourced"

info "Static: Invoke-Model function defined"
grep -q 'function Invoke-Model' "$ODS_PS1" \
    || fail "Invoke-Model function not found in ods.ps1"
pass "Invoke-Model function present"

info "Static: Invoke-Model checks current, list, and swap actions"
grep -q 'current' "$ODS_PS1" || fail "Invoke-Model missing current subcommand"
grep -q 'list' "$ODS_PS1" || fail "Invoke-Model missing list subcommand"
grep -q 'swap' "$ODS_PS1" || fail "Invoke-Model missing swap subcommand"
pass "Invoke-Model handles current, list, and swap"

info "Static: Invoke-Model uses ConvertTo-ModelFromTier and Resolve-TierConfig for swap action"
grep -q 'ConvertTo-ModelFromTier' "$ODS_PS1" \
    || fail "Invoke-Model does not call ConvertTo-ModelFromTier"
grep -q 'Resolve-TierConfig' "$ODS_PS1" \
    || fail "Invoke-Model does not call Resolve-TierConfig"
pass "Invoke-Model integrates with tier-map functions"

info "Static: Invoke-Model uses Set-ODSEnvValue to update .env on swap"
grep -q 'Set-ODSEnvValue -Key "LLM_MODEL"' "$ODS_PS1" \
    || fail "Invoke-Model does not set LLM_MODEL"
grep -q 'Set-ODSEnvValue -Key "TIER"' "$ODS_PS1" \
    || fail "Invoke-Model does not set TIER"
pass "Invoke-Model updates .env variables correctly on swap"

info "Static: command dispatcher wires 'model'"
grep -q '"model"' "$ODS_PS1" && grep -q 'Invoke-Model' "$ODS_PS1" \
    || fail "Dispatcher does not call Invoke-Model for 'model'"
pass "Dispatcher wires 'model' -> Invoke-Model"

info "Static: Show-Help lists model command"
grep -q 'model' "$ODS_PS1" && grep -q 'Inspect/swap LLM profiles' "$ODS_PS1" \
    || fail "Show-Help does not list model command"
pass "Show-Help lists model command"

info "Static: Show-Help EXAMPLES mention model swap"
grep -q 'model swap' "$ODS_PS1" \
    || fail "Show-Help EXAMPLES do not include 'model swap'"
pass "Show-Help EXAMPLES include model swap"
# ============================================================================
# Integration Test: Invoke-Model without requiring Docker
# ============================================================================
info "Integration: model subcommands work without running Docker"

to_unix_path() {
    local p="$1"
    if command -v wslpath >/dev/null 2>&1; then
        wslpath -u "$p"
    elif command -v cygpath >/dev/null 2>&1; then
        cygpath -u "$p"
    else
        if [[ "$p" =~ ^[a-zA-Z]: ]]; then
            local drive="${p:0:1}"
            drive=$(echo "$drive" | tr '[:upper:]' '[:lower:]')
            echo "/${drive}${p:2}" | tr '\\' '/'
        else
            echo "$p"
        fi
    fi
}

to_win_path() {
    local p="$1"
    if command -v wslpath >/dev/null 2>&1; then
        wslpath -w "$p"
    elif command -v cygpath >/dev/null 2>&1; then
        cygpath -w "$p"
    else
        if [[ "$p" =~ ^/[a-zA-Z]/ ]]; then
            local drive="${p:1:1}"
            drive=$(echo "$drive" | tr '[:lower:]' '[:upper:]')
            echo "${drive}:${p:2}" | tr '/' '\\'
        else
            echo "$p"
        fi
    fi
}

# Resolve a Windows-compatible temp directory
TEMP_DIR=""
set +e
WIN_TEMP=$(powershell.exe -Command "[System.IO.Path]::GetTempPath()" 2>/dev/null | tr -d '\r\n')
set -e
if [[ -n "$WIN_TEMP" ]]; then
    TEMP_DIR=$(to_unix_path "$WIN_TEMP")
else
    TEMP_DIR="/tmp"
fi
TEMP_DIR="${TEMP_DIR%/}"

MOCK_BIN=$(mktemp -d "$TEMP_DIR/ods-docker-mock.XXXXXX")
TEMP_INSTALL=$(mktemp -d "$TEMP_DIR/ods-install-mock.XXXXXX")

# Create a failing docker stub (batch file for Windows execution)
echo -e '@echo off\necho Mock docker failing >&2\nexit /b 1' > "$MOCK_BIN/docker.bat"
chmod +x "$MOCK_BIN/docker.bat"

# Create mock ODS installation files
touch "$TEMP_INSTALL/docker-compose.base.yml"
cat << 'EOF' > "$TEMP_INSTALL/.env"
LLM_MODEL="mock-model"
TIER="T0"
EOF

# Convert paths to Windows format for powershell.exe
WIN_TEMP_INSTALL=$(to_win_path "$TEMP_INSTALL")
WIN_ODS_PS1=$(to_win_path "$ODS_PS1")

# Clean up function for this test block
integration_cleanup() {
    rm -rf "$MOCK_BIN"
    rm -rf "$TEMP_INSTALL"
}

# Export the pre-translated Windows path for ODS_HOME and configure WSLENV
export ODS_HOME="$WIN_TEMP_INSTALL"
export WSLENV="ODS_HOME:PATH/l${WSLENV:+:$WSLENV}"

# Run the powershell script using a custom PATH
# Prepend the mock bin to PATH so the failing docker stub is resolved first.
set +e
out_list=$(PATH="$MOCK_BIN:$PATH" powershell.exe -ExecutionPolicy Bypass -File "$WIN_ODS_PS1" model list 2>&1)
exit_list=$?

out_current=$(PATH="$MOCK_BIN:$PATH" powershell.exe -ExecutionPolicy Bypass -File "$WIN_ODS_PS1" model current 2>&1)
exit_current=$?
set -e

# Unset variables to clean up environment
unset ODS_HOME
unset WSLENV

# Run cleanup
integration_cleanup

# Verify outputs
if [[ $exit_list -ne 0 ]]; then
    fail "model list command failed with exit code $exit_list. Output:\n$out_list"
fi

if [[ $exit_current -ne 0 ]]; then
    fail "model current command failed with exit code $exit_current. Output:\n$out_current"
fi

if echo "$out_list" | grep -q "=== Available Tiers ==="; then
    pass "model list succeeded and printed tiers without checking Docker"
else
    fail "model list did not output available tiers. Output:\n$out_list"
fi

if echo "$out_current" | grep -q "Current model: mock-model"; then
    pass "model current succeeded and printed current model without checking Docker"
else
    fail "model current did not output the mock-model. Output:\n$out_current"
fi

echo ""
echo -e "${GREEN}All windows-cli-model tests passed.${NC}"
