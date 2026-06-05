#!/usr/bin/env bash
# Windows Hermes config patching contract.
#
# Guards the Windows-AMD failure where the installer printed "Patched Hermes
# config" but both the mounted template and data/hermes/config.yaml still held
# the upstream defaults:
#
#   model.default: qwen3.5-9b
#   base_url: http://llama-server:8080/v1
#
# That leaves Hermes on an invalid/wrong local config for native Lemonade. The
# Windows installer must create the live config before first container start,
# patch template + live config, and fail loudly if either patch did not land.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

PHASE="installers/windows/phases/06-directories.ps1"
MONO="installers/windows/install-windows.ps1"
ROOT_INSTALLER="../install.ps1"

PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

echo "[contract] Windows Hermes config patching"

# ---------------------------------------------------------------------------
# 1. The patch helper must not silently no-op when the target file is missing.
# ---------------------------------------------------------------------------
if grep -q 'if (-not (Test-Path \$Path)) { return \$false }' "$PHASE"; then
    pass "Update-HermesConfigFile reports missing targets as failure"
else
    fail "Update-HermesConfigFile must return false when the target file is missing"
fi

# ---------------------------------------------------------------------------
# 2. The helper must use explicit UTF-8 reads and UTF-8-no-BOM writes.
# ---------------------------------------------------------------------------
if grep -q 'ReadAllText(\$Path, \$utf8NoBom)' "$PHASE" \
   && grep -q '\[System.IO.File\]::WriteAllText(\$Path, \$content, \$utf8NoBom)' "$PHASE"; then
    pass "Hermes config patching uses dependency-free explicit UTF-8 read/write"
else
    fail "Hermes config patching must use dependency-free explicit UTF-8 read/write"
fi

# ---------------------------------------------------------------------------
# 3. The helper must verify the requested model/base URL after writing.
# ---------------------------------------------------------------------------
if grep -Fq 'default: ".*"\r?$' "$PHASE" \
   && grep -Fq 'base_url: ".*"\r?$' "$PHASE" \
   && grep -q '\.Contains("  default: `"\$Model`"")' "$PHASE" \
   && grep -q '\.Contains("  base_url: `"\$BaseUrl`"")' "$PHASE"; then
    pass "Hermes config patching is CRLF-tolerant and verifies model/base_url after write"
else
    fail "Hermes config patching must handle CRLF YAML and verify model/base_url after write"
fi

# ---------------------------------------------------------------------------
# 4. Windows phase 06 must create data/hermes/config.yaml before Hermes first
#    start so the container cannot copy stale upstream defaults into /opt/data.
# ---------------------------------------------------------------------------
if grep -q 'Copy-Item -Path \$_hermesTemplate -Destination \$_hermesLive -Force' "$PHASE"; then
    pass "Phase 06 seeds the live Hermes config before first container start"
else
    fail "Phase 06 must copy the template to data/hermes/config.yaml before patching"
fi

# ---------------------------------------------------------------------------
# 5. Both Windows installer paths must fail loudly if either template or live
#    config did not receive the patch.
# ---------------------------------------------------------------------------
if grep -q 'Failed to patch Hermes config for Windows runtime' "$PHASE" \
   && grep -q 'Failed to patch Hermes config for Windows runtime' "$MONO"; then
    pass "Windows installer paths fail loudly when Hermes config patching does not land"
else
    fail "Windows installer paths must fail loudly when Hermes config patching does not land"
fi

# ---------------------------------------------------------------------------
# 6. The root Windows install.ps1 entrypoint must propagate failures from the
#    delegated Windows installer. The fleet harness and public docs call the
#    root script, so fail-loud checks are useless if the wrapper always exits 0.
# ---------------------------------------------------------------------------
if grep -q '\$global:LASTEXITCODE = 0' "$ROOT_INSTALLER" \
   && grep -q '& \$DreamServerInstaller @PSBoundParameters' "$ROOT_INSTALLER" \
   && grep -q 'if (\$installerSucceeded) {' "$ROOT_INSTALLER" \
   && grep -q 'exit 0' "$ROOT_INSTALLER" \
   && grep -q 'exit \$installerExit' "$ROOT_INSTALLER"; then
    pass "Root Windows installer exits 0 on success and propagates delegated failure codes"
else
    fail "Root install.ps1 must exit 0 on success and preserve delegated Windows installer failures"
fi

echo "------------------------------------------------------------"
echo "PASS=$PASS FAIL=$FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
