# The page file entries (the PagingFiles registry value) with the system
# drive's page file set to a fixed size. Page files on other drives are kept.
# The all-drives automatic entry (?:\pagefile.sys) is dropped: a fixed size
# turns automatic management off. Pure; shared by set-pagefile.ps1, which runs
# elevated, by the RTX Spark memory planner and by their contract tests.
# Ported from the RTX Spark branch (21ae748de); the parser and the
# never-shrink rule are new.

function ConvertFrom-ODSPagingFileEntry([string]$Entry) {
    # "C:\pagefile.sys 98304 98304" -> drive, path and sizes in MB. Sizes 0 0
    # (or none) mean "system managed"; "?:\pagefile.sys" is automatic on every
    # drive. $null for anything else.
    $match = [regex]::Match(([string]$Entry).Trim(), '^(?<drive>[A-Za-z?]:)\\(?<file>[^\s]+)(\s+(?<initial>[0-9]+)\s+(?<maximum>[0-9]+))?$')
    if (-not $match.Success) { return $null }
    $initial = if ($match.Groups['initial'].Success) { [long]$match.Groups['initial'].Value } else { 0 }
    $maximum = if ($match.Groups['maximum'].Success) { [long]$match.Groups['maximum'].Value } else { 0 }
    return [pscustomobject]@{
        Drive = $match.Groups['drive'].Value.ToUpperInvariant()
        InitialMB = $initial
        MaximumMB = $maximum
        Fixed = ($initial -gt 0 -and $maximum -gt 0)
    }
}

function Get-ODSSystemPagingFile([string[]]$Existing, [string]$Drive) {
    <#
    .SYNOPSIS
        The system drive's page file in the PagingFiles value: Kind 'none',
        'fixed' (InitialMB, MaximumMB), 'system' (system managed on this
        drive) or 'automatic' (?:\pagefile.sys).
    #>
    if ($Drive -notmatch '^[A-Za-z]:$') { throw "Not a drive: $Drive" }
    $entries = @($Existing | ForEach-Object { ConvertFrom-ODSPagingFileEntry $_ } | Where-Object { $null -ne $_ })
    if (@($entries | Where-Object { $_.Drive -eq '?:' }).Count) {
        return [pscustomobject]@{ Kind = 'automatic'; InitialMB = 0; MaximumMB = 0 }
    }
    $own = @($entries | Where-Object { $_.Drive -ieq $Drive })
    if (-not $own.Count) { return [pscustomobject]@{ Kind = 'none'; InitialMB = 0; MaximumMB = 0 } }
    if ($own[0].Fixed) { return [pscustomobject]@{ Kind = 'fixed'; InitialMB = $own[0].InitialMB; MaximumMB = $own[0].MaximumMB } }
    return [pscustomobject]@{ Kind = 'system'; InitialMB = 0; MaximumMB = 0 }
}

function Get-ODSPagingFilesEntries([string[]]$Existing, [string]$Drive, [int]$SizeMB) {
    if ($Drive -notmatch '^[A-Za-z]:$') { throw "Not a drive: $Drive" }
    # ODS only ever grows the system drive's page file.
    $current = Get-ODSSystemPagingFile $Existing $Drive
    if ($current.Kind -eq 'fixed' -and [math]::Max($current.InitialMB, $current.MaximumMB) -gt $SizeMB) {
        throw "The page file on $Drive is already $([math]::Max($current.InitialMB, $current.MaximumMB)) MB; ODS never makes it smaller (asked for $SizeMB MB)."
    }
    $kept = @($Existing | Where-Object {
        $entry = [string]$_
        $entry -match '^[A-Za-z]:\\' -and -not $entry.StartsWith("$Drive\", [StringComparison]::OrdinalIgnoreCase)
    })
    return @("$Drive\pagefile.sys $SizeMB $SizeMB") + $kept
}
