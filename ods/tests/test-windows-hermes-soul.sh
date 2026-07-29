#!/usr/bin/env bash
# ============================================================================
# ODS Hermes SOUL.md install contract tests
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PHASE_06="$ROOT_DIR/installers/windows/phases/06-directories.ps1"
INSTALLER="$ROOT_DIR/installers/windows/install-windows.ps1"
WINDOWS_CLI="$ROOT_DIR/installers/windows/ods.ps1"
LINUX_PHASE_06="$ROOT_DIR/installers/phases/06-directories.sh"
LINUX_PHASE_11="$ROOT_DIR/installers/phases/11-services.sh"
LINUX_CLI="$ROOT_DIR/ods-cli"
HERMES_COMPOSE="$ROOT_DIR/extensions/services/hermes/compose.yaml"
HERMES_SOUL="$ROOT_DIR/extensions/services/hermes/SOUL.md.template"
SOUL_BUILDER="$ROOT_DIR/scripts/build-installation-context.py"

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

reject() {
    local pattern="$1" file="$2" label="$3"
    if grep -Fq -- "$pattern" "$file"; then
        fail "$label"
    else
        pass "$label"
    fi
}

echo ""
echo "=== Hermes SOUL.md install contract tests ==="
echo ""

[[ -f "$PHASE_06" ]] && pass "Windows phase 06 exists" || fail "Windows phase 06 missing"
[[ -f "$INSTALLER" ]] && pass "Windows installer exists" || fail "Windows installer missing"
[[ -f "$WINDOWS_CLI" ]] && pass "Windows ods.ps1 exists" || fail "Windows ods.ps1 missing"
[[ -f "$LINUX_PHASE_06" ]] && pass "Linux phase 06 exists" || fail "Linux phase 06 missing"
[[ -f "$LINUX_PHASE_11" ]] && pass "Linux phase 11 exists" || fail "Linux phase 11 missing"
[[ -f "$LINUX_CLI" ]] && pass "Linux ods-cli exists" || fail "Linux ods-cli missing"

check '(Join-Path $_dataDir "persona")' "$PHASE_06" "Windows installer creates data/persona"
check 'function Invoke-HermesSoulRefresh' "$PHASE_06" "Windows phase 06 defines Hermes SOUL refresh"
check 'build-installation-context.py' "$PHASE_06" "Windows phase 06 calls installation-context builder"
check '--profile", "local-lemonade"' "$PHASE_06" "Windows Lemonade renders compact Hermes SOUL profile"
check 'SOUL.md.template' "$PHASE_06" "Windows phase 06 has template fallback"
check 'INSTALLATION_CONTEXT' "$PHASE_06" "fallback removes dynamic marker"
check 'Invoke-HermesSoulRefresh -InstallRoot $installDir' "$PHASE_06" "Windows phase 06 renders SOUL before compose"
check 'Invoke-HermesSoulRefresh -InstallRoot $installDir -SyncContainer' "$INSTALLER" "Windows installer syncs SOUL after compose"
if [[ "$(grep -Fc 'Invoke-HermesSoulRefresh -InstallRoot $installDir -SyncContainer' "$INSTALLER")" -ge 2 ]]; then
    pass "Windows installer refreshes SOUL again after readiness settles"
else
    fail "Windows installer must refresh SOUL after optional services settle"
fi
check '-LemonadeCompact:($gpuInfo.Backend -eq "amd")' "$PHASE_06" "Windows AMD applies compact Hermes toolset profile"
check 'http://litellm:4000/v1' "$PHASE_06" "Windows AMD Hermes routes through LiteLLM"

check 'function Invoke-HermesSoulRefresh' "$WINDOWS_CLI" "Windows CLI can refresh Hermes SOUL"
check 'Invoke-HermesSoulRefresh -SyncContainer' "$WINDOWS_CLI" "Windows CLI syncs SOUL into running Hermes"
check 'Test-ODSComposeServiceAvailable -ComposeFlags $flags -Service "hermes"' "$WINDOWS_CLI" "Windows CLI gates SOUL refresh on Hermes compose presence"
check 'ODSLemonadeRuntime' "$WINDOWS_CLI" "Windows CLI manages Lemonade through Task Scheduler"
check 'if ((Get-NativeInferenceBackend) -ne "none") {' "$WINDOWS_CLI" "Windows CLI manages native inference without relying on stale pid files"

check 'hermes,persona' "$LINUX_PHASE_06" "Linux installer creates data/persona"
check 'rm -rf "$_soul_output"' "$LINUX_PHASE_11" "Linux install self-heals directory SOUL path before compose"
check '_ods_cli_refresh_soul || true' "$LINUX_CLI" "Linux CLI refreshes SOUL before compose"
check 'If a previous failed' "$LINUX_CLI" "Linux CLI documents pre-compose SOUL repair"

check './data/persona/SOUL.md:/opt/hermes/docker/SOUL.md:ro' "$HERMES_COMPOSE" "Hermes compose mounts generated persona file"
reject ':/opt/data/SOUL.md' "$HERMES_COMPOSE" "Hermes compose avoids nested /opt/data/SOUL.md bind mount"
check 'Honor literal-output requests exactly.' "$HERMES_SOUL" "Full Hermes persona prioritizes exact literal replies"
check 'literal characters only' "$SOUL_BUILDER" "Compact Hermes persona prioritizes exact literal replies"

python_cmd="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
if [[ -n "$python_cmd" ]]; then
    tmp_dir="$(mktemp -d)"
    trap 'rm -rf "$tmp_dir"' EXIT
    printf '%s\n' '<!-- INSTALLATION_CONTEXT -->' \
        'The literal `<!-- INSTALLATION_CONTEXT -->` marker is documented here.' \
        > "$tmp_dir/template.md"
    printf '%s\n' 'ODS_DEVICE_NAME=test-host' > "$tmp_dir/.env"
    "$python_cmd" "$SOUL_BUILDER" \
        --template "$tmp_dir/template.md" \
        --env "$tmp_dir/.env" \
        --output "$tmp_dir/SOUL.md" >/dev/null
    if [[ "$(grep -c '^## About this ODS install' "$tmp_dir/SOUL.md")" -eq 1 ]]; then
        pass "SOUL builder inserts dynamic context exactly once"
    else
        fail "SOUL builder duplicated dynamic context through marker documentation"
    fi
else
    fail "python is required for SOUL builder contract test"
fi

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
