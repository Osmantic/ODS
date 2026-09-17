$ErrorActionPreference = "Stop"
. (Resolve-Path (Join-Path $PSScriptRoot "..\installers\windows\lib\install-estimate.ps1"))
$known = Get-ODSWindowsInstallSizeEstimate -TierConfig @{ ModelSizeMB = 2048 } -MinimumDiskGB 25 -EnabledOptionalFeatures @("voice", "hermes", "comfyui", "langfuse")
if ($known.modelDownloadGB -ne 2 -or $known.buildAndDataGB -ne 14 -or $known.requiredFreeDiskGB -ne 25) { throw "known estimate incorrect" }
$unknown = Get-ODSWindowsInstallSizeEstimate -TierConfig @{} -MinimumDiskGB 15
if ($null -ne $unknown.modelDownloadGB -or $unknown.buildAndDataGB -ne 2) { throw "unknown estimate incorrect" }
Write-Output "PASS: install size estimate reports known and unknown model metadata"
