# Contract: install-windows.ps1 reports an unavailable install drive (an
# unplugged USB drive, or a letter that changed) instead of crashing in
# constants.ps1 with a raw Join-Path DriveNotFound trace (#8492).
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { Write-Host 'SKIP: drive letters are Windows-only'; exit 0 }

$installer = Join-Path $PSScriptRoot '../../installers/windows/install-windows.ps1'
$used = @([IO.DriveInfo]::GetDrives() | ForEach-Object { $_.Name.Substring(0, 1).ToUpperInvariant() })
$letter = @([char[]]'QRSTUVWXYZ' | ForEach-Object { [string]$_ } | Where-Object { $used -notcontains $_ })[0]
if (-not $letter) { throw 'no unused drive letter available for the fixture' }
$target = "${letter}:\ods"

function Invoke-Installer([string[]]$Arguments) {
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer @Arguments 2>&1 | Out-String
    return [pscustomobject]@{ Exit = $LASTEXITCODE; Output = $output }
}

$previousHome = $env:ODS_HOME
try {
    foreach ($case in @(
        @{ Name = '-InstallDir'; Arguments = @('-DryRun', '-NonInteractive', '-Force', '-InstallDir', $target); Home = $null },
        @{ Name = 'ODS_HOME'; Arguments = @('-DryRun', '-NonInteractive', '-Force'); Home = $target }
    )) {
        if ($case.Home) { $env:ODS_HOME = $case.Home } else { Remove-Item Env:ODS_HOME -ErrorAction SilentlyContinue }
        $result = Invoke-Installer $case.Arguments
        if ($result.Exit -ne 1) { throw "$($case.Name): expected exit 1, got $($result.Exit)" }
        if ($result.Output -match 'DriveNotFound') { throw "$($case.Name): raw DriveNotFound trace leaked: $($result.Output)" }
        if ($result.Output -notmatch [regex]::Escape("drive ${letter}: is not available")) {
            throw "$($case.Name): missing drive was not named: $($result.Output)"
        }
        Write-Host "PASS $($case.Name) on missing drive ${letter}: exits 1 with a clear message"
    }
} finally {
    if ($null -ne $previousHome) { $env:ODS_HOME = $previousHome } else { Remove-Item Env:ODS_HOME -ErrorAction SilentlyContinue }
}
exit 0
