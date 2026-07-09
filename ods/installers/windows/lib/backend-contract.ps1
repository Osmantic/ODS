# ============================================================================
# ODS Windows Installer -- Backend Contract Loader
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Read backend contracts from an explicit ODS root path.
# ============================================================================

function Get-ODSBackendContract {
    <#
    .SYNOPSIS
        Read a backend contract JSON file from a known ODS root.
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

function Get-ODSAmdLemonadeRuntime {
    <#
    .SYNOPSIS
        Return the AMD Lemonade runtime contract from config/backends/amd.json.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RootPath
    )

    $contract = Get-ODSBackendContract -RootPath $RootPath -Backend "amd"
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

function Resolve-ODSLemonadeExe {
    <#
    .SYNOPSIS
        Resolve the native Windows Lemonade executable across both MSI roots.

    .DESCRIPTION
        Lemonade's minimal MSI can land under either Program Files root depending
        on package architecture and Windows installer behavior. Recent MSI builds
        also install LemonadeServer.exe instead of the historical
        lemonade-server.exe. Probe the known roots, folder names, and executable
        aliases before falling back so AMD installs do not miss a valid Lemonade
        runtime and silently downgrade to Vulkan llama-server.
    #>
    [CmdletBinding()]
    param(
        [string]$ExecutableName = "lemonade-server.exe"
    )

    $candidates = New-Object System.Collections.Generic.List[string]

    $existingVar = Get-Variable -Name LEMONADE_EXE -Scope Script -ErrorAction SilentlyContinue
    if ($existingVar -and -not [string]::IsNullOrWhiteSpace([string]$existingVar.Value)) {
        $candidates.Add([string]$existingVar.Value)
    }

    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)})
    $installFolders = @("Lemonade Server", "lemonade_server", "LemonadeServer")
    $executableNames = @($ExecutableName, "LemonadeServer.exe", "lemonade-server.exe") |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Select-Object -Unique
    foreach ($root in $roots) {
        if ([string]::IsNullOrWhiteSpace($root)) { continue }
        foreach ($folder in $installFolders) {
            foreach ($name in $executableNames) {
                $candidates.Add((Join-Path (Join-Path (Join-Path $root $folder) "bin") $name))
            }
        }
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }

    return $null
}
