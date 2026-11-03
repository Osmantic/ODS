# WSL memory limit for an NVIDIA RTX Spark (PR B design 4.3, decision D5),
# and for any PC whose model runs on Windows in memory Windows shares with WSL
# (wsl-memory-budget.ps1).
#
# Windows charges the memory WSL may use against the same commit limit the
# GPU model needs. By default WSL may use half of the RAM Windows sees, about
# 31 GiB on a factory RTX Spark. ODS writes a bounded memory= under [wsl2] in
# %USERPROFILE%\.wslconfig, marked as ODS's, and only when that default is
# above the bound; where WSL supports it, also autoMemoryReclaim= under
# [experimental] (WSL gives memory it no longer uses back to Windows), marked
# the same way. An owner's own memory= or autoMemoryReclaim= is always kept,
# and memory= is planned with. The old file is backed up and
# restore-windows-settings.ps1 puts it back (windows-settings.ps1). The text
# functions are pure.

. (Join-Path $PSScriptRoot 'private-file.ps1')
. (Join-Path $PSScriptRoot 'windows-settings.ps1')

$script:ODSWslConfigMarker = '# Set by ODS setup: memory limit for WSL. Rerun ODS setup to change it.'
$script:ODSWslReclaimMarker = '# Set by ODS setup: WSL gives memory it no longer uses back to Windows. Rerun ODS setup to change it.'
# ODS never limits WSL below this: Ubuntu, Docker and the Portal need it. The
# full ODS stack with Portal ran at 12 GB on an RTX Spark (WSL reported
# 11,933 MiB; 2026-10-25).
$script:ODSWslMemoryFloorGiB = 12

function Get-ODSWslConfigPath {
    if ([string]::IsNullOrWhiteSpace($env:USERPROFILE)) { throw 'Cannot locate %USERPROFILE% for .wslconfig.' }
    return (Join-Path $env:USERPROFILE '.wslconfig')
}

function ConvertFrom-ODSWslMemoryValue([string]$Value) {
    # WSL sizes: 16GB, 16G, 16384MB, 16384M, 1TB, or bytes; GB means GiB.
    $match = [regex]::Match(([string]$Value).Trim(), '^(?<n>[0-9]+(\.[0-9]+)?)\s*(?<unit>[KMGT]B?|B)?$', 'IgnoreCase')
    if (-not $match.Success) { return $null }
    $number = [double]::Parse($match.Groups['n'].Value, [Globalization.CultureInfo]::InvariantCulture)
    $unit = $match.Groups['unit'].Value.ToUpperInvariant().TrimEnd('B')
    $scale = switch ($unit) { 'K' { 1KB } 'M' { 1MB } 'G' { 1GB } 'T' { 1TB } default { 1 } }
    return [math]::Round($number * $scale / 1GB, 2)
}

function Get-ODSWslConfigLines([string]$Text) {
    # Lines without their endings, and the ending the file uses (CRLF for a
    # new file: .wslconfig is a Windows file).
    $newline = if ($Text.Contains("`r`n")) { "`r`n" } elseif ($Text.Contains("`n")) { "`n" } else { "`r`n" }
    $body = $Text
    if ($body.EndsWith($newline)) { $body = $body.Substring(0, $body.Length - $newline.Length) }
    # Built in place: a list written to the pipeline would be enumerated away.
    $lines = [Collections.Generic.List[string]]::new()
    if ($body.Length) { $lines.AddRange([string[]]($body -split [regex]::Escape($newline))) }
    return [pscustomobject]@{ Lines = $lines; Newline = $newline }
}

function Find-ODSWslConfigMemoryLine($Lines) {
    # Index of memory= in the [wsl2] section, -1 when there is none; Section
    # is the index of the [wsl2] header, -1 when there is none.
    $section = -1
    $inWsl2 = $false
    for ($index = 0; $index -lt $Lines.Count; $index++) {
        $line = $Lines[$index].Trim()
        $header = [regex]::Match($line, '^\[(?<name>[^\]]+)\]')
        if ($header.Success) {
            $inWsl2 = $header.Groups['name'].Value.Trim() -ieq 'wsl2'
            if ($inWsl2 -and $section -lt 0) { $section = $index }
            continue
        }
        if ($inWsl2 -and $line -match '^memory\s*=') { return [pscustomobject]@{ Section = $section; Memory = $index } }
    }
    return [pscustomobject]@{ Section = $section; Memory = -1 }
}

function Read-ODSWslConfigMemory([string]$Text, $RecordedGiB = $null) {
    <#
    .SYNOPSIS
        The [wsl2] memory= value as GiB, and Owner 'ods' (ODS's marker is on
        the line above it and windows-settings.json records the same value)
        or 'owner'; $null when the file sets no memory. An owner value WSL
        cannot read has GiB $null and is still the owner's.
    #>
    $parsed = Get-ODSWslConfigLines $Text
    $found = Find-ODSWslConfigMemoryLine $parsed.Lines
    if ($found.Memory -lt 0) { return $null }
    $value = ConvertFrom-ODSWslMemoryValue ($parsed.Lines[$found.Memory] -replace '^\s*memory\s*=', '')
    $marked = $found.Memory -gt 0 -and $parsed.Lines[$found.Memory - 1].Trim() -ceq $script:ODSWslConfigMarker
    $ods = $marked -and $null -ne $value -and $null -ne $RecordedGiB -and [math]::Abs([double]$RecordedGiB - $value) -lt 0.01
    return [pscustomobject]@{ GiB = $value; Owner = $(if ($ods) { 'ods' } else { 'owner' }) }
}

function Get-ODSWslDefaultMemoryGiB([double]$VisibleGiB) {
    # WSL 2's default limit: half of the RAM Windows sees.
    return [math]::Round($VisibleGiB / 2, 2)
}

function Set-ODSWslConfigMemoryText([string]$Text, [int]$GiB, $RecordedGiB = $null) {
    <#
    .SYNOPSIS
        .wslconfig text with ODS's memory=<GiB>GB under [wsl2] and its marker
        above it, every other line and comment kept; $null when the owner set
        memory= (their value stays and is planned with).
    #>
    if ($GiB -lt $script:ODSWslMemoryFloorGiB) { throw "ODS never limits WSL to less than $script:ODSWslMemoryFloorGiB GB (asked for $GiB GB)." }
    $current = Read-ODSWslConfigMemory $Text $RecordedGiB
    if ($current -and $current.Owner -ceq 'owner') { return $null }
    $parsed = Get-ODSWslConfigLines $Text
    $lines = $parsed.Lines
    $value = "memory=${GiB}GB"
    $found = Find-ODSWslConfigMemoryLine $lines
    if ($found.Memory -ge 0) {
        $lines[$found.Memory] = $value
    } elseif ($found.Section -ge 0) {
        $lines.Insert($found.Section + 1, $value)
        $lines.Insert($found.Section + 1, $script:ODSWslConfigMarker)
    } else {
        if ($lines.Count -and $lines[$lines.Count - 1].Trim()) { $lines.Add('') }
        $lines.Add('[wsl2]')
        $lines.Add($script:ODSWslConfigMarker)
        $lines.Add($value)
    }
    return (($lines -join $parsed.Newline) + $parsed.Newline)
}

function Remove-ODSWslConfigMemoryText([string]$Text) {
    # The text without ODS's lines (pure): each marker and the memory= or
    # autoMemoryReclaim= line right under it. Unchanged when no such pair is
    # there. Other lines stay. restore-windows-settings.ps1 carries this.
    $parsed = Get-ODSWslConfigLines $Text
    $lines = $parsed.Lines
    $removed = $false
    for ($index = $lines.Count - 2; $index -ge 0; $index--) {
        $marker = $lines[$index].Trim()
        if (($marker -ceq $script:ODSWslConfigMarker -and $lines[$index + 1] -match '^\s*memory\s*=') -or
            ($marker -ceq $script:ODSWslReclaimMarker -and $lines[$index + 1] -match '^\s*autoMemoryReclaim\s*=')) {
            $lines.RemoveAt($index + 1)
            $lines.RemoveAt($index)
            $removed = $true
        }
    }
    if (-not $removed) { return $Text }
    if (-not $lines.Count) { return '' }
    return (($lines -join $parsed.Newline) + $parsed.Newline)
}

function Find-ODSWslConfigReclaimLine($Lines) {
    # Index of autoMemoryReclaim= in the [experimental] section, -1 when there
    # is none; Section is the index of the [experimental] header, -1 when none.
    $section = -1
    $inSection = $false
    for ($index = 0; $index -lt $Lines.Count; $index++) {
        $line = $Lines[$index].Trim()
        $header = [regex]::Match($line, '^\[(?<name>[^\]]+)\]')
        if ($header.Success) {
            $inSection = $header.Groups['name'].Value.Trim() -ieq 'experimental'
            if ($inSection -and $section -lt 0) { $section = $index }
            continue
        }
        if ($inSection -and $line -match '^autoMemoryReclaim\s*=') { return [pscustomobject]@{ Section = $section; Reclaim = $index } }
    }
    return [pscustomobject]@{ Section = $section; Reclaim = -1 }
}

function Read-ODSWslConfigReclaim([string]$Text, $RecordedReclaim = $null) {
    # autoMemoryReclaim= under [experimental]: Value, and Owner 'ods' (ODS's
    # marker above it and windows-settings.json records the same value) or
    # 'owner'; $null when the file sets none (pure).
    $parsed = Get-ODSWslConfigLines $Text
    $found = Find-ODSWslConfigReclaimLine $parsed.Lines
    if ($found.Reclaim -lt 0) { return $null }
    $value = ($parsed.Lines[$found.Reclaim] -replace '^\s*autoMemoryReclaim\s*=', '').Trim()
    $marked = $found.Reclaim -gt 0 -and $parsed.Lines[$found.Reclaim - 1].Trim() -ceq $script:ODSWslReclaimMarker
    $ods = $marked -and $RecordedReclaim -and [string]$RecordedReclaim -ceq $value
    return [pscustomobject]@{ Value = $value; Owner = $(if ($ods) { 'ods' } else { 'owner' }) }
}

function Set-ODSWslConfigReclaimText([string]$Text, [string]$Value, $RecordedReclaim = $null) {
    <#
    .SYNOPSIS
        .wslconfig text with ODS's autoMemoryReclaim=<Value> under
        [experimental] and its marker above it, every other line kept; $null
        when the owner set autoMemoryReclaim= (theirs stays) (pure).
    #>
    if ($Value -cnotin @('dropcache', 'gradual')) { throw "Unknown autoMemoryReclaim value '$Value'." }
    $current = Read-ODSWslConfigReclaim $Text $RecordedReclaim
    if ($current -and $current.Owner -ceq 'owner') { return $null }
    $parsed = Get-ODSWslConfigLines $Text
    $lines = $parsed.Lines
    $line = "autoMemoryReclaim=$Value"
    $found = Find-ODSWslConfigReclaimLine $lines
    if ($found.Reclaim -ge 0) {
        $lines[$found.Reclaim] = $line
    } elseif ($found.Section -ge 0) {
        $lines.Insert($found.Section + 1, $line)
        $lines.Insert($found.Section + 1, $script:ODSWslReclaimMarker)
    } else {
        if ($lines.Count -and $lines[$lines.Count - 1].Trim()) { $lines.Add('') }
        $lines.Add('[experimental]')
        $lines.Add($script:ODSWslReclaimMarker)
        $lines.Add($line)
    }
    return (($lines -join $parsed.Newline) + $parsed.Newline)
}

function Get-ODSWslConfigUpdate([string]$Text, [int]$GiB, [string]$Reclaim, $RecordedGiB = $null, $RecordedReclaim = $null) {
    <#
    .SYNOPSIS
        What ODS would write (pure): Text, and Memory and Reclaim each
        'written' (ODS sets or updates its own line), 'owner' (the owner's
        line stays) or 'skipped' (not asked: GiB 0, Reclaim empty).
    #>
    $memory = 'skipped'
    if ($GiB -gt 0) {
        $changed = Set-ODSWslConfigMemoryText $Text $GiB $RecordedGiB
        if ($null -eq $changed) { $memory = 'owner' } else { $Text = $changed; $memory = 'written' }
    }
    $reclaimState = 'skipped'
    if ($Reclaim) {
        $changed = Set-ODSWslConfigReclaimText $Text $Reclaim $RecordedReclaim
        if ($null -eq $changed) { $reclaimState = 'owner' } else { $Text = $changed; $reclaimState = 'written' }
    }
    return [pscustomobject]@{ Text = $Text; Memory = $memory; Reclaim = $reclaimState }
}

function Read-ODSWslConfigFile {
    <#
    .SYNOPSIS
        .wslconfig as text: Exists, Text, Bytes and Editable. A file that is
        not plain UTF-8 (UTF-16 from some editors, for example) is never
        edited; its value is still read when it can be.
    #>
    $path = Get-ODSWslConfigPath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        return [pscustomobject]@{ Path = $path; Exists = $false; Text = ''; Bytes = [byte[]]@(); Editable = $true; Bom = $false }
    }
    $bytes = [IO.File]::ReadAllBytes($path)
    $bom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
    $utf16 = $bytes.Length -ge 2 -and (($bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) -or ($bytes[0] -eq 0xFE -and $bytes[1] -eq 0xFF))
    if ($utf16) {
        return [pscustomobject]@{ Path = $path; Exists = $true; Text = [Text.Encoding]::Unicode.GetString($bytes).TrimStart([char]0xFEFF); Bytes = $bytes; Editable = $false; Bom = $false }
    }
    $offset = if ($bom) { 3 } else { 0 }
    try {
        $text = [Text.UTF8Encoding]::new($false, $true).GetString($bytes, $offset, $bytes.Length - $offset)
    } catch [Text.DecoderFallbackException] {
        return [pscustomobject]@{ Path = $path; Exists = $true; Text = ''; Bytes = $bytes; Editable = $false; Bom = $bom }
    }
    return [pscustomobject]@{ Path = $path; Exists = $true; Text = $text; Bytes = $bytes; Editable = $true; Bom = $bom }
}

function Get-ODSWslRecordedGiB {
    $record = Get-ODSWindowsSettingsValue (Read-ODSWindowsSettings) 'WslConfig'
    if ($null -eq $record -or $null -eq $record.Value) { return $null }
    return [double]$record.Value
}

function Get-ODSWslRecordedReclaim {
    # The autoMemoryReclaim value ODS wrote, $null when it wrote none.
    $record = Get-ODSWindowsSettingsValue (Read-ODSWindowsSettings) 'WslConfig'
    if ($null -eq $record -or -not $record.Reclaim) { return $null }
    return [string]$record.Reclaim
}

function Get-ODSWslEffectiveMemory([double]$VisibleGiB) {
    <#
    .SYNOPSIS
        The memory limit WSL uses: GiB and Source 'wslconfig' (the owner's
        memory=), 'ods' (the value ODS wrote) or 'default' (half of visible).
    #>
    $file = Read-ODSWslConfigFile
    $configured = if ($file.Text) { Read-ODSWslConfigMemory $file.Text (Get-ODSWslRecordedGiB) } else { $null }
    if ($configured -and $null -ne $configured.GiB) {
        return [pscustomobject]@{ GiB = $configured.GiB; Source = $(if ($configured.Owner -ceq 'ods') { 'ods' } else { 'wslconfig' }) }
    }
    return [pscustomobject]@{ GiB = (Get-ODSWslDefaultMemoryGiB $VisibleGiB); Source = 'default' }
}

function Get-ODSSha256Hex([byte[]]$Bytes) {
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return (-join ($hash.ComputeHash($Bytes) | ForEach-Object { $_.ToString('x2') })) } finally { $hash.Dispose() }
}

function Write-ODSWslConfig([int]$GiB, [string]$Reclaim = '') {
    <#
    .SYNOPSIS
        Write ODS's WSL memory limit (GiB, 0 to leave memory= alone) and, when
        given, its autoMemoryReclaim value to .wslconfig after backing up the
        old file, and record them. Result 'written', 'owner' (the owner's
        values are kept, nothing written) or 'unsupported' (a file ODS cannot
        edit as plain text, nothing written); Memory and Reclaim say what
        happened to each (Get-ODSWslConfigUpdate). Takes effect when WSL
        restarts.
    #>
    $file = Read-ODSWslConfigFile
    $recorded = Get-ODSWslRecordedGiB
    $recordedReclaim = Get-ODSWslRecordedReclaim
    if (-not $file.Editable) {
        return [pscustomobject]@{ Result = 'unsupported'; Path = $file.Path; Backup = ''; Memory = 'unsupported'; Reclaim = 'unsupported' }
    }
    $update = Get-ODSWslConfigUpdate $file.Text $GiB $Reclaim $recorded $recordedReclaim
    if ($update.Memory -cne 'written' -and $update.Reclaim -cne 'written') {
        return [pscustomobject]@{ Result = 'owner'; Path = $file.Path; Backup = ''; Memory = $update.Memory; Reclaim = $update.Reclaim }
    }
    $text = $update.Text
    $backup = ''
    if ($file.Exists) {
        # Unique per write: two writes in one second must not overwrite the
        # backup the record points to.
        $stamp = [datetime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 6)
        $backup = Join-Path (Get-ODSWindowsSettingsDir) ('wslconfig-backup-' + $stamp + '.txt')
        Write-ODSPrivateFileBytes -Path $backup -Bytes $file.Bytes
    }
    $encoding = [Text.UTF8Encoding]::new($file.Bom)
    $bytes = [byte[]]($encoding.GetPreamble() + $encoding.GetBytes($text))
    # A plain write keeps the owner's file and its permissions as they were.
    [IO.File]::WriteAllBytes($file.Path, $bytes)
    # The record names the values ODS owns in the file now.
    $value = if ($update.Memory -ceq 'written') { $GiB } else { $recorded }
    $reclaimValue = if ($update.Reclaim -ceq 'written') { $Reclaim } else { [string]$recordedReclaim }
    Save-ODSWindowsWslConfigChange -Value $value -HadFile $file.Exists -Backup $backup -WrittenSha256 (Get-ODSSha256Hex $bytes) -Reclaim $reclaimValue
    return [pscustomobject]@{ Result = 'written'; Path = $file.Path; Backup = $backup; Memory = $update.Memory; Reclaim = $update.Reclaim }
}
