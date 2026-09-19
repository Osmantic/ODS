# ============================================================================
# ODS Windows -- degraded-capability install state
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Persist capabilities that could not be provisioned during install
#          so the readiness summary and later operations can report them as
#          degraded instead of letting the install look fully healthy.
# Requires: nothing (self-contained file IO).
# ============================================================================

function Get-ODSHfXetDegradedMarkerPath {
    param([Parameter(Mandatory = $true)][string]$InstallDir)
    return (Join-Path $InstallDir "data\config\hf-xet-degraded.json")
}

function Write-ODSHfXetDegradedMarker {
    # Records that huggingface_hub[hf_xet] could not be installed. The marker
    # is intentionally a plain JSON file under data/config so any later tool
    # (readiness summary, install report, model manager) can read it without
    # installer internals.
    param([Parameter(Mandatory = $true)][string]$InstallDir)

    $markerPath = Get-ODSHfXetDegradedMarkerPath -InstallDir $InstallDir
    $stateDir = Split-Path -Parent $markerPath
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null

    $marker = [ordered]@{
        capability      = "huggingface-xet-downloader"
        available       = $false
        degraded_since  = (Get-Date).ToString("o")
        remediation     = "python -m pip install --user 'huggingface_hub[hf_xet]>=0.27'"
        impact          = "Model manager downloads may fail on Xet-backed Hugging Face models."
    }
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($markerPath, ($marker | ConvertTo-Json) + "`n", $utf8NoBom)
    return $markerPath
}

function Remove-ODSHfXetDegradedMarker {
    # Called when the dependency provisions successfully so a marker left by an
    # earlier failed run cannot keep reporting a recovered capability.
    param([Parameter(Mandatory = $true)][string]$InstallDir)

    $markerPath = Get-ODSHfXetDegradedMarkerPath -InstallDir $InstallDir
    if (Test-Path -LiteralPath $markerPath -PathType Leaf) {
        Remove-Item -LiteralPath $markerPath -Force
    }
}

function Test-ODSHfXetDegraded {
    param([Parameter(Mandatory = $true)][string]$InstallDir)
    return (Test-Path -LiteralPath (Get-ODSHfXetDegradedMarkerPath -InstallDir $InstallDir) -PathType Leaf)
}
