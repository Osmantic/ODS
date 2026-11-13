# WSL memory limit for an RTX Spark (PR B3): the .wslconfig text transforms,
# the backed-up write and its record, and the restore script, which a child
# PowerShell runs against a temporary profile. WSL itself never runs, and only
# temporary files are written.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
. (Join-Path $root 'installers/windows/lib/wsl-memory-config.ps1')
$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}
$marker = '# Set by ODS setup: memory limit for WSL. Rerun ODS setup to change it.'
$crlf = "`r`n"

$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-wslconfig-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $fixture
$previous = @{ LOCALAPPDATA = $env:LOCALAPPDATA; USERPROFILE = $env:USERPROFILE; SystemDrive = $env:SystemDrive }
$env:LOCALAPPDATA = Join-Path $fixture 'LocalAppData'
$env:USERPROFILE = Join-Path $fixture 'Profile'
$env:SystemDrive = 'C:'
$null = New-Item -ItemType Directory -Path $env:LOCALAPPDATA, $env:USERPROFILE -Force
try {
    # --- Reading memory= ------------------------------------------------------------
    Check ((ConvertFrom-ODSWslMemoryValue '16GB') -eq 16 -and (ConvertFrom-ODSWslMemoryValue '16G') -eq 16 -and (ConvertFrom-ODSWslMemoryValue '8192MB') -eq 8 -and
        (ConvertFrom-ODSWslMemoryValue '1TB') -eq 1024 -and (ConvertFrom-ODSWslMemoryValue '17179869184') -eq 16 -and $null -eq (ConvertFrom-ODSWslMemoryValue 'lots')) 'WSL sizes read as GiB: GB, G, MB, TB and plain bytes'
    Check ($null -eq (Read-ODSWslConfigMemory '') -and $null -eq (Read-ODSWslConfigMemory "[wsl2]${crlf}processors=8${crlf}")) 'no memory= under [wsl2] is no value'
    Check ($null -eq (Read-ODSWslConfigMemory "[experimental]${crlf}memory=8GB${crlf}")) 'memory= in another section is not WSL''s limit'
    $owner = Read-ODSWslConfigMemory "[wsl2]${crlf}memory=24GB${crlf}"
    Check ($owner.GiB -eq 24 -and $owner.Owner -ceq 'owner') 'an unmarked memory= is the owner''s'
    $ods = "[wsl2]${crlf}${marker}${crlf}memory=16GB${crlf}"
    Check ((Read-ODSWslConfigMemory $ods 16).Owner -ceq 'ods' -and (Read-ODSWslConfigMemory $ods $null).Owner -ceq 'owner' -and
        (Read-ODSWslConfigMemory $ods 20).Owner -ceq 'owner') 'ODS''s marker counts only when windows-settings.json records the same value'
    $unreadable = Read-ODSWslConfigMemory "[wsl2]${crlf}memory=half${crlf}"
    Check ($null -eq $unreadable.GiB -and $unreadable.Owner -ceq 'owner') 'an owner value WSL cannot read is still the owner''s'
    Check ([math]::Abs((Get-ODSWslDefaultMemoryGiB 30.49) - 15.245) -lt 0.01 -and (Get-ODSWslDefaultMemoryGiB 62.5) -eq 31.25) 'WSL''s default limit is half of the memory Windows sees'

    # --- Writing memory= (pure) ------------------------------------------------------
    Check ((Set-ODSWslConfigMemoryText '' 16) -ceq "[wsl2]${crlf}${marker}${crlf}memory=16GB${crlf}") 'no file: [wsl2], the marker and memory=16GB, CRLF'
    $other = "# my settings${crlf}[experimental]${crlf}autoMemoryReclaim=gradual${crlf}"
    Check ((Set-ODSWslConfigMemoryText $other 16) -ceq "$other${crlf}[wsl2]${crlf}${marker}${crlf}memory=16GB${crlf}") '[wsl2] missing: added after a blank line, every other line and comment kept'
    $keys = "[wsl2]${crlf}processors=8${crlf}swap=0${crlf}"
    Check ((Set-ODSWslConfigMemoryText $keys 16) -ceq "[wsl2]${crlf}${marker}${crlf}memory=16GB${crlf}processors=8${crlf}swap=0${crlf}") 'other [wsl2] keys are kept; memory goes right under the header with its marker'
    Check ($null -eq (Set-ODSWslConfigMemoryText "[wsl2]${crlf}memory=24GB${crlf}" 16)) 'the owner''s memory= is never changed'
    Check ((Set-ODSWslConfigMemoryText "[wsl2]${crlf}${marker}${crlf}memory=20GB${crlf}" 16 20) -ceq "[wsl2]${crlf}${marker}${crlf}memory=16GB${crlf}") 'a value ODS owns is updated in place'
    Check ($null -eq (Set-ODSWslConfigMemoryText "[wsl2]${crlf}${marker}${crlf}memory=20GB${crlf}" 16 $null)) 'a marker without a matching record is treated as the owner''s'
    Check ((Set-ODSWslConfigMemoryText "[wsl2]`nprocessors=8`n" 16) -ceq "[wsl2]`n${marker}`nmemory=16GB`nprocessors=8`n") 'an LF file stays LF'
    Check ((Set-ODSWslConfigMemoryText "[wsl2]${crlf}processors=8" 16) -ceq "[wsl2]${crlf}${marker}${crlf}memory=16GB${crlf}processors=8${crlf}") 'a file without a final newline gets one'
    $floor = ''
    try { $null = Set-ODSWslConfigMemoryText '' 8 } catch { $floor = $_.Exception.Message }
    Check ($floor -match 'never limits WSL to less than 12 GB') 'ODS never writes a WSL limit under 12 GB'
    Check ((Remove-ODSWslConfigMemoryText "[wsl2]${crlf}${marker}${crlf}memory=16GB${crlf}processors=8${crlf}") -ceq "[wsl2]${crlf}processors=8${crlf}") 'the restore transform removes only ODS''s marker and its memory= line'
    Check ((Remove-ODSWslConfigMemoryText "[wsl2]${crlf}memory=24GB${crlf}") -ceq "[wsl2]${crlf}memory=24GB${crlf}") 'without ODS''s marker the restore transform changes nothing'

    # --- autoMemoryReclaim (pure) -------------------------------------------------------
    $reclaimMarker = '# Set by ODS setup: WSL gives memory it no longer uses back to Windows. Rerun ODS setup to change it.'
    Check ((Set-ODSWslConfigReclaimText '' 'dropcache') -ceq "[experimental]${crlf}${reclaimMarker}${crlf}autoMemoryReclaim=dropcache${crlf}") 'no file: [experimental], the marker and autoMemoryReclaim=dropcache'
    Check ((Set-ODSWslConfigReclaimText "[experimental]${crlf}sparseVhd=true${crlf}" 'dropcache') -ceq "[experimental]${crlf}${reclaimMarker}${crlf}autoMemoryReclaim=dropcache${crlf}sparseVhd=true${crlf}") 'an existing [experimental] keeps its other keys'
    Check ($null -eq (Set-ODSWslConfigReclaimText "[experimental]${crlf}autoMemoryReclaim=gradual${crlf}" 'dropcache') -and
        $null -eq (Set-ODSWslConfigReclaimText "[experimental]${crlf}automemoryreclaim=disabled${crlf}" 'dropcache')) 'the owner''s autoMemoryReclaim= (any case) is never changed'
    Check ((Set-ODSWslConfigReclaimText "[experimental]${crlf}${reclaimMarker}${crlf}autoMemoryReclaim=gradual${crlf}" 'dropcache' 'gradual') -ceq "[experimental]${crlf}${reclaimMarker}${crlf}autoMemoryReclaim=dropcache${crlf}") 'a value ODS owns is updated in place'
    Check ($null -eq (Set-ODSWslConfigReclaimText "[experimental]${crlf}${reclaimMarker}${crlf}autoMemoryReclaim=gradual${crlf}" 'dropcache' $null)) 'a reclaim marker without a matching record is the owner''s'
    $both = Get-ODSWslConfigUpdate "[wsl2]${crlf}processors=8${crlf}" 12 'dropcache'
    Check ($both.Memory -ceq 'written' -and $both.Reclaim -ceq 'written' -and
        $both.Text -ceq "[wsl2]${crlf}${marker}${crlf}memory=12GB${crlf}processors=8${crlf}${crlf}[experimental]${crlf}${reclaimMarker}${crlf}autoMemoryReclaim=dropcache${crlf}") 'memory= and autoMemoryReclaim= go in their own sections together'
    $ownerMemory = Get-ODSWslConfigUpdate "[wsl2]${crlf}memory=20GB${crlf}" 12 'dropcache'
    Check ($ownerMemory.Memory -ceq 'owner' -and $ownerMemory.Reclaim -ceq 'written' -and $ownerMemory.Text.Contains('memory=20GB') -and
        -not $ownerMemory.Text.Contains('memory=12GB')) 'the owner''s memory= stays while ODS adds only reclaim'
    $nothing = Get-ODSWslConfigUpdate "[wsl2]${crlf}memory=20GB${crlf}" 0 ''
    Check ($nothing.Memory -ceq 'skipped' -and $nothing.Reclaim -ceq 'skipped' -and $nothing.Text -ceq "[wsl2]${crlf}memory=20GB${crlf}") 'nothing asked, nothing changed'
    Check ((Remove-ODSWslConfigMemoryText $both.Text) -ceq "[wsl2]${crlf}processors=8${crlf}${crlf}[experimental]${crlf}") 'the restore transform removes both of ODS''s lines and keeps the rest'

    # --- Write-ODSWslConfig: backup, record, restore script ----------------------------
    $path = Join-Path $env:USERPROFILE '.wslconfig'
    $settingsPath = Get-ODSWindowsSettingsPath
    $restore = Get-ODSWindowsRestoreScriptPath
    $written = Write-ODSWslConfig 16
    $record = (Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json).WslConfig
    Check ($written.Result -ceq 'written' -and -not $written.Backup -and [IO.File]::ReadAllText($path) -ceq "[wsl2]${crlf}${marker}${crlf}memory=16GB${crlf}" -and
        $record.Value -eq 16 -and $record.HadFile -eq $false -and $record.WrittenSha256 -cmatch '^[0-9a-f]{64}$') 'with no .wslconfig ODS creates one and records that there was none'
    Check ((Get-ODSWslEffectiveMemory 62.5).Source -ceq 'ods' -and (Get-ODSWslEffectiveMemory 62.5).GiB -eq 16) 'the value ODS wrote reads back as ODS''s'
    $bytes = [IO.File]::ReadAllBytes($restore)
    $tokens = $null; $errors = $null
    $null = [Management.Automation.Language.Parser]::ParseInput([Text.Encoding]::ASCII.GetString($bytes), [ref]$tokens, [ref]$errors)
    Check (@($bytes | Where-Object { $_ -gt 127 }).Count -eq 0 -and $errors.Count -eq 0 -and -not ([Text.Encoding]::ASCII.GetString($bytes)).Contains($fixture)) 'the restore script is ASCII, parses and names no machine path'
    $again = Write-ODSWslConfig 16
    Check ($again.Result -ceq 'written' -and (Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json).WslConfig.HadFile -eq $false) 'writing ODS''s value again keeps the record of the original'

    $shell = (Get-Process -Id $PID).Path
    function Invoke-Restore {
        $output = & $shell -NoProfile -ExecutionPolicy Bypass -File $restore -Yes 2>&1
        if ($LASTEXITCODE -ne 0) { throw "the restore script failed: $output" }
        return ($output -join "`n")
    }
    $said = Invoke-Restore
    Check (-not (Test-Path -LiteralPath $path) -and $said -match 'wsl --shutdown' -and
        $null -eq (Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json).WslConfig) 'restore deletes a .wslconfig ODS created, says how WSL picks it up, and drops the record'

    $original = "# mine${crlf}[wsl2]${crlf}processors=8${crlf}"
    [IO.File]::WriteAllText($path, $original)
    $written = Write-ODSWslConfig 16
    Check ($written.Result -ceq 'written' -and (Test-Path -LiteralPath $written.Backup) -and [IO.File]::ReadAllText($written.Backup) -ceq $original -and
        $written.Backup.StartsWith((Get-ODSWindowsSettingsDir))) 'an existing .wslconfig is backed up under %LOCALAPPDATA%\ODS before ODS writes'
    $null = Invoke-Restore
    Check ([IO.File]::ReadAllText($path) -ceq $original) 'restore puts the owner''s earlier file back when nobody edited it since'

    [IO.File]::WriteAllText($path, $original)
    $null = Write-ODSWslConfig 16
    $edited = [IO.File]::ReadAllText($path) + "swap=0${crlf}"
    [IO.File]::WriteAllText($path, $edited)
    $null = Invoke-Restore
    Check ([IO.File]::ReadAllText($path) -ceq "# mine${crlf}[wsl2]${crlf}processors=8${crlf}swap=0${crlf}") 'when the file was edited since, restore removes only ODS''s lines and keeps the edit'

    [IO.File]::WriteAllText($path, "[wsl2]${crlf}memory=24GB${crlf}")
    $kept = Write-ODSWslConfig 16
    Check ($kept.Result -ceq 'owner' -and [IO.File]::ReadAllText($path) -ceq "[wsl2]${crlf}memory=24GB${crlf}" -and
        (Get-ODSWslEffectiveMemory 62.5).Source -ceq 'wslconfig' -and (Get-ODSWslEffectiveMemory 62.5).GiB -eq 24) 'an owner''s memory= is kept, nothing is written, and it is planned with'
    [IO.File]::WriteAllBytes($path, [byte[]](@(0xFF, 0xFE) + [Text.Encoding]::Unicode.GetBytes("[wsl2]${crlf}processors=8${crlf}")))
    $before = [IO.File]::ReadAllBytes($path)
    $refused = Write-ODSWslConfig 16
    Check ($refused.Result -ceq 'unsupported' -and ([Convert]::ToBase64String([IO.File]::ReadAllBytes($path)) -ceq [Convert]::ToBase64String($before))) 'a UTF-16 .wslconfig is never edited'
    [IO.File]::WriteAllBytes($path, [byte[]](@(0x5B, 0x77, 0x73, 0x6C, 0x32, 0x5D, 0x0D, 0x0A, 0xC3, 0x28)))
    $before = [IO.File]::ReadAllBytes($path)
    Check ((Write-ODSWslConfig 16).Result -ceq 'unsupported' -and ([Convert]::ToBase64String([IO.File]::ReadAllBytes($path)) -ceq [Convert]::ToBase64String($before)) -and
        (Get-ODSWslEffectiveMemory 62.5).Source -ceq 'default') 'a .wslconfig that is not valid UTF-8 is never edited, and WSL is planned at its default'
    Remove-Item -LiteralPath $path -Force
    Check ((Get-ODSWslEffectiveMemory 62.5).Source -ceq 'default' -and (Get-ODSWslEffectiveMemory 62.5).GiB -eq 31.25) 'without memory= WSL uses its default'

    # --- Write-ODSWslConfig with autoMemoryReclaim ------------------------------------
    Remove-Item -LiteralPath $settingsPath -Force
    $original = "# mine${crlf}[wsl2]${crlf}processors=8${crlf}"
    [IO.File]::WriteAllText($path, $original)
    $written = Write-ODSWslConfig 12 'dropcache'
    $record = (Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json).WslConfig
    Check ($written.Result -ceq 'written' -and $written.Memory -ceq 'written' -and $written.Reclaim -ceq 'written' -and
        $record.Value -eq 12 -and $record.Reclaim -ceq 'dropcache' -and [IO.File]::ReadAllText($written.Backup) -ceq $original) 'memory and reclaim are written once, backed up and recorded'
    Check ((Read-ODSWslConfigReclaim ([IO.File]::ReadAllText($path)) (Get-ODSWslRecordedReclaim)).Owner -ceq 'ods') 'the reclaim value ODS wrote reads back as ODS''s'
    $null = Invoke-Restore
    Check ([IO.File]::ReadAllText($path) -ceq $original) 'restore puts the earlier file back, without either ODS line'
    [IO.File]::WriteAllText($path, "[wsl2]${crlf}memory=20GB${crlf}")
    $kept = Write-ODSWslConfig 0 'dropcache'
    $record = (Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json).WslConfig
    Check ($kept.Result -ceq 'written' -and $kept.Memory -ceq 'skipped' -and $null -eq $record.Value -and $record.Reclaim -ceq 'dropcache' -and
        (Get-ODSWslEffectiveMemory 62.5).Source -ceq 'wslconfig') 'reclaim alone keeps the owner''s memory= theirs, and the record claims no memory value'
    [IO.File]::WriteAllText($path, [IO.File]::ReadAllText($path).Replace("memory=20GB${crlf}", "memory=20GB${crlf}swap=0${crlf}"))
    $said = Invoke-Restore
    Check ([IO.File]::ReadAllText($path) -ceq "[wsl2]${crlf}memory=20GB${crlf}swap=0${crlf}${crlf}[experimental]${crlf}" -and $said -match 'wsl --shutdown') 'when edited since, restore removes only ODS''s reclaim line and keeps the owner''s memory= and edit'
    Remove-Item -LiteralPath $path -Force

    # --- The page file part of the restore script ---------------------------------------
    Save-ODSWindowsPageFileOriginal -PagingFiles @('?:\pagefile.sys') -Automatic $true
    Complete-ODSWindowsPageFileChange -SizeGB 32
    $said = Invoke-Restore
    Check ($said -match 'ODS set a fixed 32 GB page file on C:; before that Windows had managed automatically by Windows' -and
        $said -match 'needs an administrator' -and $said -match 'Virtual memory' -and $said.Contains('https://discord.gg/4ntNp9MAwC')) 'restore never elevates: for the page file it names the original, the manual steps and where to get help'
    Check ((Format-ODSPagingFilesDescription @('C:\pagefile.sys 98304 98304') $false) -ceq 'a fixed 98304 MB page file on C:' -and
        (Format-ODSPagingFilesDescription @() $false) -ceq 'no page file' -and (Format-ODSPagingFilesDescription @('C:\pagefile.sys 0 0') $false) -ceq 'system managed on C:') 'the original page file is described in words'
} finally {
    foreach ($name in $previous.Keys) { Set-Item -Path "Env:\$name" -Value $previous[$name] -ErrorAction SilentlyContinue }
    $resolved = [IO.Path]::GetFullPath($fixture)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) { throw 'Refusing to clean a fixture outside the temporary directory.' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
Microsoft.PowerShell.Utility\Write-Host "Passed $script:checks Windows WSL memory limit contracts."
