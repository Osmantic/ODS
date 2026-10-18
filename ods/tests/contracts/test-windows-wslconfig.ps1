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

    # --- The limit in effect until WSL restarts (pure) -----------------------------------
    $since = 1792000000
    $waiting = [pscustomobject]@{ Value = 16; Pending = [pscustomobject]@{ SinceUnix = $since; InEffectGiB = $null } }
    $before = [DateTimeOffset]::FromUnixTimeSeconds($since - 3600).UtcDateTime
    $after = [DateTimeOffset]::FromUnixTimeSeconds($since + 60).UtcDateTime
    Check ($null -eq (Get-ODSWslPendingLimit $null $after) -and $null -eq (Get-ODSWslPendingLimit ([pscustomobject]@{ Value = 16; Pending = $null }) $before) -and
        $null -eq (Get-ODSWslPendingLimit ([pscustomobject]@{ Value = 16 }) $before)) 'no record, or a record without Pending (written before this release), waits for nothing'
    Check ((Get-ODSWslPendingLimit $waiting $before).SinceUnix -eq $since -and (Get-ODSWslPendingLimit $waiting $null).SinceUnix -eq $since -and
        $null -eq (Get-ODSWslPendingLimit $waiting $after)) 'a written limit waits until Windows starts after it was written; an unknown boot time keeps it waiting'
    $odsValue = [pscustomobject]@{ GiB = 16.0; Owner = 'ods' }
    $state = Resolve-ODSWslConfigState $odsValue $waiting $before
    Check ($null -eq $state.GiB -and $null -eq $state.Owner -and $state.PendingGiB -eq 16) 'while ODS''s 16 GB waits for a WSL restart, WSL''s default is the limit in effect (PR B design 4.3)'
    $fromOds = [pscustomobject]@{ Value = 16; Pending = [pscustomobject]@{ SinceUnix = $since; InEffectGiB = 20 } }
    $state = Resolve-ODSWslConfigState $odsValue $fromOds $before
    Check ($state.GiB -eq 20 -and $state.Owner -ceq 'ods' -and $state.PendingGiB -eq 16) 'a new ODS value waits while the earlier ODS value stays in effect'
    $state = Resolve-ODSWslConfigState $odsValue $waiting $after
    Check ($state.GiB -eq 16 -and $state.Owner -ceq 'ods' -and $null -eq $state.PendingGiB) 'after a Windows start the value ODS wrote is in effect'
    $state = Resolve-ODSWslConfigState ([pscustomobject]@{ GiB = 24.0; Owner = 'owner' }) $waiting $before
    Check ($state.GiB -eq 24 -and $state.Owner -ceq 'owner' -and $null -eq $state.PendingGiB) 'an owner''s memory= is planned with, as before'
    $state = Resolve-ODSWslConfigState $null $null $null
    Check ($null -eq $state.GiB -and $null -eq $state.Owner -and $null -eq $state.PendingGiB) 'without memory= WSL runs with its default and nothing waits'

    # --- Write-ODSWslConfig: backup, record, restore script ----------------------------
    $script:lastBoot = [datetime]::UtcNow.AddHours(-1)
    function Get-ODSWindowsLastBootUtc { return $script:lastBoot }
    $path = Join-Path $env:USERPROFILE '.wslconfig'
    $settingsPath = Get-ODSWindowsSettingsPath
    $restore = Get-ODSWindowsRestoreScriptPath
    $start = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $written = Write-ODSWslConfig 16
    $record = (Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json).WslConfig
    Check ($written.Result -ceq 'written' -and -not $written.Backup -and [IO.File]::ReadAllText($path) -ceq "[wsl2]${crlf}${marker}${crlf}memory=16GB${crlf}" -and
        $record.Value -eq 16 -and $record.HadFile -eq $false -and $record.WrittenSha256 -cmatch '^[0-9a-f]{64}$') 'with no .wslconfig ODS creates one and records that there was none'
    Check ($record.Pending.SinceUnix -ge $start -and $record.Pending.SinceUnix -le [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() -and $null -eq $record.Pending.InEffectGiB -and
        (Get-ODSWslEffectiveMemory 62.5).Source -ceq 'default' -and (Get-ODSWslEffectiveMemory 62.5).GiB -eq 31.25 -and
        (Get-ODSWslConfigState).PendingGiB -eq 16) 'the record keeps WSL''s default as the limit in effect until WSL restarts; the written 16 GB waits'
    $sha = $record.WrittenSha256
    Complete-ODSWindowsWslConfigApplied
    $record = (Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json).WslConfig
    Check ($null -eq $record.Pending -and $record.Value -eq 16 -and $record.HadFile -eq $false -and $record.WrittenSha256 -ceq $sha -and
        (Get-ODSWslEffectiveMemory 62.5).Source -ceq 'ods' -and (Get-ODSWslEffectiveMemory 62.5).GiB -eq 16 -and $null -eq (Get-ODSWslConfigState).PendingGiB) 'once ODS restarted WSL the value ODS wrote reads back as ODS''s and nothing else in the record changes'
    $null = Write-ODSWslConfig 20
    $null = Write-ODSWslConfig 24
    $record = (Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json).WslConfig
    Check ($record.Value -eq 24 -and $record.Pending.InEffectGiB -eq 16 -and (Get-ODSWslEffectiveMemory 62.5).GiB -eq 16 -and (Get-ODSWslConfigState).PendingGiB -eq 24) 'values written again before WSL restarts keep the one WSL runs with (16 GB) in effect'
    $script:lastBoot = [datetime]::UtcNow.AddMinutes(5)
    Check ((Get-ODSWslEffectiveMemory 62.5).GiB -eq 24 -and (Get-ODSWslEffectiveMemory 62.5).Source -ceq 'ods') 'a Windows start after the write puts the latest value in effect'
    $script:lastBoot = [datetime]::UtcNow.AddHours(-1)
    $null = Write-ODSWslConfig 16
    Complete-ODSWindowsWslConfigApplied
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
