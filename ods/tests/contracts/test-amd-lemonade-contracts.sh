#!/usr/bin/env bash
# AMD/Lemonade compose stack contract tests.
# Validates that the AMD overlay + extension overlays produce a correct
# compose configuration for Lemonade-based inference.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

PASS=0
FAIL=0

pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }
json_get() {
    python3 - "$1" "$2" <<'PY'
import json
import sys

path, key_path = sys.argv[1], sys.argv[2]
with open(path, "r", encoding="utf-8") as f:
    value = json.load(f)
for key in key_path.split("."):
    value = value[key]
print(value)
PY
}

# ---------------------------------------------------------------------------
# 1. Required compose files exist
# ---------------------------------------------------------------------------
echo "[contract] AMD compose files exist"
for f in docker-compose.base.yml docker-compose.amd.yml \
         extensions/services/litellm/compose.yaml \
         extensions/services/litellm/compose.amd.yaml \
         extensions/services/litellm/compose.local.yaml \
         extensions/services/llama-server/Dockerfile.amd; do
    if [[ -f "$f" ]]; then
        pass "exists: $f"
    else
        fail "missing: $f"
    fi
done

# ---------------------------------------------------------------------------
# 2. Lemonade launch uses absolute path
# ---------------------------------------------------------------------------
echo "[contract] Lemonade launch uses absolute path"
if grep -q '/opt/lemonade/lemonade-server' docker-compose.amd.yml \
    || grep -q 'exec /opt/lemonade/lemonade-server' extensions/services/llama-server/lemonade-entrypoint.sh; then
    pass "entrypoint: launches absolute path /opt/lemonade/lemonade-server"
else
    fail "entrypoint: must launch absolute path /opt/lemonade/lemonade-server directly or via wrapper"
fi

# ---------------------------------------------------------------------------
# 3. Lemonade healthcheck uses /api/v1/health
# ---------------------------------------------------------------------------
echo "[contract] Lemonade healthcheck endpoint"
if grep -q '/api/v1/health' docker-compose.amd.yml; then
    pass "healthcheck: /api/v1/health"
else
    fail "healthcheck: must use /api/v1/health (not /health)"
fi

# ---------------------------------------------------------------------------
# 4. LiteLLM AMD overlay does NOT unset LITELLM_MASTER_KEY (auth must be enforced)
# ---------------------------------------------------------------------------
echo "[contract] LiteLLM auth enforced on AMD"
if grep -qE '^[[:space:]]*unset[[:space:]]+LITELLM_MASTER_KEY' \
        extensions/services/litellm/compose.amd.yaml 2>/dev/null; then
    fail "litellm compose.amd.yaml: 'unset LITELLM_MASTER_KEY' is an auth bypass — must be removed"
else
    pass "litellm compose.amd.yaml: no 'unset LITELLM_MASTER_KEY' (auth enforced)"
fi

# ---------------------------------------------------------------------------
# 5. Lemonade config has no master_key
# ---------------------------------------------------------------------------
echo "[contract] Lemonade LiteLLM config has no master_key"
if [[ -f config/litellm/lemonade.yaml ]]; then
    if grep -q 'master_key' config/litellm/lemonade.yaml; then
        fail "lemonade.yaml: must not contain master_key"
    elif ! grep -q 'request_timeout: 900' config/litellm/lemonade.yaml \
        || ! grep -q 'stream_timeout: 900' config/litellm/lemonade.yaml; then
        fail "lemonade.yaml: must keep long-model proxy timeouts at 900s"
    else
        pass "lemonade.yaml: no master_key and long-model proxy timeouts"
    fi
else
    fail "lemonade.yaml: file missing"
fi

# ---------------------------------------------------------------------------
# 5b. Linux AMD/Lemonade keeps Hermes provider timeout long enough for full models
# ---------------------------------------------------------------------------
echo "[contract] Linux Lemonade Hermes timeout is lifted from the ODS default"
if grep -q '_hermes_request_timeout=900' installers/phases/11-services.sh \
   && grep -q -- '--request-timeout-seconds "$_hermes_request_timeout"' installers/phases/11-services.sh \
   && grep -q 'is_windows_bash || \[\[ "$runtime" == "lemonade" || "$llm_backend" == "lemonade" \]\]' scripts/bootstrap-upgrade.sh \
   && grep -q 'is_windows_bash || \[\[ "$_gpu_backend_for_hermes" == "amd" || "$_hermes_llm_backend_for_timeout" == "lemonade" \]\]' scripts/bootstrap-upgrade.sh; then
    pass "Linux Lemonade Hermes provider timeout is upgraded to 900s"
else
    fail "Linux Lemonade Hermes config must pass --request-timeout-seconds 900 at install and after bootstrap swap"
fi

echo "[contract] Lemonade ODS Talk keeps the same long-model timeout"
if grep -q 'ODS_TALK_HERMES_TIMEOUT=${ODS_TALK_HERMES_TIMEOUT:-900}' docker-compose.amd.yml \
   && grep -q 'ODS_TALK_HERMES_TIMEOUT=${ODS_TALK_HERMES_TIMEOUT:-900}' docker-compose.lemonade-external.yml \
   && grep -q 'ODS_TALK_HERMES_TIMEOUT=${ODS_TALK_HERMES_TIMEOUT:-900}' installers/windows/docker-compose.windows-amd.yml; then
    pass "Lemonade ODS Talk Hermes timeout is upgraded to 900s"
else
    fail "Lemonade ODS Talk must set ODS_TALK_HERMES_TIMEOUT=900 in AMD, external, and Windows AMD overlays"
fi

# ---------------------------------------------------------------------------
# 6. Dockerfile.amd installs libatomic1
# ---------------------------------------------------------------------------
echo "[contract] Dockerfile.amd includes libatomic1"
if grep -q 'libatomic1' extensions/services/llama-server/Dockerfile.amd; then
    pass "Dockerfile.amd: libatomic1 installed"
else
    fail "Dockerfile.amd: must install libatomic1"
fi

# ---------------------------------------------------------------------------
# 7. Dockerfile.amd pins image tag (not :latest)
# ---------------------------------------------------------------------------
echo "[contract] Dockerfile.amd pins Lemonade image tag"
AMD_LEMONADE_IMAGE="$(json_get config/backends/amd.json runtime.lemonade.container_image)"
if grep -q 'lemonade-server:latest' extensions/services/llama-server/Dockerfile.amd; then
    fail "Dockerfile.amd: must pin a specific tag, not :latest"
elif grep -q "$AMD_LEMONADE_IMAGE" extensions/services/llama-server/Dockerfile.amd; then
    pass "Dockerfile.amd: pinned image tag matches amd.json"
else
    fail "Dockerfile.amd: no matching Lemonade image reference found"
fi

# ---------------------------------------------------------------------------
# 7b. Dockerfile.amd scopes Lemonade image ARG before first FROM
# ---------------------------------------------------------------------------
echo "[contract] Dockerfile.amd scopes Lemonade image ARG before FROM"
_first_from=$(grep -n '^FROM ' extensions/services/llama-server/Dockerfile.amd | head -1 | cut -d: -f1)
_lemonade_arg=$(grep -n '^ARG LEMONADE_SERVER_IMAGE=' extensions/services/llama-server/Dockerfile.amd | head -1 | cut -d: -f1)
if [[ -n "$_first_from" && -n "$_lemonade_arg" && "$_lemonade_arg" -lt "$_first_from" ]]; then
    pass "Dockerfile.amd: LEMONADE_SERVER_IMAGE declared before first FROM"
else
    fail "Dockerfile.amd: LEMONADE_SERVER_IMAGE must be declared before first FROM for later FROM use"
fi
unset _first_from _lemonade_arg

# ---------------------------------------------------------------------------
# 8. Context size is configurable
# ---------------------------------------------------------------------------
echo "[contract] Lemonade context size configurable"
if grep -q 'LEMONADE_CTX_SIZE' docker-compose.amd.yml; then
    pass "CTX_SIZE passed to Lemonade container"
else
    fail "docker-compose.amd.yml must pass LEMONADE_CTX_SIZE"
fi

# ---------------------------------------------------------------------------
# 8b. AMD/Lemonade model routing preserves the selected GGUF
# ---------------------------------------------------------------------------
echo "[contract] AMD Lemonade routes selected GGUF through extra_models_dir"
if grep -q -- '--extra-models-dir' docker-compose.amd.yml \
    && grep -q -- '/models' docker-compose.amd.yml; then
    pass "docker-compose.amd.yml: Lemonade imports ODS-managed GGUFs from /models"
else
    fail "docker-compose.amd.yml must expose ODS-managed GGUFs through Lemonade --extra-models-dir /models"
fi
if python3 scripts/render-runtime-configs.py \
        --surface litellm-lemonade \
        --ods-mode lemonade \
        --gpu-backend amd \
        --gguf-file contract-selected.gguf \
        --lemonade-api-base http://llama-server:8080/api/v1 \
    | grep -q 'openai/extra.contract-selected.gguf'; then
    pass "LiteLLM Lemonade config maps default/wildcard to extra.\${GGUF_FILE}"
else
    fail "LiteLLM Lemonade config must map selected GGUF to openai/extra.\${GGUF_FILE}"
fi
if python3 scripts/render-runtime-configs.py \
        --surface litellm-lemonade \
        --ods-mode lemonade \
        --gpu-backend amd \
        --gguf-file contract-selected.gguf \
        --lemonade-api-base http://llama-server:8080/api/v1 \
    | grep -q 'request_timeout: 900' \
    && python3 scripts/render-runtime-configs.py \
        --surface litellm-lemonade \
        --ods-mode lemonade \
        --gpu-backend amd \
        --gguf-file contract-selected.gguf \
        --lemonade-api-base http://llama-server:8080/api/v1 \
    | grep -q 'stream_timeout: 900'; then
    pass "LiteLLM Lemonade config keeps long-model proxy timeouts at 900s"
else
    fail "LiteLLM Lemonade config must set request_timeout and stream_timeout to 900s"
fi
if grep -q '_prewarm_model="extra.${GGUF_FILE}"' installers/phases/12-health.sh; then
    pass "Phase 12 prewarms AMD Lemonade using extra.\${GGUF_FILE}"
else
    fail "Phase 12 must prewarm AMD Lemonade with extra.\${GGUF_FILE}"
fi

# ---------------------------------------------------------------------------
# 9. Service registry health override exists
# ---------------------------------------------------------------------------
echo "[contract] Service registry AMD health override"
if grep -q 'SERVICE_HEALTH.*api/v1/health' lib/service-registry.sh; then
    pass "service-registry.sh: AMD health endpoint override"
else
    fail "service-registry.sh: must override health endpoint for AMD/Lemonade"
fi

# ---------------------------------------------------------------------------
# 10. Schema allows ODS_MODE=lemonade
# ---------------------------------------------------------------------------
echo "[contract] .env schema allows lemonade mode"
if grep -q '"lemonade"' .env.schema.json; then
    pass ".env.schema.json: lemonade in ODS_MODE enum"
else
    fail ".env.schema.json: must include lemonade in ODS_MODE enum"
fi

# ---------------------------------------------------------------------------
# 11. APE healthcheck does not use curl
# ---------------------------------------------------------------------------
echo "[contract] APE healthcheck uses python (not curl)"
if grep -q 'urllib.request' extensions/services/ape/compose.yaml; then
    pass "ape compose.yaml: python urllib healthcheck"
elif grep -q 'curl' extensions/services/ape/compose.yaml; then
    fail "ape compose.yaml: must not use curl (not in slim image)"
else
    fail "ape compose.yaml: no healthcheck found"
fi

# ---------------------------------------------------------------------------
# 12. Compose stack resolver includes lemonade in local mode overlay
# ---------------------------------------------------------------------------
echo "[contract] Compose resolver loads local overlays for lemonade mode"
if grep -q 'lemonade' scripts/resolve-compose-stack.sh; then
    pass "resolve-compose-stack.sh: lemonade mode recognized"
else
    fail "resolve-compose-stack.sh: must recognize lemonade mode for local overlays"
fi

# ---------------------------------------------------------------------------
# 13. AMD backend contract centralizes Lemonade runtime metadata
# ---------------------------------------------------------------------------
echo "[contract] AMD backend contract exposes Lemonade runtime"
if [[ "$(json_get config/backends/amd.json runtime.lemonade.container_image)" == "ghcr.io/lemonade-sdk/lemonade-server:v10.2.0" ]]; then
    pass "amd.json: Linux Lemonade image pin present"
else
    fail "amd.json: runtime.lemonade.container_image must pin v10.2.0"
fi
if [[ "$(json_get config/backends/amd.json runtime.lemonade.windows_version)" == "10.0.0" ]] \
    && [[ "$(json_get config/backends/amd.json runtime.lemonade.windows_msi_file)" == "lemonade-server-minimal.msi" ]]; then
    pass "amd.json: Windows Lemonade MSI contract present"
else
    fail "amd.json: Windows Lemonade MSI contract missing"
fi

echo "[contract] Windows Lemonade follows the normal per-user install contract"
if grep -q 'INSTALLDIR=' installers/windows/install-windows.ps1 \
   && ! grep -q 'ALLUSERS=1' installers/windows/install-windows.ps1 \
   && grep -q 'LOCALAPPDATA' installers/windows/lib/backend-contract.ps1 \
   && grep -q 'LOCALAPPDATA' bin/ods-host-agent.py; then
    pass "Windows Lemonade uses and resolves the per-user MSI install location"
else
    fail "Windows Lemonade must use the non-elevated per-user MSI install location across lifecycle paths"
fi

# ---------------------------------------------------------------------------
# 14. Linux AMD image consumers use the same Lemonade image pin
# ---------------------------------------------------------------------------
echo "[contract] AMD Lemonade image pin is consistent"
if grep -q "$AMD_LEMONADE_IMAGE" docker-compose.amd.yml \
    && grep -q "$AMD_LEMONADE_IMAGE" extensions/services/llama-server/Dockerfile.amd \
    && grep -q "$AMD_LEMONADE_IMAGE" installers/phases/08-images.sh; then
    pass "compose, Dockerfile, and phase 08 share AMD Lemonade image pin"
else
    fail "compose, Dockerfile, and phase 08 must share AMD Lemonade image pin"
fi

# ---------------------------------------------------------------------------
# 15. AMD runtime env contract exists and is passed to dashboard-api
# ---------------------------------------------------------------------------
echo "[contract] AMD runtime env contract"
for key in AMD_INFERENCE_RUNTIME AMD_INFERENCE_BACKEND AMD_INFERENCE_LOCATION AMD_INFERENCE_PORT AMD_INFERENCE_SUPPORTED_BACKENDS AMD_INFERENCE_RUNTIME_MODE AMD_INFERENCE_MANAGED LEMONADE_SERVER_IMAGE; do
    if grep -q "\"$key\"" .env.schema.json; then
        pass ".env.schema.json: $key documented"
    else
        fail ".env.schema.json: $key missing"
    fi
done
for key in AMD_INFERENCE_RUNTIME AMD_INFERENCE_BACKEND AMD_INFERENCE_LOCATION AMD_INFERENCE_PORT AMD_INFERENCE_SUPPORTED_BACKENDS AMD_INFERENCE_RUNTIME_MODE AMD_INFERENCE_MANAGED; do
    if grep -q "$key" docker-compose.amd.yml && grep -q "$key" installers/windows/docker-compose.windows-amd.yml; then
        pass "dashboard-api overlays pass $key"
    else
        fail "dashboard-api overlays must pass $key"
    fi
done
if grep -q 'AMD_INFERENCE_RUNTIME_MODE=.*linux-container' installers/phases/06-directories.sh \
    && grep -q 'AMD_INFERENCE_SUPPORTED_BACKENDS=' installers/phases/06-directories.sh \
    && grep -q 'AMD_INFERENCE_MANAGED=.*true' installers/phases/06-directories.sh; then
    pass "Linux installer writes AMD capability metadata"
else
    fail "Linux installer must write AMD runtime mode, managed state, and supported backends"
fi
if grep -q 'windows-legacy-lemonade' installers/windows/phases/06-directories.ps1 \
    && grep -q 'windows-llama-server-fallback' installers/windows/install-windows.ps1 \
    && grep -q 'AMD_INFERENCE_SUPPORTED_BACKENDS' installers/windows/lib/env-generator.ps1; then
    pass "Windows installer writes AMD capability metadata"
else
    fail "Windows installer must write legacy Lemonade and llama-server fallback capability metadata"
fi

# ---------------------------------------------------------------------------
# 16. Windows backend contract helper parses explicit roots
# ---------------------------------------------------------------------------
echo "[contract] Windows backend contract helper"
if [[ -f installers/windows/lib/backend-contract.ps1 ]]; then
    pass "backend-contract.ps1 exists"
else
    fail "backend-contract.ps1 missing"
fi
_lemonade_ps_cmd=()
if command -v pwsh >/dev/null 2>&1; then
    _lemonade_ps_cmd=(pwsh -NoProfile)
elif command -v powershell.exe >/dev/null 2>&1; then
    _lemonade_ps_cmd=(powershell.exe -NoProfile -ExecutionPolicy Bypass)
fi
if ((${#_lemonade_ps_cmd[@]} > 0)); then
    _ps_tmp="${TMPDIR:-/tmp}"
    if ROOT_DIR="$ROOT_DIR" AMD_LEMONADE_IMAGE="$AMD_LEMONADE_IMAGE" TEMP="$_ps_tmp" ProgramFiles="$_ps_tmp" USERPROFILE="$_ps_tmp" "${_lemonade_ps_cmd[@]}" -Command '
        $ErrorActionPreference = "Stop"
        . (Join-Path $env:ROOT_DIR "installers/windows/lib/backend-contract.ps1")
        $runtime = Get-ODSAmdLemonadeRuntime -RootPath $env:ROOT_DIR
        if ($runtime.container_image -ne $env:AMD_LEMONADE_IMAGE) {
            throw "Unexpected container image: $($runtime.container_image)"
        }
        $failed = $false
        try {
            Get-ODSAmdLemonadeRuntime -RootPath (Join-Path $env:ROOT_DIR "missing-root") | Out-Null
        } catch {
            $failed = $true
        }
        if (-not $failed) {
            throw "Expected missing root to fail"
        }
        . (Join-Path $env:ROOT_DIR "installers/windows/lib/constants.ps1")

        $probeRoot = Join-Path $env:TEMP "ods-lemonade-resolver-contract"
        Remove-Item -LiteralPath $probeRoot -Recurse -Force -ErrorAction SilentlyContinue
        $programFiles = Join-Path $probeRoot "Program Files"
        $programFilesX86 = Join-Path $probeRoot "Program Files (x86)"
        ${env:ProgramFiles} = $programFiles
        ${env:ProgramFiles(x86)} = $programFilesX86
        $script:LEMONADE_EXE = Join-Path (Join-Path (Join-Path $programFiles "Lemonade Server") "bin") "lemonade-server.exe"
        $x86Exe = Join-Path (Join-Path (Join-Path $programFilesX86 "Lemonade Server") "bin") "LemonadeServer.exe"
        New-Item -ItemType Directory -Path (Split-Path $x86Exe) -Force | Out-Null
        Set-Content -LiteralPath $x86Exe -Value "stub" -NoNewline
        $resolved = Resolve-ODSLemonadeExe
        if ($resolved -ne $x86Exe) {
            throw "Expected Program Files (x86) LemonadeServer.exe path, got: $resolved"
        }

        $modelsDir = Join-Path $probeRoot "models"
        New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null
        $modern = Get-ODSLemonadeLaunchContract `
            -ExecutablePath $x86Exe -VersionOverride "10.7.0.0" `
            -Port 8080 -BindAddress "0.0.0.0" -ModelsDir $modelsDir `
            -AdminApiKey "contract-admin-key"
        if (-not $modern.Modern -or $modern.ArgumentString -ne "--port 8080 --host 0.0.0.0") {
            throw "Unexpected Lemonade 10.7 launch arguments: $($modern.ArgumentString)"
        }
        foreach ($obsolete in @("serve", "--no-tray", "--llamacpp", "--extra-models-dir")) {
            if ($modern.ArgumentList -contains $obsolete) {
                throw "Lemonade 10.7 contract retained obsolete argument: $obsolete"
            }
        }

        $legacy = Get-ODSLemonadeLaunchContract `
            -ExecutablePath $x86Exe -VersionOverride "10.6.9" `
            -Port 8080 -BindAddress "127.0.0.1" -ModelsDir $modelsDir
        foreach ($required in @("serve", "--no-tray", "--llamacpp", "--extra-models-dir")) {
            if ($legacy.ArgumentList -notcontains $required) {
                throw "Legacy Lemonade contract lost required argument: $required"
            }
        }

        try {
            $null = Get-ODSLemonadeLaunchContract `
                -ExecutablePath $x86Exe -VersionOverride "10.7.0" `
                -Port 8080 -BindAddress "0.0.0.0" -ModelsDir $modelsDir
            throw "Modern Lemonade accepted an unauthenticated non-loopback bind"
        } catch {
            if ($_.Exception.Message -notmatch "requires an admin API key") { throw }
        }
        $loopbackWithoutKey = Get-ODSLemonadeLaunchContract `
            -ExecutablePath $x86Exe -VersionOverride "10.7.0" `
            -Port 8080 -BindAddress "127.0.0.1" -ModelsDir $modelsDir
        if ($loopbackWithoutKey.BindAddress -ne "127.0.0.1") {
            throw "Modern Lemonade loopback launch changed its bind address"
        }

        $script:configPost = $null
        $script:expectedModelsDir = [System.IO.Path]::GetFullPath($modelsDir)
        function Invoke-RestMethod {
            param($Method, $Uri, $Headers, $ContentType, $Body, $TimeoutSec, $ErrorAction)
            if ($Method -eq "Post") {
                $script:configPost = [pscustomobject]@{
                    Uri = $Uri
                    Headers = $Headers
                    Body = $Body | ConvertFrom-Json
                }
                return [pscustomobject]@{ status = "success" }
            }
            if ($Uri -match "/api/v1/models$") {
                return [pscustomobject]@{
                    data = @(
                        [pscustomobject]@{
                            id = "Modern-Model"
                            checkpoint = (Join-Path $script:expectedModelsDir "Modern-Model.gguf")
                            checkpoints = [pscustomobject]@{}
                        }
                    )
                }
            }
            if ($Uri -match "/api/v1/health$") {
                return [pscustomobject]@{ version = "10.7.0" }
            }
            return [pscustomobject]@{
                ctx_size = 4096
                extra_models_dir = $script:expectedModelsDir
                llamacpp = [pscustomobject]@{ backend = "vulkan" }
            }
        }
        $null = Set-ODSLemonadeModernRuntimeConfig `
            -Port 8080 -ModelsDir $modelsDir -AdminApiKey "contract-admin-key" `
            -ContextSize 4096
        if ($script:configPost.Uri -ne "http://127.0.0.1:8080/internal/set") {
            throw "Modern Lemonade config did not use loopback /internal/set"
        }
        if ($script:configPost.Headers.Authorization -ne "Bearer contract-admin-key") {
            throw "Modern Lemonade config did not send the admin Bearer token"
        }
        $postedProperties = @($script:configPost.Body.PSObject.Properties.Name | Sort-Object)
        if (($postedProperties -join ",") -ne "ctx_size,extra_models_dir,llamacpp") {
            throw "Unexpected Lemonade config schema: $($postedProperties -join ",")"
        }
        if ($script:configPost.Body.extra_models_dir -ne $script:expectedModelsDir -or
            $script:configPost.Body.llamacpp.backend -ne "vulkan" -or
            [int]$script:configPost.Body.ctx_size -ne 4096) {
            throw "Lemonade 10.7 config payload values are incorrect"
        }
        $resolvedModernModel = Resolve-ODSLemonadeModelId `
            -Port 8080 -GgufFile "Modern-Model.gguf"
        if ($resolvedModernModel -ne "Modern-Model") {
            throw "Modern Lemonade model ID resolution failed: $resolvedModernModel"
        }
        $legacyFallbackModel = Resolve-ODSLemonadeModelId `
            -Port 8080 -GgufFile "Legacy-Model.gguf" -VersionOverride "10.6.9"
        if ($legacyFallbackModel -ne "extra.Legacy-Model.gguf") {
            throw "Legacy Lemonade model ID fallback failed: $legacyFallbackModel"
        }

        function New-ScheduledTaskAction {
            param($Execute, $Argument, $WorkingDirectory)
            return [pscustomobject]@{
                Execute = $Execute
                Arguments = $Argument
                WorkingDirectory = $WorkingDirectory
            }
        }
        $taskAction = New-ODSLemonadeScheduledTaskAction `
            -Contract $modern -EnvPath (Join-Path $probeRoot ".env") `
            -DiagnosticLogPath (Join-Path $probeRoot "lemonade-launch.log")
        $launcherMatch = [regex]::Match($taskAction.Arguments, "-File\s+`"([^`"]+)`"")
        if ($taskAction.Execute -ne "powershell.exe" -or -not $launcherMatch.Success) {
            throw "Modern Lemonade task must use the secure PowerShell launcher file"
        }
        if ($taskAction.Arguments.Length -gt 512) {
            throw "Modern Lemonade task arguments are too long for standard-user Task Scheduler registration"
        }
        $launcherPath = $launcherMatch.Groups[1].Value
        if (-not (Test-Path -LiteralPath $launcherPath -PathType Leaf)) {
            throw "Modern Lemonade task launcher file was not written"
        }
        $wrapper = Get-Content -LiteralPath $launcherPath -Raw
        if ($wrapper -match [regex]::Escape("contract-admin-key")) {
            throw "Lemonade admin key leaked into the launcher wrapper"
        }
        if ($wrapper -notmatch "LITELLM_LEMONADE_API_KEY" -or
            $wrapper -notmatch "Start-Process -FilePath.*-PassThru") {
            throw "Modern Lemonade task wrapper does not securely supervise the child"
        }
        $tokens = $null
        $parseErrors = $null
        [void][System.Management.Automation.Language.Parser]::ParseInput(
            $wrapper, [ref]$tokens, [ref]$parseErrors
        )
        if (@($parseErrors).Count -gt 0) {
            throw "Generated Lemonade task wrapper has PowerShell parse errors"
        }

        $contractText = Get-Content -LiteralPath (Join-Path $env:ROOT_DIR "installers/windows/lib/backend-contract.ps1") -Raw
        if ($contractText -notmatch "whoami\.exe" -or
            $contractText -notmatch "Translate\(\[System.Security.Principal.SecurityIdentifier\]\)") {
            throw "Interactive scheduled task user resolver does not validate a fully qualified account"
        }
        function Resolve-ODSInteractiveScheduledTaskUser {
            return "STRIXY\conta"
        }
        function New-ScheduledTaskPrincipal {
            param($UserId, $LogonType, $RunLevel)
            return [pscustomobject]@{
                UserId = $UserId
                LogonType = $LogonType
                RunLevel = $RunLevel
            }
        }
        $principal = New-ODSInteractiveScheduledTaskPrincipal -RunLevel Limited
        if ($principal.UserId -ne "STRIXY\conta" -or
            $principal.LogonType -ne "Interactive" -or
            $principal.RunLevel -ne "Limited") {
            throw "Interactive scheduled task principal did not preserve the resolved user and limited token"
        }
    '; then
        pass "backend-contract.ps1: resolves Lemonade and enforces versioned secure launch/config contracts"
    else
        fail "backend-contract.ps1: PowerShell contract failed"
    fi
else
    pass "backend-contract.ps1: runtime test skipped (PowerShell unavailable)"
fi

# ---------------------------------------------------------------------------
# 17. Windows AMD managed Lemonade avoids host port 9000 collision with Whisper
# ---------------------------------------------------------------------------
echo "[contract] Windows AMD managed Lemonade avoids Whisper port 9000"
if grep -q 'Lemonade.*reserves host port 9000' installers/windows/lib/env-generator.ps1 \
    && grep -q 'WHISPER_PORT=$whisperPort' installers/windows/lib/env-generator.ps1 \
    && grep -q '9100' installers/windows/phases/04-requirements.ps1; then
    pass "Windows AMD/Lemonade defaults Whisper to alternate host port"
else
    fail "Windows AMD/Lemonade must avoid Lemonade websocket port collision"
fi

if command -v pwsh >/dev/null 2>&1; then
    _ps_tmp="${TMPDIR:-/tmp}"
    if ROOT_DIR="$ROOT_DIR" TEMP="$_ps_tmp" USERPROFILE="$_ps_tmp" pwsh -NoProfile -Command '
        $ErrorActionPreference = "Stop"
        function Write-AIWarn { param([string]$Message) Write-Host "WARN: $Message" }
        . (Join-Path $env:ROOT_DIR "installers/windows/lib/detection.ps1")
        . (Join-Path $env:ROOT_DIR "installers/windows/lib/env-generator.ps1")
        $installDir = Join-Path $env:TEMP "ods-env-generator-amd-lemonade-contract"
        Remove-Item -LiteralPath $installDir -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Path $installDir -Force | Out-Null
        $tier = @{
            TierName = "Strix Halo"
            LlmModel = "test-model"
            GgufFile = "test.gguf"
            MaxContext = 4096
        }
        New-ODSEnv -InstallDir $installDir -TierConfig $tier -Tier "SH" -GpuBackend "amd" -AmdInferenceRuntime "lemonade" -AmdInferenceLocation "host" | Out-Null
        $envText = Get-Content -LiteralPath (Join-Path $installDir ".env") -Raw
        if ($envText -notmatch "(?m)^ODS_MODE=lemonade\r?$") {
            throw "Expected Windows AMD Lemonade installs to write ODS_MODE=lemonade"
        }
        if ($envText -notmatch "(?m)^LLM_BACKEND=lemonade\r?$") {
            throw "Expected Windows AMD Lemonade installs to write LLM_BACKEND=lemonade"
        }
        $litellmKey = [regex]::Match($envText, "(?m)^LITELLM_KEY=([^\r\n]+)\r?$").Groups[1].Value
        if ([string]::IsNullOrWhiteSpace($litellmKey)) {
            throw "Expected Windows AMD Lemonade installs to generate LITELLM_KEY"
        }
        if ($envText -notmatch "(?m)^HERMES_LLM_BASE_URL=http://litellm:4000/v1\r?$") {
            throw "Expected Windows AMD Lemonade Hermes to route through LiteLLM"
        }
        if ($envText -notmatch "(?m)^HERMES_LLM_API_KEY=$([regex]::Escape($litellmKey))\r?$") {
            throw "Expected Windows AMD Lemonade Hermes to authenticate with LITELLM_KEY"
        }
        if ($envText -match "(?m)^HERMES_LLM_BASE_URL=http://host\.docker\.internal:8080/api/v1$") {
            throw "Windows AMD Lemonade Hermes must not stream directly against native Lemonade"
        }
        if ($envText -notmatch "(?m)^WHISPER_PORT=9100\r?$") {
            throw "Expected WHISPER_PORT=9100 for Windows AMD managed Lemonade"
        }
        $litellmConfig = Join-Path (Join-Path (Join-Path $installDir "config") "litellm") "lemonade.yaml"
        if (-not (Test-Path -LiteralPath $litellmConfig)) {
            throw "Expected Windows AMD Lemonade installs to generate config/litellm/lemonade.yaml"
        }
        $litellmText = Get-Content -LiteralPath $litellmConfig -Raw
        if ($litellmText -notmatch "api_base: http://host\.docker\.internal:8080/api/v1") {
            throw "Expected Windows AMD Lemonade LiteLLM config to route through host.docker.internal:8080/api/v1"
        }
        if ($litellmText -match "api_base: http://llama-server:8080/api/v1") {
            throw "Windows AMD Lemonade LiteLLM config must not route to in-container llama-server"
        }
        if ($litellmText -notmatch "(?m)^  request_timeout: 900\r?$" -or $litellmText -notmatch "(?m)^  stream_timeout: 900\r?$") {
            throw "Windows AMD Lemonade LiteLLM config must keep long-model proxy timeouts at 900s"
        }

        Set-Content -LiteralPath (Join-Path $installDir ".env") -Value "WHISPER_PORT=9000`n" -NoNewline
        New-ODSEnv -InstallDir $installDir -TierConfig $tier -Tier "SH" -GpuBackend "amd" -AmdInferenceRuntime "lemonade" -AmdInferenceLocation "host" | Out-Null
        $envText = Get-Content -LiteralPath (Join-Path $installDir ".env") -Raw
        if ($envText -notmatch "(?m)^WHISPER_PORT=9100\r?$") {
            throw "Expected unsafe WHISPER_PORT=9000 to be remapped for Lemonade"
        }

        Set-Content -LiteralPath (Join-Path $installDir ".env") -Value "WHISPER_PORT=9200`n" -NoNewline
        New-ODSEnv -InstallDir $installDir -TierConfig $tier -Tier "SH" -GpuBackend "amd" -AmdInferenceRuntime "lemonade" -AmdInferenceLocation "host" | Out-Null
        $envText = Get-Content -LiteralPath (Join-Path $installDir ".env") -Raw
        if ($envText -notmatch "(?m)^WHISPER_PORT=9200\r?$") {
            throw "Expected existing WHISPER_PORT override to be preserved"
        }
    '; then
        pass "env-generator.ps1: AMD Lemonade uses 9100 and preserves explicit overrides"
    else
        fail "env-generator.ps1: AMD Lemonade Whisper port contract failed"
    fi
else
    pass "env-generator.ps1: runtime port test skipped (pwsh unavailable)"
fi

# ---------------------------------------------------------------------------
# 17b. Windows local mode always generates LiteLLM's local.yaml
# ---------------------------------------------------------------------------
echo "[contract] Windows local mode generates LiteLLM config"
if command -v pwsh >/dev/null 2>&1; then
    _ps_tmp="${TMPDIR:-/tmp}"
    if ROOT_DIR="$ROOT_DIR" TEMP="$_ps_tmp" USERPROFILE="$_ps_tmp" pwsh -NoProfile -Command '
        $ErrorActionPreference = "Stop"
        function Write-AIWarn { param([string]$Message) Write-Host "WARN: $Message" }
        . (Join-Path $env:ROOT_DIR "installers/windows/lib/detection.ps1")
        . (Join-Path $env:ROOT_DIR "installers/windows/lib/env-generator.ps1")
        $installDir = Join-Path $env:TEMP "ods-env-generator-windows-local-contract"
        Remove-Item -LiteralPath $installDir -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Path $installDir -Force | Out-Null
        $litellmDir = Join-Path (Join-Path $installDir "config") "litellm"
        New-Item -ItemType Directory -Path (Join-Path $litellmDir "local.yaml") -Force | Out-Null
        $tier = @{
            TierName = "Windows NVIDIA"
            LlmModel = "test-model"
            GgufFile = "test-local.gguf"
            MaxContext = 4096
        }
        New-ODSEnv -InstallDir $installDir -TierConfig $tier -Tier "3" -GpuBackend "nvidia" -ODSMode "local" | Out-Null
        $localConfig = Join-Path $litellmDir "local.yaml"
        if (-not (Test-Path -LiteralPath $localConfig -PathType Leaf)) {
            throw "Expected Windows local installs to generate config/litellm/local.yaml as a file"
        }
        $localText = Get-Content -LiteralPath $localConfig -Raw
        if ($localText -notmatch "model: openai/test-local\.gguf") {
            throw "Expected local LiteLLM config to route the selected GGUF model"
        }
        if ($localText -notmatch "api_base: http://llama-server:8080/v1") {
            throw "Expected Windows NVIDIA local LiteLLM config to route through llama-server:8080/v1"
        }
        if ($localText -notmatch "(?m)^  request_timeout: 900\r?$" -or $localText -notmatch "(?m)^  stream_timeout: 900\r?$") {
            throw "Windows local LiteLLM config must keep long-model proxy timeouts at 900s"
        }
        if ($localText -notmatch "(?m)^general_settings:\r?$" -or $localText -notmatch "(?m)^  master_key: os\.environ/LITELLM_MASTER_KEY\r?$") {
            throw "Windows local LiteLLM config must enforce the generated LiteLLM master key"
        }
    '; then
        pass "env-generator.ps1: Windows local writes local.yaml and repairs malformed bind-mount directories"
    else
        fail "env-generator.ps1: Windows local LiteLLM config contract failed"
    fi
else
    pass "env-generator.ps1: Windows local LiteLLM runtime test skipped (pwsh unavailable)"
fi

# ---------------------------------------------------------------------------
# 17c. Linux AMD/Lemonade avoids host port 9000 collision with Whisper
# ---------------------------------------------------------------------------
echo "[contract] Linux AMD/Lemonade avoids Whisper port 9000"
if grep -q 'AMD/Lemonade detected; reserving host port 9000' installers/phases/04-requirements.sh \
    && grep -q 'AMD/Lemonade detected; Whisper reassigned to host port' installers/phases/06-directories.sh \
    && grep -q 'WHISPER_PORT_VALUE="9100"' installers/phases/06-directories.sh \
    && grep -q 'WHISPER_PORT=${WHISPER_PORT_VALUE}' installers/phases/06-directories.sh; then
    pass "Linux AMD/Lemonade defaults Whisper to alternate host port"
else
    fail "Linux AMD/Lemonade must avoid Lemonade host port 9000 collision"
fi

if grep -q 'SERVICE_PORTS\[whisper\]=9100' installers/phases/04-requirements.sh \
    && grep -q 'SERVICE_PORTS\[whisper\]="\$WHISPER_PORT_VALUE"' installers/phases/06-directories.sh; then
    pass "Linux installer aligns Whisper port checks with generated .env"
else
    fail "Linux installer must align Whisper port checks/health with generated .env"
fi

if grep -q 'backend-contract.ps1' installers/windows/ods.ps1 \
    && grep -q 'Resolve-ODSLemonadeExe' installers/windows/ods.ps1; then
    pass "ods.ps1 resolves Lemonade across both Program Files roots"
else
    fail "ods.ps1 must use Lemonade resolver for installed CLI commands"
fi

# ---------------------------------------------------------------------------
# 17d. Windows AMD falls back when managed Lemonade never becomes healthy
# ---------------------------------------------------------------------------
echo "[contract] Windows AMD Lemonade health failure falls back to llama-server"
if grep -q 'function Stop-ODSWindowsLemonadeProcesses' installers/windows/install-windows.ps1 \
    && grep -q 'function Get-ODSPriorLemonadeTaskName' installers/windows/install-windows.ps1 \
    && grep -q '"ODSLemonadeRuntime"' installers/windows/install-windows.ps1 \
    && grep -q '\$taskNames = @(\$taskName, (Get-ODSPriorLemonadeTaskName))' installers/windows/install-windows.ps1 \
    && ! grep -q '_managedPort' installers/windows/install-windows.ps1 \
    && grep -q 'Falling back to native llama-server (Vulkan)' installers/windows/install-windows.ps1 \
    && grep -q '\$useLemonade = \$false' installers/windows/install-windows.ps1 \
    && grep -q 'if (-not \$useLemonade)' installers/windows/install-windows.ps1 \
    && ! grep -q 'throw "Lemonade \$launchMethod started but no Lemonade process was found' installers/windows/install-windows.ps1; then
    pass "install-windows.ps1: exact stale tasks are removed and unhealthy Lemonade falls back"
else
    fail "install-windows.ps1: Windows AMD must not block Compose behind an unhealthy Lemonade endpoint"
fi
if command -v pwsh >/dev/null 2>&1; then
    if pwsh -NoProfile -File tests/contracts/test-windows-lemonade-task-cleanup.ps1; then
        pass "install-windows.ps1: managed scheduled-task cleanup behavior"
    else
        fail "install-windows.ps1: managed scheduled-task cleanup behavior failed"
    fi
else
    pass "install-windows.ps1: scheduled-task cleanup runtime test skipped (pwsh unavailable)"
fi

# ---------------------------------------------------------------------------
# 18. Windows image validation treats missing local images as probe misses
# ---------------------------------------------------------------------------
echo "[contract] Windows Docker image validation is stderr-safe"
if grep -A16 'function Test-ODSDockerImageAvailable' installers/windows/install-windows.ps1 \
    | grep -q 'SilentlyContinue' \
    && grep -A20 'function Test-ODSDockerImageAvailable' installers/windows/install-windows.ps1 \
        | grep -q 'finally' \
    && grep -q 'manifest inspect' installers/windows/install-windows.ps1 \
    && grep -A24 'function Invoke-ODSWindowsDockerPullWithRetry' installers/windows/install-windows.ps1 \
        | grep -q 'SilentlyContinue' \
    && grep -A30 'function Invoke-ODSWindowsDockerPullWithRetry' installers/windows/install-windows.ps1 \
        | grep -q 'finally'; then
    pass "install-windows.ps1: image probes restore ErrorActionPreference"
else
    fail "install-windows.ps1: Docker image probes must not abort on missing local images"
fi
if command -v pwsh >/dev/null 2>&1; then
    if pwsh -NoProfile -File tests/contracts/test-windows-docker-pull.ps1; then
        pass "install-windows.ps1: Docker pull progress and result runtime contract"
    else
        fail "install-windows.ps1: Docker pull progress and result runtime contract failed"
    fi
else
    pass "install-windows.ps1: Docker pull runtime test skipped (pwsh unavailable)"
fi

# ---------------------------------------------------------------------------
# 19. AMD Docker 29.3 downgrade is Debian-aware and sudo-aware
# ---------------------------------------------------------------------------
echo "[contract] AMD Docker downgrade handles Debian and inactive docker group"
docker_phase="installers/phases/05-docker.sh"
if grep -q 'apt-cache madison docker-ce' "$docker_phase"; then
    pass "05-docker.sh: apt downgrade resolves the installable Docker CE version"
else
    fail "05-docker.sh: apt downgrade must resolve Docker CE 29.2.1 via apt-cache madison"
fi
if grep -q 'docker-ce=5:29\.2\.1-1~ubuntu' "$docker_phase" \
    || grep -q 'docker-ce-cli=5:29\.2\.1-1~ubuntu' "$docker_phase"; then
    fail "05-docker.sh: apt downgrade must not hardcode Ubuntu package versions"
else
    pass "05-docker.sh: apt downgrade no longer hardcodes Ubuntu package versions"
fi
if awk '/_docker_read_server_version\(\)/,/^}/' "$docker_phase" \
    | grep -q 'ods_sudo docker version'; then
    pass "05-docker.sh: Docker server version probe falls back through ods_sudo"
else
    fail "05-docker.sh: AMD downgrade must detect Docker 29.3 when docker group membership is not active"
fi
if awk '/_docker_server_version_for_amd_downgrade\(\)/,/^}/' "$docker_phase" \
    | grep -q 'systemctl start docker'; then
    pass "05-docker.sh: AMD downgrade starts docker before giving up on version detection"
else
    fail "05-docker.sh: AMD downgrade must retry after starting docker on systemd hosts"
fi
if awk '/Docker 29\.3\.x has a bug/,/fi$/' "$docker_phase" \
    | grep -q '_docker_server_version_for_amd_downgrade'; then
    pass "05-docker.sh: AMD downgrade uses the sudo-aware version probe"
else
    fail "05-docker.sh: AMD downgrade still uses a bare docker version probe"
fi
_amd_docker_probe_tmp="$(mktemp -d)"
mkdir -p "$_amd_docker_probe_tmp/bin"
cat > "$_amd_docker_probe_tmp/bin/docker" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "version" && "${2:-}" == "--format" ]]; then
    if [[ "${ODS_FAKE_DOCKER_SUDO:-}" == "1" ]]; then
        echo "29.3.0"
        exit 0
    fi
    exit 1
fi
exit 99
EOF
cat > "$_amd_docker_probe_tmp/bin/sudo" <<'EOF'
#!/usr/bin/env bash
ODS_FAKE_DOCKER_SUDO=1 "$@"
EOF
chmod +x "$_amd_docker_probe_tmp/bin/docker" "$_amd_docker_probe_tmp/bin/sudo"
_docker_probe_func="$(awk '
    /_docker_read_server_version\(\)/,/^}/ {print}
    /_docker_server_version_for_amd_downgrade\(\)/,/^}/ {print}
' "$docker_phase")"
if _probe_output="$(
    PATH="$_amd_docker_probe_tmp/bin:$PATH" bash -c '
        set -euo pipefail
        ods_sudo() { ODS_FAKE_DOCKER_SUDO=1 "$@"; }
        eval "$1"
        _docker_server_version_for_amd_downgrade
    ' bash "$_docker_probe_func"
)"; then
    if [[ "$_probe_output" == "29.3.0" ]]; then
        pass "05-docker.sh: sudo fallback detects Docker 29.3 when bare docker is denied"
    else
        fail "05-docker.sh: sudo fallback returned unexpected Docker version: $_probe_output"
    fi
else
    fail "05-docker.sh: sudo fallback did not recover Docker server version"
fi
rm -rf "$_amd_docker_probe_tmp"
unset _amd_docker_probe_tmp _docker_probe_func _probe_output
_sample_debian_madison=$'   docker-ce | 5:29.3.0-1~debian.13~trixie | https://download.docker.com/linux/debian trixie/stable amd64 Packages\n   docker-ce | 5:29.2.1-1~debian.13~trixie | https://download.docker.com/linux/debian trixie/stable amd64 Packages'
_resolved_debian_2921="$(awk '$3 ~ /(^|:)29\.2\.1/ {print $3; exit}' <<<"$_sample_debian_madison")"
if [[ "$_resolved_debian_2921" == "5:29.2.1-1~debian.13~trixie" ]]; then
    pass "apt-cache madison parser resolves Debian trixie Docker CE 29.2.1"
else
    fail "apt-cache madison parser did not resolve the Debian trixie Docker CE 29.2.1 version"
fi
unset _sample_debian_madison _resolved_debian_2921 docker_phase

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "AMD/Lemonade contracts: $PASS passed, $FAIL failed"
if [[ $FAIL -gt 0 ]]; then
    exit 1
fi
