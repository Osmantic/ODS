function Get-ODSWindowsInstallSizeEstimate {
    param(
        [Parameter(Mandatory = $true)][hashtable]$TierConfig,
        [Parameter(Mandatory = $true)][int]$MinimumDiskGB,
        [string[]]$EnabledOptionalFeatures = @()
    )

    $modelGB = $null
    if ($TierConfig.ContainsKey("ModelSizeMB") -and [int]$TierConfig.ModelSizeMB -gt 0) {
        $modelGB = [math]::Round(([double]$TierConfig.ModelSizeMB / 1024), 1)
    }
    $featureCosts = @{
        voice = 2.0; workflows = 1.5; rag = 1.5; recommended = 3.0
        hermes = 1.5; openclaw = 1.5; comfyui = 8.0; deepResearch = 2.5
        privacyShield = 1.0; langfuse = 0.5; braveSearch = 0.5
        odsProxy = 0.5; remoteAccess = 0.5
    }
    $buildGB = 2.0
    foreach ($feature in @($EnabledOptionalFeatures | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })) {
        if ($featureCosts.ContainsKey($feature)) { $buildGB += [double]$featureCosts[$feature] }
    }
    $buildGB = [math]::Round($buildGB, 1)
    [ordered]@{
        modelDownloadGB = $modelGB
        buildAndDataGB = $buildGB
        requiredFreeDiskGB = $MinimumDiskGB
        estimateBasis = "model metadata when available; conservative container/data allowance otherwise"
    }
}
