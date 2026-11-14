# ============================================================================
# ODS Windows Desktop -- Owner settings
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Keep the owner's Windows Desktop choices across reruns. A value the
#          owner passes now wins, then the saved value, then the default, so a
#          repair or upgrade never quietly resets a choice (the bug class of
#          #7964 and #7531 on the Compose path).
# Expects: private-file.ps1 (Write-ODSPrivateEnvFile) for Write-ODSDesktopSettings.
# Provides: Resolve-ODSDesktopSettings, Read-ODSDesktopSettings,
#           Write-ODSDesktopSettings
# Modder notes: Resolve-ODSDesktopSettings is pure. Only keys in -Defaults are
#   settings; each must keep its default's type (string, integer or boolean).
#   $false and 0 are real choices, never "unset".
# ============================================================================

function Get-ODSDesktopSettingKind($Value) {
    if ($Value -is [bool]) { return 'boolean' }
    if ($Value -is [int] -or $Value -is [long]) { return 'integer' }
    if ($Value -is [string]) { return 'string' }
    return 'unsupported'
}

function Test-ODSDesktopSettingPresent($Value) {
    return ($null -ne $Value -and -not ($Value -is [string] -and [string]::IsNullOrWhiteSpace($Value)))
}

function Resolve-ODSDesktopSettings {
    param(
        [hashtable]$Explicit = @{},
        $Previous,
        [Parameter(Mandatory = $true)][Collections.Specialized.OrderedDictionary]$Defaults
    )
    $resolved = [ordered]@{}
    foreach ($key in $Defaults.Keys) {
        $kind = Get-ODSDesktopSettingKind $Defaults[$key]
        if ($kind -eq 'unsupported') { throw "The default for Windows Desktop setting '$key' must be a string, integer or boolean." }
        $value = $Defaults[$key]
        $source = 'default'
        $saved = if ($null -ne $Previous -and $Previous.PSObject.Properties[$key]) { $Previous.$key } else { $null }
        if (Test-ODSDesktopSettingPresent $saved) { $value = $saved; $source = 'saved' }
        if ($Explicit.ContainsKey($key) -and (Test-ODSDesktopSettingPresent $Explicit[$key])) { $value = $Explicit[$key]; $source = 'explicit' }
        if ((Get-ODSDesktopSettingKind $value) -ne $kind) {
            throw "Windows Desktop setting '$key' must be a $kind, but the $source value is '$value'."
        }
        $resolved[$key] = $(if ($kind -eq 'integer') { [long]$value } else { $value })
    }
    return $resolved
}

function Read-ODSDesktopSettings([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    # Strict UTF-8: invalid bytes stop here instead of becoming a silent reset.
    $text = [IO.File]::ReadAllText($Path, [Text.UTF8Encoding]::new($false, $true))
    try {
        $settings = $text | ConvertFrom-Json
    } catch [ArgumentException] {
        throw "The Windows Desktop settings file is not valid JSON: $Path. Fix it or delete it to use the defaults."
    }
    # [pscustomobject] as a type test means [psobject] and matches almost
    # anything; check the real type so a JSON array or scalar is refused.
    if ($settings -isnot [Management.Automation.PSCustomObject]) {
        throw "The Windows Desktop settings file must hold a JSON object: $Path."
    }
    return $settings
}

function Write-ODSDesktopSettings([string]$Path, [Collections.Specialized.OrderedDictionary]$Settings) {
    Write-ODSPrivateEnvFile -Path $Path -Content ($Settings | ConvertTo-Json -Compress)
}
