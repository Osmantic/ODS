# ============================================================================
# Dream Server Windows Installer -- Backend Contract Loader
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Read backend contracts from an explicit Dream Server root path.
# ============================================================================

function Get-DreamBackendContract {
    <#
    .SYNOPSIS
        Read a backend contract JSON file from a known Dream Server root.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RootPath,

        [string]$Backend = "amd"
    )

    if ([string]::IsNullOrWhiteSpace($RootPath)) {
        throw "RootPath is required to load backend contract '$Backend'."
    }

    $resolvedRoot = Resolve-Path -LiteralPath $RootPath -ErrorAction Stop
    $contractPath = Join-Path (Join-Path (Join-Path $resolvedRoot.Path "config") "backends") "$Backend.json"
    if (-not (Test-Path -LiteralPath $contractPath -PathType Leaf)) {
        throw "Backend contract not found: $contractPath"
    }

    try {
        $raw = Get-Content -LiteralPath $contractPath -Raw -ErrorAction Stop
        $contract = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Invalid backend contract '$contractPath': $($_.Exception.Message)"
    }

    if (-not $contract.id -or $contract.id -ne $Backend) {
        throw "Backend contract '$contractPath' has id '$($contract.id)', expected '$Backend'."
    }

    return $contract
}

function Get-DreamAmdLemonadeRuntime {
    <#
    .SYNOPSIS
        Return the AMD Lemonade runtime contract from config/backends/amd.json.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RootPath
    )

    $contract = Get-DreamBackendContract -RootPath $RootPath -Backend "amd"
    if (-not $contract.runtime -or -not $contract.runtime.lemonade) {
        throw "AMD backend contract is missing runtime.lemonade."
    }

    $lemonade = $contract.runtime.lemonade
    $required = @(
        "container_image",
        "windows_version",
        "windows_msi_file",
        "windows_executable",
        "api_port",
        "health_path",
        "linux_backend",
        "windows_backend"
    )
    foreach ($field in $required) {
        if (-not $lemonade.PSObject.Properties[$field] -or [string]::IsNullOrWhiteSpace([string]$lemonade.$field)) {
            throw "AMD Lemonade runtime contract is missing '$field'."
        }
    }

    return $lemonade
}
