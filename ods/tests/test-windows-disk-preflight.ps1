$ErrorActionPreference = "Stop"

# Windows disk preflight contract.
#
# The per-tier disk minimums in phase 04 were sized for the tier map's own
# model. The catalog selector can promote a larger one before anything is
# downloaded, so the requirement has to follow the selected model — the same
# recheck installers/phases/04-requirements.sh and installers/macos/
# install-macos.sh already perform.

$root = Split-Path -Parent $PSScriptRoot
$phasePath = Join-Path $root "installers\windows\phases\04-requirements.ps1"

$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $phasePath,
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -gt 0) {
    throw "Phase 04 failed to parse: $($errors[0].Message)"
}

$functionAst = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq "Get-WindowsModelDiskRequirementGB"
}, $true)
if (-not $functionAst) { throw "Function not found: Get-WindowsModelDiskRequirementGB" }
. ([scriptblock]::Create($functionAst.Extent.Text))

function Assert-Equal {
    param($Actual, $Expected, [string]$Label)
    if ($Actual -ne $Expected) {
        throw "$Label`: expected $Expected, got $Actual"
    }
    Write-Host "[PASS] $Label"
}

# No selected size (CLOUD, or the selector disabled) keeps the tier minimum.
Assert-Equal (Get-WindowsModelDiskRequirementGB -ModelSizeMB 0 -TierMinimumGB 35) 35 `
    "unknown model size keeps the tier minimum"
Assert-Equal (Get-WindowsModelDiskRequirementGB -ModelSizeMB -1 -TierMinimumGB 15) 15 `
    "negative model size keeps the tier minimum"

# A model that fits inside the tier minimum must not lower the gate.
Assert-Equal (Get-WindowsModelDiskRequirementGB -ModelSizeMB 5760 -TierMinimumGB 35) 35 `
    "small model does not weaken the tier minimum"

# Tier 3 minimum is 35GB; a 24GB pick needs 24 + 15 = 39GB.
Assert-Equal (Get-WindowsModelDiskRequirementGB -ModelSizeMB 24576 -TierMinimumGB 35) 39 `
    "tier 3 requirement follows a 24GB model"

# Tier 4 minimum is 50GB; the 48500MB coder-next entry needs 48 + 15 = 63GB.
Assert-Equal (Get-WindowsModelDiskRequirementGB -ModelSizeMB 48500 -TierMinimumGB 50) 63 `
    "tier 4 requirement follows a 48500MB model"

# Partial GB rounds up, matching (SIZE + 1023) / 1024 on Linux.
Assert-Equal (Get-WindowsModelDiskRequirementGB -ModelSizeMB 21110 -TierMinimumGB 30) 36 `
    "model size rounds up to whole GB"

$phaseText = Get-Content -LiteralPath $phasePath -Raw

# The gap this closes: the tier table's own number would have admitted a model
# that cannot fit. Read tier 4's minimum out of the phase so the check tracks
# the table instead of a copy of it.
$phaseLines = Get-Content -LiteralPath $phasePath
$diskSwitchStart = ($phaseLines | Select-String -SimpleMatch '$_minDiskGB = switch' | Select-Object -First 1).LineNumber
if (-not $diskSwitchStart) { throw "Phase 04 no longer declares a per-tier disk switch" }
$tier4MinimumGB = 0
foreach ($line in $phaseLines[$diskSwitchStart..([Math]::Min($diskSwitchStart + 12, $phaseLines.Count - 1))]) {
    if ($line -match '^\s*"4"\s*\{\s*(\d+)\s*\}') { $tier4MinimumGB = [int]$Matches[1]; break }
}
if ($tier4MinimumGB -le 0) {
    throw "Phase 04 no longer declares a tier 4 disk minimum in the expected form"
}
$tier4Required = Get-WindowsModelDiskRequirementGB -ModelSizeMB 48500 -TierMinimumGB $tier4MinimumGB
if ($tier4Required -le $tier4MinimumGB) {
    throw ("Tier 4 minimum ${tier4MinimumGB}GB still admits a 48500MB model " +
           "(requirement resolved to ${tier4Required}GB)")
}
Write-Host "[PASS] tier 4 minimum (${tier4MinimumGB}GB) is raised to ${tier4Required}GB for a 48500MB model"

# The phase must feed the selected model size in, not just declare the helper.
if ($phaseText -notmatch [regex]::Escape('$tierConfig.ModelSizeMB')) {
    throw "Phase 04 does not read the selected model size from the tier config"
}
if ($phaseText -notmatch [regex]::Escape('Get-WindowsModelDiskRequirementGB')) {
    throw "Phase 04 does not apply the model-aware disk requirement"
}
Write-Host "[PASS] phase 04 applies the requirement to the selected model"

Write-Host ""
Write-Host "All Windows disk preflight contract checks passed."
