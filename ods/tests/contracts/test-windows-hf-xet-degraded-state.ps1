$ErrorActionPreference = "Stop"

# Degraded-capability marker contract for huggingface_hub[hf_xet].
#
# When the Windows installer cannot provision the Xet downloader dependency it
# used to only print a warning — the install looked fully healthy and later
# model-manager downloads failed with no durable trace of the cause. The fix
# persists a marker under data/config, surfaces it in the readiness summary,
# and clears it when a rerun provisions the dependency successfully.

$root = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$libPath = Join-Path $root "installers/windows/lib/install-state.ps1"
$phasePath = Join-Path $root "installers/windows/phases/07-devtools.ps1"
$summaryPath = Join-Path $root "installers/windows/lib/readiness-summary.ps1"
$installerPath = Join-Path $root "installers/windows/install-windows.ps1"
$uiPath = Join-Path $root "installers/windows/lib/ui.ps1"

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw "FAILED: $Message" }
    Write-Host "[PASS] $Message"
}

function Get-FunctionExtents {
    param([string]$Path, [string[]]$Names)
    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
    if ($errors.Count -gt 0) { throw "Could not parse ${Path}: $($errors[0].Message)" }
    $extents = @()
    foreach ($name in $Names) {
        $fn = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
        if (-not $fn) { throw "$name not found in $Path" }
        $extents += $fn.Extent.Text
    }
    return $extents
}

# Invoke at script scope so the functions outlive the extraction helper.
@(Get-FunctionExtents -Path $libPath -Names @(
    "Get-ODSHfXetDegradedMarkerPath",
    "Write-ODSHfXetDegradedMarker",
    "Remove-ODSHfXetDegradedMarker",
    "Test-ODSHfXetDegraded"
)) + @(Get-FunctionExtents -Path $summaryPath -Names @("Write-ODSInstallReadinessSummary")) |
    ForEach-Object { Invoke-Expression $_ }

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("ods-hf-xet-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
try {
    # --- Behavioural: marker lifecycle on a scratch install dir ---------------
    Assert-True (-not (Test-ODSHfXetDegraded -InstallDir $tempRoot)) "fresh install dir reports not degraded"

    $markerPath = Write-ODSHfXetDegradedMarker -InstallDir $tempRoot
    Assert-True (Test-Path -LiteralPath $markerPath -PathType Leaf) "marker file is written under data/config"
    Assert-True (Test-ODSHfXetDegraded -InstallDir $tempRoot) "degraded capability is detectable after write"

    $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
    Assert-True ($marker.capability -eq "huggingface-xet-downloader") "marker names the degraded capability"
    Assert-True ($marker.available -eq $false) "marker records capability as unavailable"
    Assert-True ($marker.remediation.Contains("huggingface_hub[hf_xet]")) "marker carries the dependency-specific remediation"
    Assert-True (-not [string]::IsNullOrWhiteSpace($marker.degraded_since)) "marker records when degradation was detected"

    Remove-ODSHfXetDegradedMarker -InstallDir $tempRoot
    Assert-True (-not (Test-ODSHfXetDegraded -InstallDir $tempRoot)) "a recovered rerun clears the stale marker"
}
finally {
    Remove-Item -Path $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

# --- Behavioural: readiness summary surfaces non-HTTP findings --------------
$summary = Write-ODSInstallReadinessSummary -Checks @(@{}) `
    -ExtraAttention @("HF Xet downloader           degraded - test") -PassThru
Assert-True (-not $summary.AllReady) "readiness summary treats a degraded capability as not all-ready"

# --- Contract: the failure path persists the marker, success clears it ------
$phaseText = [System.IO.File]::ReadAllText($phasePath)
Assert-True ($phaseText -match 'Write-ODSHfXetDegradedMarker -InstallDir \$installDir') "07-devtools persists the degraded marker when the dependency install fails"
Assert-True ($phaseText -match 'Remove-ODSHfXetDegradedMarker -InstallDir \$installDir') "07-devtools clears the marker when the dependency install succeeds"

$installerText = [System.IO.File]::ReadAllText($installerPath)
Assert-True ($installerText -match 'install-state\.ps1') "install-windows sources the install-state library"
Assert-True ($installerText -match 'Test-ODSHfXetDegraded -InstallDir \$installDir') "readiness block reads the persisted marker"
Assert-True ($installerText -match '-ExtraAttention \$extraReadinessAttention') "readiness summary receives the degraded-capability attention item"

$summaryText = [System.IO.File]::ReadAllText($summaryPath)
Assert-True ($summaryText -match '\[string\[\]\]\$ExtraAttention') "readiness summary accepts non-HTTP attention items"

$uiText = [System.IO.File]::ReadAllText($uiPath)
Assert-True ($uiText.Contains("the retry will likely fail. Fix with: python -m pip install --user 'huggingface_hub[hf_xet]>=0.27'")) "HF fallback path reports the dependency-specific remediation instead of failing silently"

Write-Host ""
Write-Host "All HF Xet degraded-state contracts passed."
