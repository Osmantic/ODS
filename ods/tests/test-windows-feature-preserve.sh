#!/usr/bin/env bash
# Tests for the Windows installer feature-selection preservation contract.
#
# install-windows.ps1 reruns that do not restate feature flags used to reset
# every $enable* variable to its phase-03 default, so the service plan
# silently reverted services the user opted in to (voice/workflows/RAG
# default off) or out of (ComfyUI/Perplexica/Privacy Shield default on).
# The installed tree records each optional service's enablement as
# compose.yaml / compose.yaml.disabled -- the same record the service plan
# consumes -- and phase 03 must now restore it unless a flag restates the
# choice.
#
# These are hermetic static contracts (no PowerShell, no Docker required),
# matching the convention in test-windows-cli-enable-disable.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE_PLAN="$ROOT_DIR/installers/windows/lib/service-plan.ps1"
FEATURES_PHASE="$ROOT_DIR/installers/windows/phases/03-features.ps1"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

pass() { echo -e "${GREEN}OK${NC} $1"; }
fail() { echo -e "${RED}FAIL${NC} $1"; exit 1; }
info() { echo -e "${BLUE}i${NC} $1"; }

# -- Static checks (no PowerShell needed) -------------------------------------

info "Static: files exist"
[[ -s "$SERVICE_PLAN" ]] || fail "service-plan.ps1 missing"
[[ -s "$FEATURES_PHASE" ]] || fail "03-features.ps1 missing"
pass "installer files exist"

info "Static: record reader exists"
grep -q 'function Get-ODSWindowsFeatureRecord' "$SERVICE_PLAN" \
    || fail "Get-ODSWindowsFeatureRecord not found"
pass "Get-ODSWindowsFeatureRecord present"

info "Static: flag resolver exists"
grep -q 'function Resolve-ODSWindowsFeatureFlag' "$SERVICE_PLAN" \
    || fail "Resolve-ODSWindowsFeatureFlag not found"
pass "Resolve-ODSWindowsFeatureFlag present"

info "Static: record reader checks both marker paths"
awk '/^function Get-ODSWindowsFeatureRecord/,/^}/' "$SERVICE_PLAN" \
    | grep -q 'compose\.yaml' \
    || fail "record reader does not inspect compose.yaml"
awk '/^function Get-ODSWindowsFeatureRecord/,/^}/' "$SERVICE_PLAN" \
    | grep -q 'disabledPath' \
    || fail "record reader does not inspect the disabled marker"
pass "record reader inspects the marker pair"

info "Static: record reader returns null on missing or ambiguous records"
awk '/^function Get-ODSWindowsFeatureRecord/,/^}/' "$SERVICE_PLAN" \
    | grep -q 'return $null' \
    || fail "record reader does not fail closed to null"
pass "record reader fails closed to null"

info "Static: resolver lets explicit flags win"
awk '/^function Resolve-ODSWindowsFeatureFlag/,/^}/' "$SERVICE_PLAN" \
    | grep -q 'if ($Explicit) { return $Current }' \
    || fail "resolver does not short-circuit explicit flags"
pass "explicit flags override the recorded selection"

info "Static: phase 03 restores every marker-backed feature flag"
for pair in \
    'enableVoice:whisper' \
    'enableWorkflows:n8n' \
    'enableRag:qdrant' \
    'enableRecommended:token-spy' \
    'enableHermes:hermes' \
    'enableOpenClaw:openclaw' \
    'enableComfyui:comfyui' \
    'enableDeepResearch:perplexica' \
    'enablePrivacyShield:privacy-shield' \
    'enableLangfuse:langfuse' \
    'enableBraveSearch:brave-search' \
    'enableODSProxy:ods-proxy' \
    'enableRemoteAccess:tailscale'; do
    var="${pair%%:*}"
    svc="${pair##*:}"
    grep -Eq "Resolve-ODSWindowsFeatureFlag +\\\$$var .+\"$svc\"" "$FEATURES_PHASE" \
        || fail "$var is not resolved from the $svc marker"
done
pass "all feature flags resolve through their recorded markers"

info "Static: preservation runs before the interactive menu"
preserve_line="$(grep -n 'Resolve-ODSWindowsFeatureFlag' "$FEATURES_PHASE" | head -1 | cut -d: -f1)"
menu_line="$(grep -n 'Read-Host' "$FEATURES_PHASE" | head -1 | cut -d: -f1)"
[[ -n "$preserve_line" && -n "$menu_line" && "$preserve_line" -lt "$menu_line" ]] \
    || fail "preservation does not run before the interactive menu"
pass "recorded selection is restored before the menu (menu stays authoritative)"

info "Static: explicit CLI flags are wired into each resolver call"
for flag in voiceFlag workflowsFlag ragFlag recommendedFlag noRecommendedFlag \
    hermesFlag noHermesFlag openClawFlag comfyuiFlag noComfyuiFlag \
    langfuseFlag noLangfuseFlag allFlag; do
    grep -Eq "Resolve-ODSWindowsFeatureFlag .*\\\$$flag" "$FEATURES_PHASE" \
        || fail "$flag is not part of any explicit expression"
done
pass "every CLI feature flag feeds an explicit expression"

info "Static: fresh-install fallback keeps caller defaults"
awk '/^function Resolve-ODSWindowsFeatureFlag/,/^}/' "$SERVICE_PLAN" \
    | grep -q 'if ($null -ne $record) { return \[bool\]$record }' \
    || fail "resolver does not apply the record when present"
awk '/^function Resolve-ODSWindowsFeatureFlag/,/^}/' "$SERVICE_PLAN" \
    | grep -q 'return $Current' \
    || fail "resolver does not keep the caller default when no record exists"
pass "missing records keep the caller default"

echo ""
echo "All Windows feature preservation contracts hold."
