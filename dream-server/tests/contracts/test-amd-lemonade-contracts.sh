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
# 2. Lemonade entrypoint uses absolute path
# ---------------------------------------------------------------------------
echo "[contract] Lemonade entrypoint uses absolute path"
if grep -q '/opt/lemonade/lemonade-server' docker-compose.amd.yml; then
    pass "entrypoint: absolute path /opt/lemonade/lemonade-server"
else
    fail "entrypoint: must use absolute path /opt/lemonade/lemonade-server"
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
    else
        pass "lemonade.yaml: no master_key"
    fi
else
    fail "lemonade.yaml: file missing"
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
# 8. Context size is configurable
# ---------------------------------------------------------------------------
echo "[contract] Lemonade context size configurable"
if grep -q 'LEMONADE_CTX_SIZE' docker-compose.amd.yml; then
    pass "CTX_SIZE passed to Lemonade container"
else
    fail "docker-compose.amd.yml must pass LEMONADE_CTX_SIZE"
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
# 10. Schema allows DREAM_MODE=lemonade
# ---------------------------------------------------------------------------
echo "[contract] .env schema allows lemonade mode"
if grep -q '"lemonade"' .env.schema.json; then
    pass ".env.schema.json: lemonade in DREAM_MODE enum"
else
    fail ".env.schema.json: must include lemonade in DREAM_MODE enum"
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
if command -v pwsh >/dev/null 2>&1; then
    _ps_tmp="${TMPDIR:-/tmp}"
    if ROOT_DIR="$ROOT_DIR" AMD_LEMONADE_IMAGE="$AMD_LEMONADE_IMAGE" TEMP="$_ps_tmp" ProgramFiles="$_ps_tmp" USERPROFILE="$_ps_tmp" pwsh -NoProfile -Command '
        $ErrorActionPreference = "Stop"
        . (Join-Path $env:ROOT_DIR "installers/windows/lib/backend-contract.ps1")
        $runtime = Get-DreamAmdLemonadeRuntime -RootPath $env:ROOT_DIR
        if ($runtime.container_image -ne $env:AMD_LEMONADE_IMAGE) {
            throw "Unexpected container image: $($runtime.container_image)"
        }
        $failed = $false
        try {
            Get-DreamAmdLemonadeRuntime -RootPath (Join-Path $env:ROOT_DIR "missing-root") | Out-Null
        } catch {
            $failed = $true
        }
        if (-not $failed) {
            throw "Expected missing root to fail"
        }
        . (Join-Path $env:ROOT_DIR "installers/windows/lib/constants.ps1")
    '; then
        pass "backend-contract.ps1: reads explicit root and constants.ps1 stays standalone"
    else
        fail "backend-contract.ps1: PowerShell contract failed"
    fi
else
    pass "backend-contract.ps1: runtime test skipped (pwsh unavailable)"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "AMD/Lemonade contracts: $PASS passed, $FAIL failed"
if [[ $FAIL -gt 0 ]]; then
    exit 1
fi
