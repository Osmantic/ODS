$ErrorActionPreference = "Stop"

# Windows PowerShell 5.1 has no TimeZoneInfo.TryConvertWindowsIdToIanaId, so
# the installer falls back to its own Windows-ID -> IANA map for TIMEZONE in
# .env. A wildcard match once sent "Central Europe Standard Time" to
# America/Chicago and "SA Eastern Standard Time" to America/New_York.

$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
. (Join-Path $root "installers\windows\lib\env-generator.ps1")

$cases = [ordered]@{
    "Central Europe Standard Time"   = "Europe/Budapest"
    "Central European Standard Time" = "Europe/Warsaw"
    "AUS Eastern Standard Time"      = "Australia/Sydney"
    "Eastern Standard Time"          = "America/New_York"
    "Central Standard Time"          = "America/Chicago"
    "Arabian Standard Time"          = "Asia/Dubai"
    "SA Eastern Standard Time"       = "Etc/GMT+3"
    "Central Pacific Standard Time"  = "Etc/GMT-11"
    "Not A Zone"                     = "UTC"
}
$failed = $false
foreach ($id in $cases.Keys) {
    $got = ConvertTo-ODSIanaTimeZone -WindowsId $id
    if ($got -cne $cases[$id]) {
        Write-Host "FAIL: '$id' -> '$got' (expected '$($cases[$id])')"
        $failed = $true
    }
}
if ($failed) { exit 1 }
Write-Host "PASS: Windows time zone IDs map to the matching IANA zone"
