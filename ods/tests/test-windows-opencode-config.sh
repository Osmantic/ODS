#!/bin/bash
# ============================================================================
# ODS Windows OpenCode config tests
# ============================================================================
# Static checks for the Windows OpenCode config migration/update path.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DEVTOOLS_PS1="$ROOT_DIR/installers/windows/phases/07-devtools.ps1"
OPENCODE_LIB="$ROOT_DIR/installers/windows/lib/opencode-config.ps1"
INSTALLER_PS1="$ROOT_DIR/installers/windows/install-windows.ps1"
BOOTSTRAP_UPGRADE="$ROOT_DIR/scripts/bootstrap-upgrade.sh"
UPDATE_SCRIPT="$ROOT_DIR/scripts/update-windows-opencode-config.ps1"
CONSTANTS_PS1="$ROOT_DIR/installers/windows/lib/constants.ps1"
ODS_PS1="$ROOT_DIR/installers/windows/ods.ps1"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASS=0
FAIL=0

pass() { echo -e "  ${GREEN}PASS${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}FAIL${NC} $1"; FAIL=$((FAIL + 1)); }

echo ""
echo "=== Windows OpenCode config tests ==="
echo ""

[[ -f "$DEVTOOLS_PS1" ]] && pass "07-devtools.ps1 exists" || fail "07-devtools.ps1 missing"
[[ -f "$OPENCODE_LIB" ]] && pass "opencode-config.ps1 exists" || fail "opencode-config.ps1 missing"
[[ -f "$UPDATE_SCRIPT" ]] && pass "update-windows-opencode-config.ps1 exists" || fail "update-windows-opencode-config.ps1 missing"
grep -q "function Sync-WindowsOpenCodeConfigFromEnv" "$OPENCODE_LIB" && pass "env sync helper exists" || fail "env sync helper missing"
grep -q 'config.json' "$OPENCODE_LIB" && pass "config.json sync exists" || fail "config.json sync missing"
grep -q 'Sync-WindowsOpenCodeConfigFromEnv' "$DEVTOOLS_PS1" && pass "phase 07 uses shared OpenCode sync helper" || fail "phase 07 missing shared OpenCode sync helper"
grep -q 'opencode-config.ps1' "$INSTALLER_PS1" && pass "installer sources OpenCode helper library" || fail "installer missing OpenCode helper library"
grep -q 'OpenCode config synced to active model' "$INSTALLER_PS1" && pass "installer resyncs OpenCode after launch" || fail "installer missing active-model OpenCode resync"
grep -q 'update-windows-opencode-config.ps1' "$BOOTSTRAP_UPGRADE" && pass "bootstrap upgrade refreshes Windows OpenCode config" || fail "bootstrap upgrade missing Windows OpenCode refresh"
grep -q 'OpenCode config updated' "$DEVTOOLS_PS1" && pass "existing config update message exists" || fail "existing config update message missing"
grep -q 'ODS_MODEL_SWITCHBOARD' "$OPENCODE_LIB" && pass "switchboard mode is read from .env" || fail "switchboard mode missing from OpenCode helper"
grep -q 'ods/current' "$OPENCODE_LIB" && pass "switchboard alias is written to OpenCode config" || fail "switchboard alias missing from OpenCode helper"
grep -q 'LITELLM_KEY' "$OPENCODE_LIB" && pass "switchboard OpenCode route uses LiteLLM key" || fail "switchboard LiteLLM key missing from OpenCode helper"
grep -q 'ODS switchboard' "$OPENCODE_LIB" && pass "switchboard provider is labelled" || fail "switchboard provider label missing"
grep -q 'OPENCODE_TASK_NAME = "ODSOpenCodeWeb"' "$CONSTANTS_PS1" \
    && pass "OpenCode scheduled task has a stable name" \
    || fail "OpenCode scheduled task name missing"
grep -q 'Register-ScheduledTask -TaskName \$script:OPENCODE_TASK_NAME' "$DEVTOOLS_PS1" \
    && pass "installer registers OpenCode for login persistence" \
    || fail "installer must register the OpenCode login task"
grep -q 'Start-ScheduledTask -TaskName \$script:OPENCODE_TASK_NAME' "$DEVTOOLS_PS1" \
    && pass "installer starts OpenCode immediately" \
    || fail "installer must start OpenCode immediately"
grep -q 'Remove-Item Env:OPENCODE_SERVER_PASSWORD' "$DEVTOOLS_PS1" \
    && pass "OpenCode launcher clears inherited browser authentication" \
    || fail "OpenCode launcher must open directly on loopback"
grep -q -- '--hostname 127.0.0.1' "$DEVTOOLS_PS1" \
    && pass "passwordless OpenCode remains loopback-only" \
    || fail "passwordless OpenCode must remain loopback-only"
grep -q 'function Start-ODSOpenCodeRuntime' "$ODS_PS1" \
    && grep -q 'Start-ODSOpenCodeRuntime' "$ODS_PS1" \
    && pass "Windows CLI manages the OpenCode host app" \
    || fail "Windows CLI must manage the OpenCode host app"
grep -q 'function Get-ODSOpenCodePortState' "$ODS_PS1" \
    && grep -q 'OwnedByODS' "$ODS_PS1" \
    && pass "Windows CLI rejects foreign OpenCode port listeners" \
    || fail "Windows CLI must verify OpenCode port ownership"
grep -q 'function Test-ODSOpenCodePortOwned' "$DEVTOOLS_PS1" \
    && pass "installer verifies the OpenCode listener belongs to ODS" \
    || fail "installer must not accept a foreign listener as OpenCode"
grep -q '\$script:OPENCODE_TASK_NAME' "$ODS_PS1" \
    && pass "OpenCode task participates in runtime cleanup" \
    || fail "OpenCode scheduled task cleanup missing"

if grep -q 'preserving existing configuration' "$DEVTOOLS_PS1"; then
    fail "existing configs are still preserved without migration"
else
    pass "existing configs are no longer preserved without migration"
fi

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
