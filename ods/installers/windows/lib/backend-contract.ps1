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

function Test-ODSLoopbackAddress {
    [CmdletBinding()]
    param([string]$Address)

    $normalized = ([string]$Address).Trim().ToLowerInvariant()
    return $normalized -in @("", "localhost", "127.0.0.1", "::1", "[::1]")
}

function Get-ODSEnvFileValue {
    [CmdletBinding()]
    param(
        [string]$EnvPath,
        [Parameter(Mandatory = $true)]
        [string]$Key
    )

    if ([string]::IsNullOrWhiteSpace($EnvPath) -or -not (Test-Path -LiteralPath $EnvPath -PathType Leaf)) {
        return $null
    }
    foreach ($line in (Get-Content -LiteralPath $EnvPath -ErrorAction SilentlyContinue)) {
        if ($line -match ('^\s*' + [regex]::Escape($Key) + '=(.*)$')) {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

function ConvertTo-ODSPowerShellSingleQuotedLiteral {
    param([AllowNull()][string]$Value)
    if ($null -eq $Value) { return "''" }
    return "'" + $Value.Replace("'", "''") + "'"
}

function Resolve-ODSInteractiveScheduledTaskUser {
    <#
    .SYNOPSIS
        Return a Task Scheduler principal for the current interactive user.

    .DESCRIPTION
        Remote PowerShell sessions can expose USERNAME without the local/domain
        qualifier that Task Scheduler needs for InteractiveToken tasks. Prefer
        whoami's fully-qualified identity and only fall back after verifying the
        account translates to a SID.
    #>
    [CmdletBinding()]
    param()

    $candidates = New-Object System.Collections.Generic.List[string]
    try {
        $whoami = (& "$env:SystemRoot\System32\whoami.exe" 2>$null | Select-Object -First 1)
        if (-not [string]::IsNullOrWhiteSpace([string]$whoami)) {
            $candidates.Add(([string]$whoami).Trim())
        }
    } catch { }

    if (-not [string]::IsNullOrWhiteSpace($env:USERDOMAIN) -and
        -not [string]::IsNullOrWhiteSpace($env:USERNAME)) {
        $candidates.Add("$($env:USERDOMAIN)\$($env:USERNAME)")
    }
    if (-not [string]::IsNullOrWhiteSpace($env:USERNAME)) {
        $candidates.Add([string]$env:USERNAME)
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        try {
            $account = New-Object System.Security.Principal.NTAccount($candidate)
            $null = $account.Translate([System.Security.Principal.SecurityIdentifier])
            return $candidate
        } catch { }
    }

    throw "Could not resolve the current interactive Windows user for an ODS scheduled task."
}

function New-ODSInteractiveScheduledTaskPrincipal {
    [CmdletBinding()]
    param(
        [ValidateSet("Limited", "Highest")]
        [string]$RunLevel = "Limited"
    )

    $userId = Resolve-ODSInteractiveScheduledTaskUser
    return New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel $RunLevel
}
