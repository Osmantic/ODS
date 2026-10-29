# ============================================================================
# ODS Windows Native Desktop -- Release Packaging Script
# ============================================================================
param(
    [string]$DistDir = (Join-Path $PSScriptRoot '..\..\dist\windows-desktop'),
    [string]$OutputDir = (Join-Path $PSScriptRoot '..\..\dist')
)

$ErrorActionPreference = 'Stop'
$resolvedDist = [System.IO.Path]::GetFullPath($DistDir)
$resolvedOut = [System.IO.Path]::GetFullPath($OutputDir)

Write-Host "Packaging release from: $resolvedDist"
Write-Host "Output directory:       $resolvedOut"

if (-not (Test-Path -LiteralPath $resolvedDist)) {
    throw "Distribution directory not found: $resolvedDist"
}

$zipName = "ODS-Windows-Desktop-v3.0.0-x64.zip"
$zipPath = Join-Path $resolvedOut $zipName

if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($resolvedDist, $zipPath, [System.IO.Compression.CompressionLevel]::Optimal, $false)

$zipItem = Get-Item -LiteralPath $zipPath
Write-Host "Created archive: $($zipItem.FullName) ($([math]::Round($zipItem.Length / 1MB, 2)) MB)"

$setupExe = Join-Path $resolvedDist 'ODS-Setup.exe'
$odsExe = Join-Path $resolvedDist 'ODS.exe'

$hashes = @()
if (Test-Path -LiteralPath $setupExe) {
    $h = Get-FileHash -LiteralPath $setupExe -Algorithm SHA256
    $hashes += [PSCustomObject]@{ File = 'ODS-Setup.exe'; SHA256 = $h.Hash; Size = (Get-Item $setupExe).Length }
}
if (Test-Path -LiteralPath $odsExe) {
    $h = Get-FileHash -LiteralPath $odsExe -Algorithm SHA256
    $hashes += [PSCustomObject]@{ File = 'ODS.exe'; SHA256 = $h.Hash; Size = (Get-Item $odsExe).Length }
}
$hZip = Get-FileHash -LiteralPath $zipPath -Algorithm SHA256
$hashes += [PSCustomObject]@{ File = $zipName; SHA256 = $hZip.Hash; Size = $zipItem.Length }

$checksumsFile = Join-Path $resolvedOut 'SHA256SUMS.txt'
$checksumLines = $hashes | ForEach-Object { "$($_.SHA256.ToLowerInvariant())  $($_.File)" }
[System.IO.File]::WriteAllLines($checksumsFile, $checksumLines, [System.Text.Encoding]::UTF8)

Write-Host "`nRelease Checksums:"
$hashes | Format-Table -AutoSize
