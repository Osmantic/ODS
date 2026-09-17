$ErrorActionPreference = "Stop"
$phase = Join-Path $PSScriptRoot "..\installers\windows\phases\03-features.ps1"

function Invoke-FeatureCase {
    param([switch]$Voice)
    & {
        param($phasePath, $voiceEnabled)
        function Write-Phase { param($Phase, $Total, $Name, $Estimate) }
        function Write-AIWarn { param($Message) }
        function Write-AI { param($Message) }
        function Write-InfoBox { param($Label, $Value) }
        $voiceFlag = [bool]$voiceEnabled; $workflowsFlag = $false; $ragFlag = $false
        $recommendedFlag = $false; $hermesFlag = $false; $openClawFlag = $false
        $allFlag = $false; $noRecommendedFlag = $false; $noHermesFlag = $false
        $noComfyuiFlag = $false; $langfuseFlag = $false; $noLangfuseFlag = $false
        $nonInteractive = $true; $dryRun = $true; $selectedTier = "1"
        $coreOnlyFlag = $false; $cloudMode = $false
        $gpuInfo = @{ Backend = "nvidia" }; $tierConfig = @{ MaxContext = 4096 }
        . $phasePath
        [pscustomobject]@{
            Voice = $enableVoice; Workflows = $enableWorkflows; Rag = $enableRag
            Recommended = $enableRecommended; Hermes = $enableHermes
            DeepResearch = $enableDeepResearch; PrivacyShield = $enablePrivacyShield
        }
    } $phase $Voice.IsPresent
}

$defaults = Invoke-FeatureCase
if ($defaults.Voice -or $defaults.Workflows -or $defaults.Rag -or $defaults.Recommended -or
    $defaults.Hermes -or $defaults.DeepResearch -or $defaults.PrivacyShield) {
    throw "Tier 1 noninteractive defaults were not Core Only"
}
$optIn = Invoke-FeatureCase -Voice
if (-not $optIn.Voice) { throw "explicit Voice opt-in was suppressed" }
Write-Output "PASS: Tier 1 noninteractive Core Only defaults and explicit opt-in"
