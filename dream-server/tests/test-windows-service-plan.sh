#!/usr/bin/env bash
# ============================================================================
# Dream Server Windows service-plan tests
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PLAN_LIB="$ROOT_DIR/installers/windows/lib/service-plan.ps1"
INSTALL_PS1="$ROOT_DIR/installers/windows/install-windows.ps1"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASS=0
FAIL=0

pass() { echo -e "  ${GREEN}PASS${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}FAIL${NC} $1"; FAIL=$((FAIL + 1)); }

check() {
    local pattern="$1" file="$2" label="$3"
    if grep -Fq -- "$pattern" "$file"; then
        pass "$label"
    else
        fail "$label"
    fi
}

echo ""
echo "=== Windows service-plan tests ==="
echo ""

[[ -f "$PLAN_LIB" ]] && pass "service-plan library exists" || fail "service-plan library missing"
check 'service-plan.ps1' "$INSTALL_PS1" "Windows installer sources service-plan library"
check 'New-DreamWindowsServicePlan' "$PLAN_LIB" "service-plan constructor exists"
check 'Get-DreamWindowsServicePlanDecision' "$PLAN_LIB" "service-plan decision function exists"
check 'Test-DreamWindowsServiceEnabled' "$PLAN_LIB" "service-plan enabled helper exists"
check 'OpenClaw is deprecated' "$PLAN_LIB" "OpenClaw is documented as opt-in legacy"

if command -v pwsh >/dev/null 2>&1; then
    OUTPUT="$(PLAN_LIB="$PLAN_LIB" pwsh -NoProfile -Command '
        $ErrorActionPreference = "Stop"
        . $env:PLAN_LIB

        function Assert-Plan {
            param([bool]$Condition, [string]$Label)
            if ($Condition) { "PASS:$Label" } else { "FAIL:$Label" }
        }

        $core = New-DreamWindowsServicePlan `
            -EnableRecommended $false `
            -EnableVoice $false `
            -EnableWorkflows $false `
            -EnableRag $false `
            -EnableHermes $false `
            -EnableOpenClaw $false `
            -EnableComfyui $false `
            -EnableDeepResearch $false `
            -EnablePrivacyShield $false

        $full = New-DreamWindowsServicePlan `
            -EnableRecommended $true `
            -EnableVoice $true `
            -EnableWorkflows $true `
            -EnableRag $true `
            -EnableHermes $true `
            -EnableOpenClaw $false `
            -EnableComfyui $true `
            -EnableDeepResearch $true `
            -EnablePrivacyShield $true

        $legacy = New-DreamWindowsServicePlan `
            -EnableRecommended $false `
            -EnableVoice $false `
            -EnableWorkflows $false `
            -EnableRag $false `
            -EnableHermes $false `
            -EnableOpenClaw $true `
            -EnableComfyui $false `
            -EnableDeepResearch $false `
            -EnablePrivacyShield $false

        $unknownOptional = Get-DreamWindowsServicePlanDecision `
            -ServiceId "future-optional" `
            -Category "optional" `
            -Plan $core `
            -EnableRecommended $false

        $unknownRecommended = Get-DreamWindowsServicePlanDecision `
            -ServiceId "future-recommended" `
            -Category "recommended" `
            -Plan $core `
            -EnableRecommended $true

        Assert-Plan (-not (Test-DreamWindowsServiceEnabled -ServiceId "hermes" -Plan $core)) "Core disables Hermes"
        Assert-Plan (-not (Test-DreamWindowsServiceEnabled -ServiceId "openclaw" -Plan $core)) "Core disables OpenClaw"
        Assert-Plan (-not (Test-DreamWindowsServiceEnabled -ServiceId "tailscale" -Plan $core)) "Core disables Tailscale"
        Assert-Plan (Test-DreamWindowsServiceEnabled -ServiceId "hermes" -Plan $full) "Full enables Hermes"
        Assert-Plan (-not (Test-DreamWindowsServiceEnabled -ServiceId "openclaw" -Plan $full)) "Full keeps OpenClaw opt-in"
        Assert-Plan (Test-DreamWindowsServiceEnabled -ServiceId "ape" -Plan $full) "Full enables APE with agents"
        Assert-Plan (Test-DreamWindowsServiceEnabled -ServiceId "openclaw" -Plan $legacy) "OpenClaw flag enables legacy agent"
        Assert-Plan (Test-DreamWindowsServiceEnabled -ServiceId "ape" -Plan $legacy) "OpenClaw opt-in enables APE"
        Assert-Plan (-not $unknownOptional.Enabled) "Unknown optional is disabled"
        Assert-Plan ($unknownRecommended.Enabled) "Unknown recommended follows recommended flag"
    ')"

    while IFS= read -r line; do
        case "$line" in
            PASS:*) pass "${line#PASS:}" ;;
            FAIL:*) fail "${line#FAIL:}" ;;
        esac
    done <<< "$OUTPUT"
else
    pass "PowerShell runtime behavior skipped (pwsh unavailable)"
fi

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
