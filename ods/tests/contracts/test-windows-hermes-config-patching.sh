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
# 6. Windows Hermes YAML patching must use the generated HERMES_LLM_BASE_URL.
#    Windows AMD/Lemonade env generation routes Hermes through LiteLLM, and
#    Hermes model.base_url wins over OPENAI_BASE_URL, so recomputing AMD as
#    host.docker.internal:8080/api/v1 here silently undoes the env fix.
# ---------------------------------------------------------------------------
if grep -q 'HERMES_LLM_BASE_URL' "$PHASE" \
   && grep -q 'HERMES_LLM_BASE_URL' "$MONO" \
   && ! grep -A12 '\$_hermesBaseUrl = \$' "$PHASE" | grep -q 'host.docker.internal:8080/api/v1' \
   && ! grep -A12 '\$hermesBaseUrl = \$' "$MONO" | grep -q 'host.docker.internal:8080/api/v1'; then
    pass "Windows Hermes config patching follows generated HERMES_LLM_BASE_URL"
else
    fail "Windows Hermes config patching must not recompute AMD Hermes base_url as direct Lemonade"
fi

# ---------------------------------------------------------------------------
# 6b. Switchboard mode exposes stable model aliases through LiteLLM. Windows
#     Hermes must not keep requesting raw GGUF/Lemonade model IDs when the
#     gateway only advertises ods/current/default/local.
# ---------------------------------------------------------------------------
if grep -q 'ODS_MODEL_SWITCHBOARD' "$PHASE" \
   && grep -q 'ODS_MODEL_SWITCHBOARD' "$MONO" \
   && grep -q '\$_hermesModel = "ods/current"' "$PHASE" \
   && grep -q '\$ModelId = "ods/current"' "$MONO" \
   && grep -q '\$_switchboardMode -eq "enabled"' "$PHASE" \
   && grep -q '\$switchboardEnabled' "$MONO"; then
    pass "Windows Hermes config patching uses the stable switchboard alias"
else
    fail "Windows Hermes config patching must use ods/current when switchboard mode is enabled"
fi

# ---------------------------------------------------------------------------
# 7. Windows local inference needs a longer Hermes provider timeout than the
#    shared template default. The helper must expose a timeout parameter and
#    both installer paths must pass the Windows-local value.
# ---------------------------------------------------------------------------
if grep -q '\[int\]\$RequestTimeoutSeconds = 180' "$PHASE" \
   && grep -q 'request_timeout_seconds: \$RequestTimeoutSeconds' "$PHASE" \
   && grep -q '\$_hermesRequestTimeout = \$' "$PHASE" \
   && grep -q '\$hermesRequestTimeout = \$' "$MONO" \
   && grep -q -- '-RequestTimeoutSeconds \$_hermesRequestTimeout' "$PHASE" \
   && grep -q -- '-RequestTimeoutSeconds \$hermesRequestTimeout' "$MONO"; then
    pass "Windows Hermes config patching applies the local provider timeout"
else
    fail "Windows Hermes config patching must pass the local provider timeout into template and live config"
fi

# ---------------------------------------------------------------------------
# 8. The root Windows install.ps1 entrypoint must propagate failures from the
#    delegated Windows installer. The fleet harness and public docs call the
#    root script, so fail-loud checks are useless if the wrapper always exits 0.
# ---------------------------------------------------------------------------
root_wrapper_block="$(awk '
    /\$global:LASTEXITCODE = 0/ { in_block=1 }
    in_block { print }
    in_block && /exit 1/ { exit }
' "$ROOT_INSTALLER")"
delegated_line="$(grep -nF '& $ODSInstaller @PSBoundParameters' <<<"$root_wrapper_block" | head -n1 | cut -d: -f1 || true)"
exit_capture_line="$(grep -nF '$installerExit = if ($null -ne $global:LASTEXITCODE' <<<"$root_wrapper_block" | head -n1 | cut -d: -f1 || true)"
nonzero_line="$(grep -nF 'if ($installerExit -ne 0) {' <<<"$root_wrapper_block" | head -n1 | cut -d: -f1 || true)"
success_line="$(grep -nF 'if ($installerSucceeded) {' <<<"$root_wrapper_block" | head -n1 | cut -d: -f1 || true)"

if [[ -n "$delegated_line" && -n "$exit_capture_line" && -n "$nonzero_line" && -n "$success_line" \
      && "$delegated_line" -lt "$exit_capture_line" \
      && "$exit_capture_line" -lt "$nonzero_line" \
      && "$nonzero_line" -lt "$success_line" ]]; then
    pass "Root Windows installer checks delegated exit codes before success"
else
    fail "Root install.ps1 must preserve delegated nonzero exits before trusting PowerShell success"
fi

if grep -q '\$global:LASTEXITCODE = 0' "$MONO" \
   && tail -n 5 "$MONO" | grep -q 'exit 0'; then
    pass "Delegated Windows installer clears native exit code on success"
else
    fail "install-windows.ps1 must clear LASTEXITCODE and exit 0 on the success path"
fi

echo "------------------------------------------------------------"
echo "PASS=$PASS FAIL=$FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
