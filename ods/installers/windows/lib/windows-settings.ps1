# Windows settings ODS setup changed for an NVIDIA RTX Spark, and how to put
# them back (PR B design 4.6).
#
# %LOCALAPPDATA%\ODS\windows-settings.json records, only when ODS changes
# them, the page file the system drive had before (the PagingFiles value) and
# the facts of ODS's WSL memory limit in %USERPROFILE%\.wslconfig. The first
# original is kept however often ODS changes a setting again.
# restore-windows-settings.ps1 beside it runs unelevated, asks first, and puts
# .wslconfig back. It never elevates (a script the user can edit must never
# run as administrator), so for the page file it prints the original size and
# the steps to set it by hand.
# Requires private-file.ps1 in scope (Write-ODSPrivateEnvFile).

$script:ODSWindowsSettingsVersion = 1

function Get-ODSWindowsSettingsDir {
    $localAppData = $env:LOCALAPPDATA
    if ([string]::IsNullOrWhiteSpace($localAppData)) { throw 'Cannot locate %LOCALAPPDATA% for the ODS settings record.' }
    return (Join-Path $localAppData 'ODS')
}

function Get-ODSWindowsSettingsPath { return (Join-Path (Get-ODSWindowsSettingsDir) 'windows-settings.json') }

function Get-ODSWindowsRestoreScriptPath { return (Join-Path (Get-ODSWindowsSettingsDir) 'restore-windows-settings.ps1') }

function Read-ODSWindowsSettings {
    # The record as an ordered table (empty when ODS changed nothing yet).
    $path = Get-ODSWindowsSettingsPath
    $settings = [ordered]@{ schemaVersion = $script:ODSWindowsSettingsVersion }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $settings }
    $saved = [IO.File]::ReadAllText($path, [Text.UTF8Encoding]::new($false, $true)) | ConvertFrom-Json -ErrorAction Stop
    foreach ($property in @($saved.PSObject.Properties)) { $settings[$property.Name] = $property.Value }
    return $settings
}

function Save-ODSWindowsSettings($Settings) {
    Write-ODSPrivateEnvFile -Path (Get-ODSWindowsSettingsPath) -Content ($Settings | ConvertTo-Json -Depth 6)
}

function Get-ODSWindowsSettingsValue($Settings, [string]$Name) {
    if ($Settings.Contains($Name)) { return $Settings[$Name] }
    return $null
}

function Save-ODSWindowsPageFileOriginal([string[]]$PagingFiles, [bool]$Automatic) {
    # Before the first page-file change only: what the system had before ODS.
    $settings = Read-ODSWindowsSettings
    $record = Get-ODSWindowsSettingsValue $settings 'PageFile'
    if ($null -ne $record -and $null -ne $record.Original) { return }
    $settings['PageFile'] = [ordered]@{ Original = @($PagingFiles | Where-Object { $_ }); OriginalAutomatic = $Automatic; SetGB = $null; SetAt = $null }
    Save-ODSWindowsSettings $settings
}

function Complete-ODSWindowsPageFileChange([int]$SizeGB) {
    $settings = Read-ODSWindowsSettings
    $record = Get-ODSWindowsSettingsValue $settings 'PageFile'
    $updated = [ordered]@{ Original = @($record.Original); OriginalAutomatic = [bool]$record.OriginalAutomatic
        SetGB = $SizeGB; SetAt = [datetime]::UtcNow.ToString('o') }
    $settings['PageFile'] = $updated
    Save-ODSWindowsSettings $settings
    Write-ODSWindowsRestoreScript
}

function Save-ODSWindowsWslConfigChange([int]$Value, [bool]$HadFile, [string]$Backup, [string]$WrittenSha256, $InEffectGiB = $null) {
    # The first change keeps the owner's original file (HadFile, Backup);
    # later ones update only the value and what ODS wrote. WSL applies the
    # value when it restarts; until then Pending holds the limit WSL still
    # runs with (InEffectGiB, $null for WSL's default) and when ODS wrote it.
    # Complete-ODSWindowsWslConfigApplied clears it after ODS restarts WSL; a
    # Windows start after SinceUnix ends it too (Get-ODSWslPendingLimit).
    $settings = Read-ODSWindowsSettings
    $record = Get-ODSWindowsSettingsValue $settings 'WslConfig'
    if ($null -ne $record) {
        $HadFile = [bool]$record.HadFile
        $Backup = [string]$record.Backup
    }
    $settings['WslConfig'] = [ordered]@{ Value = $Value; HadFile = $HadFile; Backup = $Backup; WrittenSha256 = $WrittenSha256
        SetAt = [datetime]::UtcNow.ToString('o')
        Pending = [ordered]@{ SinceUnix = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
            InEffectGiB = $(if ($null -ne $InEffectGiB) { [double]$InEffectGiB } else { $null }) } }
    Save-ODSWindowsSettings $settings
    Write-ODSWindowsRestoreScript
}

function Complete-ODSWindowsWslConfigApplied {
    # WSL runs with ODS's value now (ODS restarted it, or nothing ran in WSL
    # yet): nothing waits any more.
    $settings = Read-ODSWindowsSettings
    $record = Get-ODSWindowsSettingsValue $settings 'WslConfig'
    if ($null -eq $record -or $null -eq $record.Pending) { return }
    $updated = [ordered]@{}
    foreach ($property in @($record.PSObject.Properties)) { $updated[$property.Name] = $property.Value }
    $updated['Pending'] = $null
    $settings['WslConfig'] = $updated
    Save-ODSWindowsSettings $settings
}

function Format-ODSPagingFilesDescription([string[]]$PagingFiles, [bool]$Automatic, [string]$Drive = 'C:') {
    # How the page file was set, in words (pure).
    if ($Automatic) { return 'managed automatically by Windows' }
    $entries = @($PagingFiles | Where-Object { $_ })
    if (-not $entries.Count) { return 'no page file' }
    $parts = @()
    foreach ($entry in $entries) {
        $match = [regex]::Match(([string]$entry).Trim(), '^(?<drive>[A-Za-z?]:)\\\S+(\s+(?<initial>[0-9]+)\s+(?<maximum>[0-9]+))?$')
        if (-not $match.Success) { $parts += "'$entry'"; continue }
        $where = $match.Groups['drive'].Value
        if ($where -eq '?:') { $parts += 'managed automatically by Windows'; continue }
        $initial = if ($match.Groups['initial'].Success) { [long]$match.Groups['initial'].Value } else { 0 }
        $maximum = if ($match.Groups['maximum'].Success) { [long]$match.Groups['maximum'].Value } else { 0 }
        if ($initial -le 0 -or $maximum -le 0) { $parts += "system managed on $where"; continue }
        if ($initial -eq $maximum) { $parts += "a fixed $initial MB page file on $where"; continue }
        $parts += "$initial to $maximum MB on $where"
    }
    return ($parts -join ', ')
}

function Get-ODSWindowsRestoreScriptText {
    # The restore script: generic code, no machine paths (it finds them from
    # its own folder and %USERPROFILE%), ASCII only so Windows PowerShell 5.1
    # reads it in any code page. It carries the pure helpers it needs.
    $helpers = @("`$script:ODSWslConfigMarker = '" + ([string]$script:ODSWslConfigMarker).Replace("'", "''") + "'`r`n")
    $helpers += foreach ($name in @('Get-ODSWslConfigLines', 'Remove-ODSWslConfigMemoryText', 'Format-ODSPagingFilesDescription')) {
        "function $name {`r`n" + (Get-Item -LiteralPath "Function:$name").Definition.Trim("`r", "`n") + "`r`n}`r`n"
    }
    $body = @'
# Puts back the Windows settings ODS setup changed for this account. Run it in
# normal PowerShell (not as administrator):
#   & "$env:LOCALAPPDATA\ODS\restore-windows-settings.ps1"
# It asks before it changes anything (-Yes skips the question) and never asks
# for administrator rights. Need help? Ask on the ODS Discord:
# https://discord.gg/4ntNp9MAwC
param([switch]$Yes)
$ErrorActionPreference = 'Stop'
$settingsPath = Join-Path $PSScriptRoot 'windows-settings.json'
$wslConfigPath = Join-Path $env:USERPROFILE '.wslconfig'
'@ + "`r`n" + ($helpers -join "`r`n") + @'

function Get-RestoreSha256([byte[]]$Bytes) {
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return (-join ($hash.ComputeHash($Bytes) | ForEach-Object { $_.ToString('x2') })) } finally { $hash.Dispose() }
}

if (-not (Test-Path -LiteralPath $settingsPath -PathType Leaf)) {
    Write-Host 'ODS has no record of Windows settings it changed for this account; nothing to restore.'
    exit 0
}
$settings = [IO.File]::ReadAllText($settingsPath, [Text.UTF8Encoding]::new($false, $true)) | ConvertFrom-Json
$wsl = $settings.WslConfig
$changed = $false
if ($wsl) {
    $exists = Test-Path -LiteralPath $wslConfigPath -PathType Leaf
    $bytes = if ($exists) { [IO.File]::ReadAllBytes($wslConfigPath) } else { [byte[]]@() }
    $unchanged = $exists -and (Get-RestoreSha256 $bytes) -ceq [string]$wsl.WrittenSha256
    $action = ''
    if ($unchanged -and -not $wsl.HadFile) {
        $action = "delete $wslConfigPath, which ODS created (you had none before)"
    } elseif ($unchanged -and $wsl.Backup -and (Test-Path -LiteralPath $wsl.Backup -PathType Leaf)) {
        $action = "put back your earlier $wslConfigPath from $($wsl.Backup)"
    } elseif ($exists) {
        $text = [Text.UTF8Encoding]::new($false).GetString($bytes)
        $kept = Remove-ODSWslConfigMemoryText $text
        if ($kept -cne $text) { $action = "remove only the memory limit ODS added to $wslConfigPath (the file changed since, so the rest is kept)" }
    }
    if (-not $action) {
        Write-Host "ODS's WSL memory limit is no longer in $wslConfigPath; nothing to restore there."
    } elseif ($Yes -or (Read-Host "Restore: $action? [y/N]") -match '^(y|yes)$') {
        if ($action.StartsWith('delete')) { Remove-Item -LiteralPath $wslConfigPath -Force }
        elseif ($action.StartsWith('put back')) { [IO.File]::WriteAllBytes($wslConfigPath, [IO.File]::ReadAllBytes($wsl.Backup)) }
        else { [IO.File]::WriteAllText($wslConfigPath, $kept, [Text.UTF8Encoding]::new($false)) }
        $settings.PSObject.Properties.Remove('WslConfig')
        $changed = $true
        Write-Host "Done. WSL uses it after WSL restarts: run wsl --shutdown (this stops Ubuntu, ODS and Docker Desktop's containers), or restart Windows."
    } else {
        Write-Host 'Nothing was changed.'
    }
}
$pageFile = $settings.PageFile
if ($pageFile -and $pageFile.SetGB) {
    $before = Format-ODSPagingFilesDescription @($pageFile.Original) ([bool]$pageFile.OriginalAutomatic) $env:SystemDrive
    Write-Host ("ODS set a fixed $($pageFile.SetGB) GB page file on $env:SystemDrive; before that Windows had $before. " +
        'This script does not change it, because that needs an administrator. To change it yourself: System Properties > Advanced > ' +
        'Performance > Settings > Advanced > Virtual memory > Change; choose the size you want, Set, OK, then restart Windows. ' +
        'A model on the GPU needs the larger size; see the ODS RTX Spark notes before you shrink it.')
}
if ($changed) { [IO.File]::WriteAllText($settingsPath, ($settings | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false)) }
Write-Host 'Need help? Ask on the ODS Discord: https://discord.gg/4ntNp9MAwC'
'@
    return $body
}

function Write-ODSWindowsRestoreScript {
    $text = Get-ODSWindowsRestoreScriptText
    if ($text -cnotmatch '^[\x00-\x7f]*$') { throw 'The ODS restore script must be ASCII.' }
    Write-ODSPrivateFileBytes -Path (Get-ODSWindowsRestoreScriptPath) -Bytes ([Text.Encoding]::ASCII.GetBytes($text))
}
