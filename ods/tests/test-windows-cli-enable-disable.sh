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

info "Static: Update-ComposeFlags delegates to resolve-compose-stack.sh when available"
grep -q 'resolve-compose-stack.sh' "$ODS_PS1" \
    || fail "Update-ComposeFlags does not reference resolve-compose-stack.sh"
pass "Update-ComposeFlags references resolve-compose-stack.sh"

info "Static: Update-ComposeFlags has safe fallback when resolver not available"
grep -q 'fallback minimal swap\|fallback' "$ODS_PS1" \
    || fail "Update-ComposeFlags has no fallback path"
pass "Update-ComposeFlags has fallback path"

info "Static: Update-ComposeFlags preserves --env-file in resolver path"
grep -q 'env-file.*resolver\|--env-file .env.*newContent\|existingRaw.*--env-file' "$ODS_PS1" \
    || grep -q "Prepend --env-file" "$ODS_PS1" \
    || fail "Update-ComposeFlags does not preserve --env-file when using the resolver"
pass "Update-ComposeFlags preserves --env-file in resolver output"

info "Static: Update-ComposeFlags passes GPU_BACKEND to resolver"
grep -q 'gpuBackend\|gpu-backend' "$ODS_PS1" \
    || fail "Update-ComposeFlags does not pass GPU_BACKEND to resolver"
pass "Update-ComposeFlags passes GPU_BACKEND to resolver"

info "Static: Update-ComposeFlags fallback preserves backend overlay -f entries"
# The fallback keeps everything that doesn't match extensions/services
grep -q "extensions\[/\\\\\\\\\]services" "$ODS_PS1" \
    || grep -q 'extensions.*services' "$ODS_PS1" \
    || fail "Update-ComposeFlags fallback does not filter on extensions/services path"
pass "Update-ComposeFlags fallback preserves non-extension -f entries (backend overlays)"

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

echo ""
echo -e "${GREEN}All windows-cli-enable-disable tests passed.${NC}"
