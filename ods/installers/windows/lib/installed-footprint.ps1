function Move-ODSDevelopmentPathsToBackup {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$InstallDir,

        [Parameter(Mandatory = $true)]
        [string[]]$RelativePaths
    )

    $backupDir = $null
    foreach ($relativePath in $RelativePaths) {
        if ([string]::IsNullOrWhiteSpace($relativePath) -or
            $relativePath -in @(".", "..") -or
            [IO.Path]::IsPathRooted($relativePath) -or
            $relativePath.IndexOfAny([char[]]@("/", "\")) -ge 0) {
            throw "Invalid root-level development path: $relativePath"
        }

        $stalePath = Join-Path $InstallDir $relativePath
        $staleItem = Get-Item -LiteralPath $stalePath -Force -ErrorAction SilentlyContinue
        if ($null -eq $staleItem) {
            continue
        }

        if ($null -eq $backupDir) {
            $backupParent = Join-Path $InstallDir "data\installer-backups\development-footprint"
            New-Item -ItemType Directory -Path $backupParent -Force | Out-Null
            $backupName = "{0}-{1}" -f (
                [DateTime]::UtcNow.ToString("yyyyMMdd-HHmmss")
            ), ([guid]::NewGuid().ToString("N").Substring(0, 8))
            $backupDir = Join-Path $backupParent $backupName
            New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
        }

        # Move the root directory entry itself. Reparse points are not walked,
        # so a junction cannot make cleanup delete content outside InstallDir.
        Move-Item -LiteralPath $stalePath -Destination (
            Join-Path $backupDir $relativePath
        ) -Force
    }

    return $backupDir
}
