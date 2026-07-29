#!/usr/bin/env bash
# ============================================================================
# ODS Windows service-plan tests
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
check 'New-ODSWindowsServicePlan' "$PLAN_LIB" "service-plan constructor exists"
check 'Get-ODSWindowsServicePlanDecision' "$PLAN_LIB" "service-plan decision function exists"
check 'Test-ODSWindowsServiceEnabled' "$PLAN_LIB" "service-plan enabled helper exists"
check 'Set-ODSWindowsExtensionComposeState' "$PLAN_LIB" "service-plan compose-state helper exists"
check 'OpenClaw is deprecated' "$PLAN_LIB" "OpenClaw is documented as opt-in legacy"
check 'elseif ($currentBackend -eq "amd")' "$INSTALL_PS1" "Windows installer has AMD extension overlay branch"
check 'compose.amd.yaml' "$INSTALL_PS1" "Windows installer includes AMD extension overlays"
check 'function Test-ODSWindowsDockerCredentialHelperFailure' "$INSTALL_PS1" "Windows installer detects Docker credential-helper failures"
check '$text = $text.Replace([string][char]0, "")' "$INSTALL_PS1" "Windows installer normalizes null-separated Docker output"
check 'DOCKER_BUILDKIT=0' "$INSTALL_PS1" "Windows installer can retry builds with legacy Docker builder"
check 'Get-ODSWindowsUserDockerClientArgs' "$INSTALL_PS1" "Windows installer can retry builds with the preserved user Docker config"
check 'function Invoke-ODSWindowsPlainDockerBuildService' "$INSTALL_PS1" "Windows installer has plain docker build fallback"
check 'plain docker build and DOCKER_BUILDKIT=0' "$INSTALL_PS1" "Windows installer bypasses Compose build on credential-helper failure"
check "retrying \$_svc with user's Docker config" "$INSTALL_PS1" "Windows installer retries scoped Docker config build failures with the preserved user config"
check '$enableComfyui -and $currentBackend -eq "nvidia"' "$INSTALL_PS1" "Windows installer only locally builds ComfyUI on NVIDIA"
check 'ComfyUI disabled on Windows AMD' "$ROOT_DIR/installers/windows/phases/03-features.ps1" "Windows AMD disables Dockerized ComfyUI"
check '/dev/dri and /dev/kfd' "$ROOT_DIR/installers/windows/phases/03-features.ps1" "Windows AMD ComfyUI warning names unsupported ROCm devices"

if command -v pwsh >/dev/null 2>&1; then
    OUTPUT="$(PLAN_LIB="$PLAN_LIB" pwsh -NoProfile -Command '
        $ErrorActionPreference = "Stop"
        . $env:PLAN_LIB

        function Assert-Plan {
            param([bool]$Condition, [string]$Label)
            if ($Condition) { "PASS:$Label" } else { "FAIL:$Label" }
        }

        $core = New-ODSWindowsServicePlan `
            -EnableRecommended $false `
            -EnableVoice $false `
            -EnableWorkflows $false `
            -EnableRag $false `
            -EnableHermes $false `
            -EnableOpenClaw $false `
            -EnableComfyui $false `
            -EnableDeepResearch $false `
            -EnablePrivacyShield $false

        $full = New-ODSWindowsServicePlan `
            -EnableRecommended $true `
            -EnableVoice $true `
            -EnableWorkflows $true `
            -EnableRag $true `
            -EnableHermes $true `
            -EnableOpenClaw $false `
            -EnableComfyui $true `
            -EnableDeepResearch $true `
            -EnablePrivacyShield $true

        $legacy = New-ODSWindowsServicePlan `
            -EnableRecommended $false `
            -EnableVoice $false `
            -EnableWorkflows $false `
            -EnableRag $false `
            -EnableHermes $false `
            -EnableOpenClaw $true `
            -EnableComfyui $false `
            -EnableDeepResearch $false `
            -EnablePrivacyShield $false

        $unknownOptional = Get-ODSWindowsServicePlanDecision `
            -ServiceId "future-optional" `
            -Category "optional" `
            -Plan $core `
            -EnableRecommended $false

        $unknownRecommended = Get-ODSWindowsServicePlanDecision `
            -ServiceId "future-recommended" `
            -Category "recommended" `
            -Plan $core `
            -EnableRecommended $true

        Assert-Plan (-not (Test-ODSWindowsServiceEnabled -ServiceId "hermes" -Plan $core)) "Core disables Hermes"
        Assert-Plan (-not (Test-ODSWindowsServiceEnabled -ServiceId "openclaw" -Plan $core)) "Core disables OpenClaw"
        Assert-Plan (-not (Test-ODSWindowsServiceEnabled -ServiceId "tailscale" -Plan $core)) "Core disables Tailscale"
        Assert-Plan (Test-ODSWindowsServiceEnabled -ServiceId "hermes" -Plan $full) "Full enables Hermes"
        Assert-Plan (-not (Test-ODSWindowsServiceEnabled -ServiceId "openclaw" -Plan $full)) "Full keeps OpenClaw opt-in"
        Assert-Plan (Test-ODSWindowsServiceEnabled -ServiceId "ape" -Plan $full) "Full enables APE with agents"
        Assert-Plan (Test-ODSWindowsServiceEnabled -ServiceId "openclaw" -Plan $legacy) "OpenClaw flag enables legacy agent"
        Assert-Plan (Test-ODSWindowsServiceEnabled -ServiceId "ape" -Plan $legacy) "OpenClaw opt-in enables APE"
        Assert-Plan (-not $unknownOptional.Enabled) "Unknown optional is disabled"
        Assert-Plan ($unknownRecommended.Enabled) "Unknown recommended follows recommended flag"

        $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("ods-service-plan-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
        try {
            $composePath = Join-Path $tempRoot "compose.yaml"
            Set-Content -LiteralPath $composePath -Value "services: {}" -Encoding Ascii

            $disabledResult = Set-ODSWindowsExtensionComposeState `
                -ComposePath $composePath `
                -Enabled $false
            Assert-Plan (-not $disabledResult) "Disabled compose state reports unavailable"
            Assert-Plan (-not (Test-Path -LiteralPath $composePath)) "Disabled compose fragment is hidden"
            Assert-Plan (Test-Path -LiteralPath "$composePath.disabled") "Disabled compose marker is created"

            $enabledResult = Set-ODSWindowsExtensionComposeState `
                -ComposePath $composePath `
                -Enabled $true
            Assert-Plan $enabledResult "Enabled compose state reports available"
            Assert-Plan (Test-Path -LiteralPath $composePath) "Enabled compose fragment is restored"
            Assert-Plan (-not (Test-Path -LiteralPath "$composePath.disabled")) "Enabled compose marker is removed"
        } finally {
            Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
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
