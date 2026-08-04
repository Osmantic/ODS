$ErrorActionPreference = "Stop"

# The Windows .env generator has to publish the same device-identity keys the
# Linux and macOS generators do: ODS_DEVICE_NAME (proxy vhosts + magic-link
# URLs) and HOST_LAN_IP (openclaw's LAN allowedOrigins).

$root = Split-Path -Parent $PSScriptRoot
$generatorPath = Join-Path $root "installers\windows\lib\env-generator.ps1"
$schemaPath = Join-Path $root ".env.schema.json"

. $generatorPath

function Assert-Equal {
    param($Actual, $Expected, [string]$Label)
    if ($Actual -ne $Expected) {
        throw "$Label expected '$Expected', got '$Actual'"
    }
}

function Assert-Match {
    param([string]$Value, [string]$Pattern, [string]$Label)
    if ($Value -notmatch $Pattern) {
        throw "$Label : '$Value' does not match /$Pattern/"
    }
}

$schema = Get-Content -LiteralPath $schemaPath -Raw | ConvertFrom-Json
$devicePattern = $schema.properties.ODS_DEVICE_NAME.pattern
if ([string]::IsNullOrWhiteSpace($devicePattern)) {
    throw "ODS_DEVICE_NAME has no pattern in .env.schema.json"
}

# --- Get-WindowsODSDeviceName -------------------------------------------

$cases = @(
    @{ In = "DESKTOP-AB12";                          Out = "desktop-ab12" }
    @{ In = "My_Laptop!";                            Out = "my-laptop" }
    @{ In = "ods";                                   Out = "ods" }
    @{ In = "";                                      Out = "ods" }
    @{ In = "---";                                   Out = "ods" }
    @{ In = "_";                                     Out = "ods" }
    # 40 characters: truncated to 32, and the truncation must not leave a
    # trailing hyphen behind (the schema requires an alphanumeric last char).
    @{ In = "abcdefghijklmnopqrstuvwxyz01234-56789xyz"; Out = "abcdefghijklmnopqrstuvwxyz01234" }
)

foreach ($case in $cases) {
    $actual = Get-WindowsODSDeviceName -MachineName $case.In
    Assert-Equal $actual $case.Out "Get-WindowsODSDeviceName('$($case.In)')"
    Assert-Match $actual $devicePattern "Get-WindowsODSDeviceName('$($case.In)') vs schema"
    if ($actual.Length -gt 32) {
        throw "Get-WindowsODSDeviceName('$($case.In)') returned $($actual.Length) characters"
    }
}

Write-Host "[PASS] Get-WindowsODSDeviceName produces schema-valid names"

# --- Get-WindowsODSHostLanIp --------------------------------------------

# Never throws and never reports loopback/APIPA, whatever the runner's
# adapters look like (CI agents differ from developer machines).
$lanIp = Get-WindowsODSHostLanIp
if ($null -eq $lanIp) { throw "Get-WindowsODSHostLanIp returned null instead of a string" }
if ($lanIp -ne "") {
    Assert-Match $lanIp '^\d{1,3}(\.\d{1,3}){3}$' "Get-WindowsODSHostLanIp"
    if ($lanIp -like "127.*" -or $lanIp -like "169.254.*") {
        throw "Get-WindowsODSHostLanIp returned a non-LAN address: $lanIp"
    }
}

Write-Host "[PASS] Get-WindowsODSHostLanIp returns a LAN address or an empty string"

# --- Generated .env content ---------------------------------------------

$generatorText = Get-Content -LiteralPath $generatorPath -Raw

foreach ($key in @("ODS_DEVICE_NAME", "HOST_LAN_IP")) {
    if ($generatorText -notmatch "(?m)^$key=") {
        throw "Windows .env generator never writes $key"
    }
}

# HOST_LAN_IP must stay empty on loopback installs so the compose
# `${HOST_LAN_IP:-}` fallback keeps its meaning.
if ($generatorText -notmatch [regex]::Escape('if ($bindAddress -eq "0.0.0.0") { $hostLanIp = Get-WindowsODSHostLanIp }')) {
    throw "HOST_LAN_IP detection is not gated on BIND_ADDRESS=0.0.0.0"
}

# Operator values in an existing .env must survive a re-run, like every other
# persistent key in this generator.
foreach ($key in @("ODS_DEVICE_NAME", "HOST_LAN_IP")) {
    if ($generatorText -notmatch [regex]::Escape("Get-EnvOrNew `"$key`"")) {
        throw "$key does not go through Get-EnvOrNew, so a re-run would discard the operator value"
    }
}

Write-Host "[PASS] Windows .env carries ODS_DEVICE_NAME and HOST_LAN_IP"
Write-Host "[PASS] windows env device identity"
