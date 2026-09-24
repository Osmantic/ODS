$ErrorActionPreference = "Stop"

# Regression test: when the Windows installer enables ComfyUI it must write
# ENABLE_IMAGE_GENERATION=true so the Open WebUI container exposes the Images
# feature (docker-compose.base.yml defaults it to false). Linux writes
# ENABLE_IMAGE_GENERATION=${ENABLE_COMFYUI:-true}; the Windows generator
# dropped the key entirely, so a -Comfyui install left image generation off.

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $repoRoot "installers/windows/lib/env-generator.ps1")

# New-ODSEnv calls Write-AIWarn for non-fatal ACL issues and Get-LlamaCpuBudget
# for the CPU cap; stub the cross-lib helpers (detection.ps1/ui.ps1 are out of
# scope for this contract test).
if (-not (Get-Command Write-AIWarn -ErrorAction SilentlyContinue)) {
    function Write-AIWarn { param([string]$Message) Write-Host "WARN: $Message" }
}
if (-not (Get-Command Get-LlamaCpuBudget -ErrorAction SilentlyContinue)) {
    function Get-LlamaCpuBudget { param([string]$GpuBackend) return @{ Available = 4; Limit = 4; Reservation = 1 } }
}

$failures = 0
function Assert-Equal([string]$Label, [string]$Expected, [string]$Actual) {
    if ($Expected -eq $Actual) { Write-Host "PASS: $Label" }
    else { $script:failures++; Write-Host "FAIL: $Label expected=[$Expected] actual=[$Actual]" }
}
function Get-EnvValue([string]$EnvPath, [string]$Key) {
    foreach ($line in (Get-Content $EnvPath)) {
        if ($line -match "^$([regex]::Escape($Key))=(.*)$") { return $Matches[1] }
    }
    return $null
}

$tierConfig = @{ TierName = "Tier 1"; LlmModel = "test-model"; GgufFile = "test.gguf"; MaxContext = 8192 }
$root = Join-Path ([IO.Path]::GetTempPath()) ("ods-imggen-" + [guid]::NewGuid().ToString("N"))

try {
    # ComfyUI enabled -> Open WebUI Images on.
    $onDir = Join-Path $root "on"
    New-Item -ItemType Directory -Force -Path $onDir | Out-Null
    New-ODSEnv -InstallDir $onDir -TierConfig $tierConfig -Tier "1" -GpuBackend "nvidia" -EnableComfyui $true | Out-Null
    Assert-Equal "comfyui enabled" "true" (Get-EnvValue (Join-Path $onDir ".env") "ENABLE_IMAGE_GENERATION")

    # Default install -> explicitly false (compose default is also false, but
    # the generated .env must state the choice so operators can flip it).
    $offDir = Join-Path $root "off"
    New-Item -ItemType Directory -Force -Path $offDir | Out-Null
    New-ODSEnv -InstallDir $offDir -TierConfig $tierConfig -Tier "1" -GpuBackend "nvidia" | Out-Null
    Assert-Equal "comfyui disabled" "false" (Get-EnvValue (Join-Path $offDir ".env") "ENABLE_IMAGE_GENERATION")

    # A -NoComfyui rerun over an enabled install must flip the key back off —
    # the flag-derived value is written unconditionally like Linux phase 06.
    New-ODSEnv -InstallDir $onDir -TierConfig $tierConfig -Tier "1" -GpuBackend "nvidia" -EnableComfyui $false | Out-Null
    Assert-Equal "rerun without comfyui flips off" "false" (Get-EnvValue (Join-Path $onDir ".env") "ENABLE_IMAGE_GENERATION")
} finally {
    Remove-Item -Recurse -Force $root -ErrorAction SilentlyContinue
}

if ($failures -gt 0) { exit 1 }
Write-Host "All ENABLE_IMAGE_GENERATION tests passed."
