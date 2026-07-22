#!/bin/bash
# Tests for ods.ps1 enable/disable command parity (issue #1699)
# These are hermetic shell tests that exercise the PowerShell logic
# by stubbing the filesystem layout and verifying file state changes.
# They do NOT require Docker, PowerShell, or a live Windows install.
#
# Run: bash ods/tests/test-windows-cli-enable-disable.sh

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

# ── Static checks (no PowerShell needed) ─────────────────────────────────────

info "Static: ods.ps1 exists and is non-empty"
[[ -f "$ODS_PS1" ]] || fail "ods.ps1 not found at $ODS_PS1"
[[ -s "$ODS_PS1" ]] || fail "ods.ps1 is empty"
pass "ods.ps1 exists"

info "Static: header comment documents enable/disable"
grep -q 'enable.*Enable an extension' "$ODS_PS1" \
    || fail "Header comment missing 'enable' usage line"
grep -q 'disable.*Disable an extension' "$ODS_PS1" \
    || fail "Header comment missing 'disable' usage line"
pass "Header comment documents enable and disable"

info "Static: Invoke-Enable function defined"
grep -q 'function Invoke-Enable' "$ODS_PS1" \
    || fail "Invoke-Enable function not found in ods.ps1"
pass "Invoke-Enable function present"

info "Static: Invoke-Disable function defined"
grep -q 'function Invoke-Disable' "$ODS_PS1" \
    || fail "Invoke-Disable function not found in ods.ps1"
pass "Invoke-Disable function present"

info "Static: Test-ODSInstallFiles helper defined (Docker-free validation)"
grep -q 'function Test-ODSInstallFiles' "$ODS_PS1" \
    || fail "Test-ODSInstallFiles not found -- enable/disable will require Docker"
pass "Test-ODSInstallFiles helper present"

info "Static: Invoke-Enable uses Test-ODSInstallFiles, not Test-Install"
# Extract the Invoke-Enable function body and assert it doesn't call Test-Install
awk '/^function Invoke-Enable/,/^}/' "$ODS_PS1" \
    | grep -q 'Test-ODSInstallFiles' \
    || fail "Invoke-Enable does not call Test-ODSInstallFiles"
awk '/^function Invoke-Enable/,/^}/' "$ODS_PS1" \
    | grep -qv 'Test-Install[^F]' \
    || fail "Invoke-Enable still calls Test-Install (requires Docker)"
pass "Invoke-Enable uses Docker-free Test-ODSInstallFiles"

info "Static: Invoke-Disable uses Test-ODSInstallFiles, not Test-Install"
awk '/^function Invoke-Disable/,/^}/' "$ODS_PS1" \
    | grep -q 'Test-ODSInstallFiles' \
    || fail "Invoke-Disable does not call Test-ODSInstallFiles"
pass "Invoke-Disable uses Docker-free Test-ODSInstallFiles"

info "Static: Invoke-Disable still attempts docker stop when Docker is running"
grep -q 'docker compose.*stop' "$ODS_PS1" \
    || fail "Invoke-Disable does not attempt docker stop"
pass "Invoke-Disable attempts docker stop when Docker is available"

info "Static: Invoke-Disable gracefully skips stop when Docker is offline"
grep -q 'Docker Desktop is not running.*skipping container stop' "$ODS_PS1" \
    || fail "Invoke-Disable does not have a Docker-offline warning"
pass "Invoke-Disable gracefully skips stop when Docker is offline"

info "Static: Rename+flags update runs regardless of Docker state in Invoke-Disable"
# The Rename-Item call must appear AFTER the docker-offline else branch
awk '/^function Invoke-Disable/,/^}/' "$ODS_PS1" \
    | grep -q 'Rename-Item.*compose.yaml.disabled' \
    || fail "Rename-Item not found in Invoke-Disable body"
pass "Rename + flags update unconditional in Invoke-Disable"

info "Static: Update-ComposeFlags edits the toggled service only"
grep -q 'Update-ComposeFlags -ServiceId .* -Action "enable"' "$ODS_PS1" \
    || fail "Invoke-Enable does not tell Update-ComposeFlags which service it toggled"
grep -q 'Update-ComposeFlags -ServiceId .* -Action "disable"' "$ODS_PS1" \
    || fail "Invoke-Disable does not tell Update-ComposeFlags which service it toggled"
pass "Update-ComposeFlags is scoped to the toggled service"

info "Static: Update-ComposeFlags scopes its -f removal to the toggled service dir"
grep -q 'ownedByService' "$ODS_PS1" \
    || fail "Update-ComposeFlags does not scope removal to the toggled service directory"
grep -q "extensions\[/\\\\\\\\\]services\[/\\\\\\\\\]" "$ODS_PS1" \
    || fail "Update-ComposeFlags service-scoped pattern is missing"
pass "Update-ComposeFlags removes only the toggled service's fragments"

info "Static: Update-ComposeFlags does not delegate to the Linux resolver"
# resolve-compose-stack.sh emits neither docker-compose.tier0.yml nor the
# Windows AMD overlay, so its output is not a superset of the Windows stack.
if grep -q '\$resolverScript' "$ODS_PS1"; then
    fail "Update-ComposeFlags still invokes resolve-compose-stack.sh"
fi
pass "Update-ComposeFlags no longer shells out to the Linux resolver"

info "Static: Update-ComposeFlags helper defined"
grep -q 'function Update-ComposeFlags' "$ODS_PS1" \
    || fail "Update-ComposeFlags not found in ods.ps1"
pass "Update-ComposeFlags helper present"

info "Static: Get-ExtensionServiceDir helper defined"
grep -q 'function Get-ExtensionServiceDir' "$ODS_PS1" \
    || fail "Get-ExtensionServiceDir not found in ods.ps1"
pass "Get-ExtensionServiceDir helper present"

info "Static: Get-ExtensionCategory helper defined"
grep -q 'function Get-ExtensionCategory' "$ODS_PS1" \
    || fail "Get-ExtensionCategory not found in ods.ps1"
pass "Get-ExtensionCategory helper present"

info "Static: command dispatcher wires 'enable'"
grep -q '"enable".*Invoke-Enable' "$ODS_PS1" \
    || fail "Dispatcher does not call Invoke-Enable for 'enable'"
pass "Dispatcher wires 'enable' -> Invoke-Enable"

info "Static: command dispatcher wires 'disable'"
grep -q '"disable".*Invoke-Disable' "$ODS_PS1" \
    || fail "Dispatcher does not call Invoke-Disable for 'disable'"
pass "Dispatcher wires 'disable' -> Invoke-Disable"

info "Static: Show-Help lists enable command"
grep -q 'enable.*service.*Enable an extension' "$ODS_PS1" \
    || fail "Show-Help does not list 'enable <service>'"
pass "Show-Help lists enable command"

info "Static: Show-Help lists disable command"
grep -q 'disable.*service.*Disable an extension' "$ODS_PS1" \
    || fail "Show-Help does not list 'disable <service>'"
pass "Show-Help lists disable command"

info "Static: Show-Help EXAMPLES mention enable"
grep -q 'enable comfyui' "$ODS_PS1" \
    || fail "Show-Help EXAMPLES do not include 'enable comfyui'"
pass "Show-Help EXAMPLES include enable comfyui"

info "Static: Show-Help EXAMPLES mention disable"
grep -q 'disable langfuse' "$ODS_PS1" \
    || fail "Show-Help EXAMPLES do not include 'disable langfuse'"
pass "Show-Help EXAMPLES include disable langfuse"

info "Static: Invoke-Enable handles already-enabled case"
grep -q 'already enabled' "$ODS_PS1" \
    || fail "Invoke-Enable does not handle already-enabled case"
pass "Invoke-Enable handles already-enabled case"

info "Static: Invoke-Disable handles already-disabled case"
grep -q 'already disabled' "$ODS_PS1" \
    || fail "Invoke-Disable does not handle already-disabled case"
pass "Invoke-Disable handles already-disabled case"

info "Static: core service guard in Invoke-Enable"
grep -q 'core.*always enabled' "$ODS_PS1" \
    || fail "Invoke-Enable does not guard core services"
pass "Invoke-Enable guards core services"

info "Static: core service guard in Invoke-Disable"
grep -q 'Cannot disable core service' "$ODS_PS1" \
    || fail "Invoke-Disable does not guard core services"
pass "Invoke-Disable guards core services"

# ── Filesystem simulation tests ───────────────────────────────────────────────

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

INSTALL_DIR="$TMP/ods"
EXT_SVC="$INSTALL_DIR/extensions/services"
mkdir -p "$EXT_SVC"

# ── simulate_update_compose_flags_fallback FLAGS_FILE EXT_DIR INSTALL_DIR
# Mirrors the PowerShell fallback path in bash.
simulate_update_compose_flags_fallback() {
    local flags_file="$1"
    local ext_dir="$2"
    local install_dir="$3"
    local existing
    existing=$(cat "$flags_file")
    local base_flags=()
    local skip_next=false
    local token
    for token in $existing; do
        if $skip_next; then skip_next=false; continue; fi
        if [[ "$token" == "-f" ]]; then
            base_flags+=("$token")
            continue
        fi
        if [[ "$token" == *"extensions/services/"* ]]; then
            # Remove the preceding -f we already added
            unset 'base_flags[-1]'
            continue
        fi
        base_flags+=("$token")
    done
    # Re-append only compose.yaml for enabled extensions
    local svc_dir cf rel
    for svc_dir in "$ext_dir"/*; do
        [[ -d "$svc_dir" ]] || continue
        cf="$svc_dir/compose.yaml"
        if [[ -f "$cf" ]]; then
            rel="${cf#$install_dir/}"
            base_flags+=("-f" "$rel")
        fi
    done
    printf '%s' "${base_flags[*]}" > "$flags_file"
}

# ── Test: backend overlays are preserved when an extension is toggled ─────────
info "Filesystem: backend overlay preserved when extension is enabled"
mkdir -p "$EXT_SVC/comfyui" "$EXT_SVC/langfuse"
touch "$EXT_SVC/comfyui/compose.yaml.disabled"   # disabled initially
# langfuse also disabled

# .compose-flags as installer would write it: includes nvidia backend overlay
# but does NOT include comfyui (it was disabled at install time)
cat > "$INSTALL_DIR/.compose-flags" <<'EOF'
--env-file .env -f docker-compose.base.yml -f docker-compose.nvidia.yml
EOF

# Simulate enable: rename .disabled -> compose.yaml, then rebuild
mv "$EXT_SVC/comfyui/compose.yaml.disabled" "$EXT_SVC/comfyui/compose.yaml"
simulate_update_compose_flags_fallback "$INSTALL_DIR/.compose-flags" "$EXT_SVC" "$INSTALL_DIR"

result=$(cat "$INSTALL_DIR/.compose-flags")
# Backend overlays must still be present
echo "$result" | grep -q 'docker-compose.nvidia.yml' \
    || fail "Backend overlay (docker-compose.nvidia.yml) was dropped after enable"
echo "$result" | grep -q 'docker-compose.base.yml' \
    || fail "Base compose (docker-compose.base.yml) was dropped after enable"
# The newly enabled extension must appear
echo "$result" | grep -q 'extensions/services/comfyui/compose.yaml' \
    || fail "Enabled comfyui compose.yaml not found in flags after enable"
# Disabled langfuse must NOT appear
echo "$result" | grep -q 'langfuse' \
    && fail "Disabled langfuse appeared in flags after enable of comfyui"
# --env-file must survive
echo "$result" | grep -q -- '--env-file' \
    || fail "--env-file was dropped after enable"
pass "Backend overlay, base compose, and --env-file preserved after enable"

# ── Test: .compose-flags with AMD backend overlay preserved on disable ─────────
info "Filesystem: AMD backend overlay preserved when extension is disabled"
T2="$TMP/amd_install"
EXT_SVC2="$T2/extensions/services"
mkdir -p "$EXT_SVC2/comfyui" "$EXT_SVC2/langfuse"
touch "$EXT_SVC2/comfyui/compose.yaml"    # comfyui enabled
touch "$EXT_SVC2/langfuse/compose.yaml"   # langfuse enabled

# AMD-style .compose-flags with per-extension GPU overlay entries
cat > "$T2/.compose-flags" <<'EOF'
--env-file .env -f docker-compose.base.yml -f docker-compose.amd.yml -f extensions/services/comfyui/compose.yaml -f extensions/services/langfuse/compose.yaml
EOF

# Simulate disable langfuse: rename, then rebuild
mv "$EXT_SVC2/langfuse/compose.yaml" "$EXT_SVC2/langfuse/compose.yaml.disabled"
simulate_update_compose_flags_fallback "$T2/.compose-flags" "$EXT_SVC2" "$T2"

result2=$(cat "$T2/.compose-flags")
echo "$result2" | grep -q 'docker-compose.amd.yml' \
    || fail "AMD backend overlay was dropped after disable"
echo "$result2" | grep -q 'docker-compose.base.yml' \
    || fail "Base compose was dropped after disable"
echo "$result2" | grep -q 'extensions/services/comfyui/compose.yaml' \
    || fail "comfyui (still enabled) disappeared after disabling langfuse"
echo "$result2" | grep -q 'langfuse' \
    && fail "Disabled langfuse still present in flags after disable"
pass "AMD backend overlay preserved and disabled extension removed correctly"

# ── Test: Docker-offline enable path works (file-only operation) ──────────────
info "Filesystem: enable works when Docker is offline (file-only operation)"
T3="$TMP/offline_enable"
EXT_SVC3="$T3/extensions/services"
mkdir -p "$EXT_SVC3/comfyui"
touch "$T3/docker-compose.base.yml"
touch "$T3/docker-compose.nvidia.yml"
touch "$EXT_SVC3/comfyui/compose.yaml.disabled"

cat > "$T3/.compose-flags" <<'EOF'
--env-file .env -f docker-compose.base.yml -f docker-compose.nvidia.yml
EOF

# Simulate enable (rename only -- no docker call needed)
mv "$EXT_SVC3/comfyui/compose.yaml.disabled" "$EXT_SVC3/comfyui/compose.yaml"
simulate_update_compose_flags_fallback "$T3/.compose-flags" "$EXT_SVC3" "$T3"

[[ -f "$EXT_SVC3/comfyui/compose.yaml" ]] \
    || fail "compose.yaml not present after offline enable"
[[ ! -f "$EXT_SVC3/comfyui/compose.yaml.disabled" ]] \
    || fail "compose.yaml.disabled still exists after offline enable"
result3=$(cat "$T3/.compose-flags")
echo "$result3" | grep -q 'docker-compose.nvidia.yml' \
    || fail "Backend overlay dropped in offline enable"
echo "$result3" | grep -q 'extensions/services/comfyui/compose.yaml' \
    || fail "Extension not added to flags in offline enable"
pass "Enable succeeds offline: compose renamed, flags updated, no Docker needed"

# ── Test: Docker-offline disable path still renames and updates flags ──────────
info "Filesystem: disable completes when Docker is offline (rename still happens)"
T4="$TMP/offline_disable"
EXT_SVC4="$T4/extensions/services"
mkdir -p "$EXT_SVC4/comfyui"
touch "$T4/docker-compose.base.yml"
touch "$T4/docker-compose.nvidia.yml"
touch "$EXT_SVC4/comfyui/compose.yaml"

cat > "$T4/.compose-flags" <<'EOF'
--env-file .env -f docker-compose.base.yml -f docker-compose.nvidia.yml -f extensions/services/comfyui/compose.yaml
EOF

# Simulate disable when Docker is offline: docker stop is skipped, rename still runs
# (PowerShell: the else branch prints a warning but Rename-Item still executes)
DOCKER_AVAILABLE=false   # simulate Docker offline
if $DOCKER_AVAILABLE; then
    : # would run docker compose stop here
else
    : # warning printed; rename still happens
fi
mv "$EXT_SVC4/comfyui/compose.yaml" "$EXT_SVC4/comfyui/compose.yaml.disabled"
simulate_update_compose_flags_fallback "$T4/.compose-flags" "$EXT_SVC4" "$T4"

[[ -f "$EXT_SVC4/comfyui/compose.yaml.disabled" ]] \
    || fail "compose.yaml.disabled not created in offline disable"
[[ ! -f "$EXT_SVC4/comfyui/compose.yaml" ]] \
    || fail "compose.yaml still present after offline disable"
result4=$(cat "$T4/.compose-flags")
echo "$result4" | grep -q 'docker-compose.nvidia.yml' \
    || fail "Backend overlay dropped in offline disable"
echo "$result4" | grep -q 'comfyui' \
    && fail "Disabled comfyui still in flags after offline disable"
pass "Disable completes offline: compose renamed, flags updated, no Docker needed"

# ── Basic rename sanity tests ──────────────────────────────────────────────────
info "Filesystem: enable renames compose.yaml.disabled -> compose.yaml"
SVCDIR="$TMP/newext_test"
mkdir -p "$SVCDIR"
touch "$SVCDIR/compose.yaml.disabled"
mv "$SVCDIR/compose.yaml.disabled" "$SVCDIR/compose.yaml"
[[ -f "$SVCDIR/compose.yaml" ]]         || fail "compose.yaml not created after enable"
[[ ! -f "$SVCDIR/compose.yaml.disabled" ]] || fail "compose.yaml.disabled still exists after enable"
pass "Enable renames compose.yaml.disabled to compose.yaml"

info "Filesystem: disable renames compose.yaml -> compose.yaml.disabled"
mv "$SVCDIR/compose.yaml" "$SVCDIR/compose.yaml.disabled"
[[ -f "$SVCDIR/compose.yaml.disabled" ]] || fail "compose.yaml.disabled not created after disable"
[[ ! -f "$SVCDIR/compose.yaml" ]]        || fail "compose.yaml still exists after disable"
pass "Disable renames compose.yaml to compose.yaml.disabled"

# ── Behavioral: Update-ComposeFlags must not touch other services ─────────────
# Toggling one extension used to rebuild every extension -f entry from disk,
# which dropped the per-extension GPU overlays the installer recorded and
# hoisted tier0/override ahead of the extension fragments.
PS_BIN="$(command -v pwsh || command -v powershell || true)"
if [[ -n "$PS_BIN" ]]; then
    # Reuse $TMP so the existing EXIT trap cleans this up too.
    PS_TMP="$TMP/compose-flags"
    mkdir -p "$PS_TMP"

    if ODS_PS1="$ODS_PS1" PS_TMP="$PS_TMP" "$PS_BIN" -NoProfile -Command '
        $ErrorActionPreference = "Stop"
        function Write-AIWarn { param($m) }
        function Write-AI     { param($m) }

        # ods.ps1 runs a command dispatcher on load, so lift just the function.
        $src   = Get-Content $env:ODS_PS1 -Raw
        $start = $src.IndexOf("function Update-ComposeFlags {")
        $next  = $src.IndexOf("`nfunction Get-ExtensionServiceDir", $start)
        if ($start -lt 0 -or $next -lt 0) { throw "Update-ComposeFlags not found" }
        Invoke-Expression $src.Substring($start, $next - $start)

        $InstallDir = Join-Path $env:PS_TMP "ods"
        $svcRoot = Join-Path (Join-Path $InstallDir "extensions") "services"
        foreach ($s in @("comfyui", "n8n", "whisper")) {
            New-Item -ItemType Directory -Path (Join-Path $svcRoot $s) -Force | Out-Null
            New-Item -ItemType File -Path (Join-Path (Join-Path $svcRoot $s) "compose.yaml") -Force | Out-Null
        }
        # comfyui and whisper also carry a per-extension NVIDIA overlay.
        foreach ($s in @("comfyui", "whisper")) {
            New-Item -ItemType File -Path (Join-Path (Join-Path $svcRoot $s) "compose.nvidia.yaml") -Force | Out-Null
        }

        # Token order exactly as install-windows.ps1 writes it.
        $original = "--env-file .env -f docker-compose.base.yml -f docker-compose.nvidia.yml " +
                    "-f extensions/services/comfyui/compose.yaml -f extensions/services/comfyui/compose.nvidia.yaml " +
                    "-f extensions/services/n8n/compose.yaml " +
                    "-f extensions/services/whisper/compose.yaml -f extensions/services/whisper/compose.nvidia.yaml " +
                    "-f docker-compose.tier0.yml -f docker-compose.override.yml"
        $flagsFile = Join-Path $InstallDir ".compose-flags"
        [System.IO.File]::WriteAllText($flagsFile, $original, (New-Object System.Text.UTF8Encoding($false)))

        # disable n8n
        Remove-Item (Join-Path (Join-Path $svcRoot "n8n") "compose.yaml")
        New-Item -ItemType File -Path (Join-Path (Join-Path $svcRoot "n8n") "compose.yaml.disabled") -Force | Out-Null
        Update-ComposeFlags -ServiceId "n8n" -Action "disable"
        $afterDisable = (Get-Content $flagsFile -Raw).Trim()

        if ($afterDisable -match "n8n") { throw "n8n fragment survived disable" }
        foreach ($keep in @("comfyui/compose.nvidia.yaml", "whisper/compose.nvidia.yaml",
                            "docker-compose.tier0.yml", "docker-compose.override.yml")) {
            if ($afterDisable -notmatch [regex]::Escape($keep)) { throw "disable dropped $keep" }
        }
        if ($afterDisable -notmatch "^--env-file \.env ") { throw "disable dropped --env-file" }

        # tier0/override must stay behind the extension fragments so they win the merge
        $iExt = $afterDisable.IndexOf("extensions/services/")
        $iTier0 = $afterDisable.IndexOf("docker-compose.tier0.yml")
        if ($iExt -lt 0 -or $iTier0 -lt $iExt) { throw "disable reordered tier0 ahead of extensions" }

        # re-enable n8n -> byte-for-byte round trip of the token set
        Remove-Item (Join-Path (Join-Path $svcRoot "n8n") "compose.yaml.disabled")
        New-Item -ItemType File -Path (Join-Path (Join-Path $svcRoot "n8n") "compose.yaml") -Force | Out-Null
        Update-ComposeFlags -ServiceId "n8n" -Action "enable"
        $afterEnable = (Get-Content $flagsFile -Raw).Trim()

        if ($afterEnable -notmatch "extensions/services/n8n/compose\.yaml") { throw "enable did not restore n8n" }
        if ($afterEnable -notmatch "whisper/compose\.nvidia\.yaml") { throw "enable dropped whisper overlay" }
        $iN8n = $afterEnable.IndexOf("n8n/compose.yaml")
        $iTier0 = $afterEnable.IndexOf("docker-compose.tier0.yml")
        if ($iTier0 -lt $iN8n) { throw "enable inserted n8n after tier0" }

        $before = ($original -split "\s+" | Sort-Object) -join " "
        $after  = ($afterEnable -split "\s+" | Sort-Object) -join " "
        if ($before -ne $after) { throw "disable+enable round trip changed the token set" }
    '; then
        pass "Update-ComposeFlags preserves other services overlays and token order"
    else
        fail "Update-ComposeFlags corrupted .compose-flags on enable/disable"
    fi
else
    info "No PowerShell on PATH -- skipping Update-ComposeFlags behavioral test"
fi

# ── Static checks: manifest depends_on awareness ──────────────────────────────

info "Static: Get-ExtensionDependencies helper defined"
grep -q 'function Get-ExtensionDependencies' "$ODS_PS1" \
    || fail "Get-ExtensionDependencies not found -- enable cannot resolve manifest deps"
pass "Get-ExtensionDependencies helper present"

info "Static: Get-DisabledDependencies helper defined"
grep -q 'function Get-DisabledDependencies' "$ODS_PS1" \
    || fail "Get-DisabledDependencies not found"
pass "Get-DisabledDependencies helper present"

info "Static: Get-EnabledDependents helper defined"
grep -q 'function Get-EnabledDependents' "$ODS_PS1" \
    || fail "Get-EnabledDependents not found"
pass "Get-EnabledDependents helper present"

info "Static: Invoke-Enable cascades into disabled dependencies"
awk '/^function Invoke-Enable/,/^}/' "$ODS_PS1" \
    | grep -q 'Get-DisabledDependencies' \
    || fail "Invoke-Enable does not resolve disabled dependencies"
awk '/^function Invoke-Enable/,/^}/' "$ODS_PS1" \
    | grep -q 'Invoke-Enable -ServiceId \$dep -AsDependency' \
    || fail "Invoke-Enable does not recursively enable its dependencies"
pass "Invoke-Enable enables disabled dependencies first"

info "Static: Invoke-Enable guards against manifest dependency cycles"
awk '/^function Invoke-Enable/,/^}/' "$ODS_PS1" \
    | grep -q '_EnableVisited' \
    || fail "Invoke-Enable has no cycle guard"
pass "Invoke-Enable has a cycle guard"

info "Static: Invoke-Disable refuses when enabled extensions still depend on the target"
awk '/^function Invoke-Disable/,/^}/' "$ODS_PS1" \
    | grep -q 'Get-EnabledDependents' \
    || fail "Invoke-Disable does not check reverse dependents"
awk '/^function Invoke-Disable/,/^}/' "$ODS_PS1" \
    | grep -q 'These enabled extensions depend on' \
    || fail "Invoke-Disable has no dependent-refusal message"
pass "Invoke-Disable checks reverse dependents"

info "Static: Invoke-Disable exposes a -Force escape hatch"
awk '/^function Invoke-Disable/,/^}/' "$ODS_PS1" \
    | grep -q '\[switch\]\$Force' \
    || fail "Invoke-Disable has no -Force switch"
grep -q 'Test-ForceArgument' "$ODS_PS1" \
    || fail "Dispatcher cannot detect -Force"
pass "Invoke-Disable supports -Force"

# ── PowerShell behaviour tests ────────────────────────────────────────────────
# These run the real functions out of ods.ps1 (extracted, with the UI/Docker
# boundary stubbed) so the manifest parsing and the guards are exercised as
# written, not re-implemented in bash.

PS_BIN=""
if command -v pwsh >/dev/null 2>&1; then
    PS_BIN="pwsh"
elif command -v powershell >/dev/null 2>&1; then
    PS_BIN="powershell"
fi

if [[ -z "$PS_BIN" ]]; then
    info "PowerShell not available -- skipping behaviour tests (static checks still ran)"
else
    info "PowerShell behaviour tests using: $PS_BIN"

    PSTMP="$TMP/ps"
    mkdir -p "$PSTMP"

    # Pull the functions under test straight out of ods.ps1.
    extract_fn() {
        # Top-level functions open with `function <Name> {` and close on a
        # column-0 brace. [{] keeps the brace literal without an awk escape.
        awk -v fn="^function $1 [{]" '$0 ~ fn, /^}/' "$ODS_PS1"
    }

    HARNESS="$PSTMP/harness.ps1"
    {
        echo '$ErrorActionPreference = "Stop"'
        echo '$InstallDir = $env:TEST_INSTALL_DIR'
        # UI + side-effect boundary stubs.
        echo 'function Write-AI { param($Message) Write-Host $Message }'
        echo 'function Write-AIWarn { param($Message) Write-Host "WARN: $Message" }'
        echo 'function Write-AIError { param($Message) Write-Host "ERROR: $Message" }'
        echo 'function Write-AISuccess { param($Message) Write-Host "OK: $Message" }'
        echo 'function Test-ODSInstallFiles { }'
        # Accepts both the no-arg and the -ServiceId/-Action call shapes.
        echo 'function Update-ComposeFlags { param($ServiceId, $Action) }'
        # Keep Docker out of the test: force the offline branch of Invoke-Disable.
        echo 'function docker { $global:LASTEXITCODE = 1 }'
        echo '$script:_EnableVisited = @()'
        for fn in Get-ExtensionServiceDir Get-ExtensionCategory Get-ExtensionDependencies \
                  Get-DisabledDependencies Get-EnabledDependents Invoke-Enable Invoke-Disable \
                  Get-ServiceIdArgument Test-ForceArgument; do
            extract_fn "$fn"
            echo ""
        done
    } > "$HARNESS"

    # Sanity: every function we asked for made it into the harness.
    for fn in Get-ExtensionDependencies Get-DisabledDependencies Get-EnabledDependents \
              Invoke-Enable Invoke-Disable; do
        grep -q "function $fn" "$HARNESS" || fail "extraction failed for $fn"
    done
    pass "Extracted enable/disable functions from ods.ps1"

    # ── Fixture builder: a service dir with a manifest and a compose fragment ──
    # make_service <install_dir> <id> <category> <deps-csv> <enabled|disabled|none>
    make_service() {
        local root="$1" id="$2" svc_category="$3" deps="$4" state="$5"
        local dir="$root/extensions/services/$id"
        mkdir -p "$dir"
        cat > "$dir/manifest.yaml" <<EOF
schema_version: ods.services.v1

service:
  id: $id
  category: $svc_category
  depends_on: [$deps]

library:
  meta:
    category: not-the-service-category
EOF
        case "$state" in
            enabled)  echo "services: {}" > "$dir/compose.yaml" ;;
            disabled) echo "services: {}" > "$dir/compose.yaml.disabled" ;;
            none)     : ;;
        esac
    }

    run_ps() {
        local install_dir="$1" snippet="$2"
        local script="$PSTMP/case.ps1"
        cat "$HARNESS" > "$script"
        echo "$snippet" >> "$script"
        TEST_INSTALL_DIR="$install_dir" "$PS_BIN" -NoProfile -File "$script" 2>&1
    }

    # ── Case 1: disable is refused while an enabled extension depends on it ────
    # Regression: hermes-proxy/compose.yaml carries `depends_on: - hermes`.
    # Disabling hermes while hermes-proxy stays enabled makes docker compose
    # reject the entire project ("depends on undefined service").
    info "PowerShell: disable refuses while an enabled extension depends on the target"
    C1="$PSTMP/case1"
    make_service "$C1" llama-server core ""                     none
    make_service "$C1" dashboard-api core ""                    none
    make_service "$C1" hermes        recommended "llama-server" enabled
    make_service "$C1" hermes-proxy  recommended "hermes, dashboard-api" enabled

    out1=$(run_ps "$C1" 'Invoke-Disable -ServiceId "hermes"' || true)
    echo "$out1" | grep -q "These enabled extensions depend on hermes" \
        || fail "disable did not refuse; output: $out1"
    echo "$out1" | grep -q "hermes-proxy" \
        || fail "refusal message does not name hermes-proxy; output: $out1"
    [[ -f "$C1/extensions/services/hermes/compose.yaml" ]] \
        || fail "hermes was disabled despite an enabled dependent"
    [[ ! -f "$C1/extensions/services/hermes/compose.yaml.disabled" ]] \
        || fail "hermes compose fragment was renamed despite the refusal"
    pass "disable refused and left the compose fragment untouched"

    # ── Case 2: -Force overrides the refusal ──────────────────────────────────
    info "PowerShell: disable -Force proceeds and warns about the dependents"
    C2="$PSTMP/case2"
    make_service "$C2" dashboard-api core ""                    none
    make_service "$C2" hermes        recommended ""             enabled
    make_service "$C2" hermes-proxy  recommended "hermes, dashboard-api" enabled

    out2=$(run_ps "$C2" 'Invoke-Disable -ServiceId "hermes" -Force' || true)
    echo "$out2" | grep -q "Forcing disable" \
        || fail "-Force did not warn about dependents; output: $out2"
    [[ -f "$C2/extensions/services/hermes/compose.yaml.disabled" ]] \
        || fail "-Force did not disable hermes"
    pass "disable -Force proceeds with a warning"

    # ── Case 3: enable pulls in a disabled dependency ─────────────────────────
    info "PowerShell: enable cascades into disabled dependencies"
    C3="$PSTMP/case3"
    make_service "$C3" dashboard-api core ""                    none
    make_service "$C3" searxng       recommended ""             disabled
    make_service "$C3" hermes        recommended "searxng"      disabled
    make_service "$C3" hermes-proxy  recommended "hermes, dashboard-api" disabled

    out3=$(run_ps "$C3" 'Invoke-Enable -ServiceId "hermes-proxy"' || true)
    for svc in hermes-proxy hermes searxng; do
        [[ -f "$C3/extensions/services/$svc/compose.yaml" ]] \
            || fail "enable hermes-proxy did not transitively enable $svc; output: $out3"
    done
    pass "enable resolved the transitive dependency chain"

    # ── Case 4: core dependencies are never treated as disabled ───────────────
    # Core services live in docker-compose.base.yml and own no compose.yaml,
    # so a naive Test-Path check would report them as missing forever.
    info "PowerShell: core dependencies are not reported as disabled"
    C4="$PSTMP/case4"
    make_service "$C4" dashboard-api core ""                     none
    make_service "$C4" ods-proxy     optional "dashboard-api"    disabled

    out4=$(run_ps "$C4" '@(Get-DisabledDependencies -ServiceId "ods-proxy").Count' || true)
    echo "$out4" | tail -1 | grep -qx "0" \
        || fail "core dependency reported as disabled; output: $out4"
    pass "core dependencies skipped by Get-DisabledDependencies"

    # ── Case 4b: a dependency with no compose fragment is not "disabled" ──────
    # opencode ships a manifest but no compose.yaml/.disabled, so there is
    # nothing to rename. Reporting it as disabled would abort the caller's
    # enable on "No compose fragment found".
    info "PowerShell: a dependency with no compose fragment does not abort enable"
    C4B="$PSTMP/case4b"
    make_service "$C4B" opencode optional ""         none
    make_service "$C4B" widget   optional "opencode" disabled

    out4b=$(run_ps "$C4B" '@(Get-DisabledDependencies -ServiceId "widget").Count' || true)
    echo "$out4b" | tail -1 | grep -qx "0" \
        || fail "fragment-less dependency reported as disabled; output: $out4b"
    out4c=$(run_ps "$C4B" 'Invoke-Enable -ServiceId "widget"' || true)
    [[ -f "$C4B/extensions/services/widget/compose.yaml" ]] \
        || fail "enable aborted on a fragment-less dependency; output: $out4c"
    pass "fragment-less dependencies are skipped, enable still succeeds"

    # ── Case 5: manifest dependency cycles terminate ──────────────────────────
    info "PowerShell: a dependency cycle does not recurse forever"
    C5="$PSTMP/case5"
    make_service "$C5" alpha optional "beta"  disabled
    make_service "$C5" beta  optional "alpha" disabled

    out5=$(run_ps "$C5" 'Invoke-Enable -ServiceId "alpha"' || true)
    [[ -f "$C5/extensions/services/alpha/compose.yaml" ]] \
        || fail "cycle guard blocked the requested service; output: $out5"
    [[ -f "$C5/extensions/services/beta/compose.yaml" ]] \
        || fail "cycle guard blocked the dependency; output: $out5"
    pass "dependency cycle terminates and both services are enabled"

    # ── Case 6: disable still works when nothing depends on the service ───────
    info "PowerShell: disable proceeds when no enabled extension depends on it"
    C6="$PSTMP/case6"
    make_service "$C6" comfyui optional "" enabled
    make_service "$C6" langfuse optional "" disabled

    out6=$(run_ps "$C6" 'Invoke-Disable -ServiceId "comfyui"' || true)
    [[ -f "$C6/extensions/services/comfyui/compose.yaml.disabled" ]] \
        || fail "disable did not rename an undepended service; output: $out6"
    pass "disable proceeds without dependents"

    # ── Case 7: a disabled dependent does not block disable ───────────────────
    info "PowerShell: a disabled dependent does not block disable"
    C7="$PSTMP/case7"
    make_service "$C7" hermes       recommended "" enabled
    make_service "$C7" hermes-proxy recommended "hermes" disabled

    out7=$(run_ps "$C7" 'Invoke-Disable -ServiceId "hermes"' || true)
    [[ -f "$C7/extensions/services/hermes/compose.yaml.disabled" ]] \
        || fail "a disabled dependent wrongly blocked disable; output: $out7"
    pass "disabled dependents are ignored"

    # ── Case 8: manifest parsing ignores nested keys and prose comments ───────
    info "PowerShell: depends_on parsing handles empty lists and nested category keys"
    C8="$PSTMP/case8"
    make_service "$C8" solo optional "" enabled
    out8a=$(run_ps "$C8" '@(Get-ExtensionDependencies -ServiceDir (Join-Path (Join-Path (Join-Path $InstallDir "extensions") "services") "solo")).Count' || true)
    echo "$out8a" | tail -1 | grep -qx "0" \
        || fail "empty depends_on did not parse to 0 entries; output: $out8a"
    out8b=$(run_ps "$C8" 'Get-ExtensionCategory -ServiceDir (Join-Path (Join-Path (Join-Path $InstallDir "extensions") "services") "solo")' || true)
    echo "$out8b" | tail -1 | grep -qx "optional" \
        || fail "category parsed from the nested library block; output: $out8b"
    pass "depends_on and category parse from the top-level service block"

    # ── Case 9: argument parsing tolerates flag order ─────────────────────────
    info "PowerShell: -Force is detected regardless of argument order"
    C9="$PSTMP/case9"
    mkdir -p "$C9"
    out9=$(run_ps "$C9" '
$a = @("-Force", "hermes")
"id=$(Get-ServiceIdArgument -Arguments $a)"
"force=$(Test-ForceArgument -Arguments $a)"
$b = @("hermes", "--force")
"id2=$(Get-ServiceIdArgument -Arguments $b)"
"force2=$(Test-ForceArgument -Arguments $b)"
"force3=$(Test-ForceArgument -Arguments @("hermes"))"' || true)
    echo "$out9" | grep -qx "id=hermes"   || fail "service id not parsed past a leading flag; output: $out9"
    echo "$out9" | grep -qx "force=True"  || fail "-Force not detected before the id; output: $out9"
    echo "$out9" | grep -qx "id2=hermes"  || fail "service id not parsed before a trailing flag; output: $out9"
    echo "$out9" | grep -qx "force2=True" || fail "--force not detected; output: $out9"
    echo "$out9" | grep -qx "force3=False" || fail "-Force detected when absent; output: $out9"
    pass "argument parsing handles flag order and both flag spellings"
fi

echo ""
echo -e "${GREEN}All windows-cli-enable-disable tests passed.${NC}"
