# WSL memory limit for an NVIDIA RTX Spark (PR B design 4.3, decision D5).
#
# Windows charges the memory WSL may use against the same commit limit the
# GPU model needs. By default WSL may use half of the RAM Windows sees, about
# 31 GiB on a factory RTX Spark. With the owner's consent ODS writes a bounded
# memory= under [wsl2] in %USERPROFILE%\.wslconfig, marked as ODS's, and only
# when that default is above the bound. An owner's own memory= is always kept
# and planned with. The old file is backed up and restore-windows-settings.ps1
# puts it back (windows-settings.ps1). The text functions are pure.

. (Join-Path $PSScriptRoot 'private-file.ps1')
. (Join-Path $PSScriptRoot 'windows-settings.ps1')

$script:ODSWslConfigMarker = '# Set by ODS setup: memory limit for WSL. Rerun ODS setup to change it.'
# ODS never limits WSL below this: Ubuntu, Docker and the Portal need it.
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
    # The text without ODS's marker and the memory= line right under it
    # (pure); unchanged when that pair is not there. Other lines stay.
    $parsed = Get-ODSWslConfigLines $Text
    $lines = $parsed.Lines
    for ($index = 0; $index -lt $lines.Count - 1; $index++) {
        if ($lines[$index].Trim() -ceq $script:ODSWslConfigMarker -and $lines[$index + 1] -match '^\s*memory\s*=') {
            $lines.RemoveAt($index + 1)
            $lines.RemoveAt($index)
            if (-not $lines.Count) { return '' }
            return (($lines -join $parsed.Newline) + $parsed.Newline)
        }
    }
    return $Text
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

function Get-ODSWindowsLastBootUtc {
    # When Windows last started, read unelevated; $null when WMI does not say.
    $boot = (Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop).LastBootUpTime
    if ($null -eq $boot) { return $null }
    return ([datetime]$boot).ToUniversalTime()
}

function Get-ODSWslPendingLimit($Record, $LastBootUtc) {
    <#
    .SYNOPSIS
        The record's Pending { SinceUnix; InEffectGiB } while WSL may still
        run with the limit it had before ODS wrote its own (pure): until
        Windows starts again after SinceUnix. ODS clears it when it restarts
        WSL itself. An unknown boot time keeps it, so ODS never plans with
        memory WSL may not have given back. $null when nothing waits.
    #>
    if ($null -eq $Record -or $null -eq $Record.Pending -or $null -eq $Record.Pending.SinceUnix) { return $null }
    if ($null -ne $LastBootUtc) {
        $boot = [DateTimeOffset]::new(([datetime]$LastBootUtc).ToUniversalTime()).ToUnixTimeSeconds()
        if ($boot -gt [long]$Record.Pending.SinceUnix) { return $null }
    }
    return $Record.Pending
}

function Resolve-ODSWslConfigState($Configured, $Record, $LastBootUtc) {
    <#
    .SYNOPSIS
        The WSL memory limit in effect (pure; PR B design 4.3: after a
        declined WSL restart ODS plans with the limit WSL runs with until it
        restarts). GiB and Owner ('ods' or 'owner') of the memory= WSL runs
        with, both $null for WSL's default, and PendingGiB, the limit ODS
        wrote that applies only when WSL restarts ($null when none waits).
        ODS only lowers the limit (it writes one only while the limit in
        effect is above its bound), so the limit in effect is also the
        larger one: safe to plan with now and after WSL restarts.
    #>
    $pending = if ($Configured -and [string]$Configured.Owner -ceq 'ods') { Get-ODSWslPendingLimit $Record $LastBootUtc } else { $null }
    if ($pending) {
        $inEffect = if ($null -ne $pending.InEffectGiB) { [double]$pending.InEffectGiB } else { $null }
        return [pscustomobject]@{ GiB = $inEffect; Owner = $(if ($null -ne $inEffect) { 'ods' } else { $null }); PendingGiB = [double]$Configured.GiB }
    }
    return [pscustomobject]@{ GiB = $(if ($Configured) { $Configured.GiB } else { $null })
        Owner = $(if ($Configured) { $Configured.Owner } else { $null }); PendingGiB = $null }
}

function Get-ODSWslConfigState {
    # Resolve-ODSWslConfigState for this account's .wslconfig and record.
    $file = Read-ODSWslConfigFile
    $record = Get-ODSWindowsSettingsValue (Read-ODSWindowsSettings) 'WslConfig'
    $recorded = if ($null -ne $record -and $null -ne $record.Value) { [double]$record.Value } else { $null }
    $configured = if ($file.Text) { Read-ODSWslConfigMemory $file.Text $recorded } else { $null }
    # Only a limit that waits for WSL to restart needs the boot time.
    $boot = if ($null -ne $record -and $null -ne $record.Pending) { Get-ODSWindowsLastBootUtc } else { $null }
    return (Resolve-ODSWslConfigState $configured $record $boot)
}

function Get-ODSWslEffectiveMemory([double]$VisibleGiB) {
    <#
    .SYNOPSIS
        The memory limit WSL uses: GiB and Source 'wslconfig' (the owner's
        memory=), 'ods' (the value ODS wrote, once WSL runs with it) or
        'default' (half of visible).
    #>
    $state = Get-ODSWslConfigState
    if ($null -ne $state.GiB) {
        return [pscustomobject]@{ GiB = $state.GiB; Source = $(if ($state.Owner -ceq 'ods') { 'ods' } else { 'wslconfig' }) }
    }
    return [pscustomobject]@{ GiB = (Get-ODSWslDefaultMemoryGiB $VisibleGiB); Source = 'default' }
}

function Get-ODSSha256Hex([byte[]]$Bytes) {
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return (-join ($hash.ComputeHash($Bytes) | ForEach-Object { $_.ToString('x2') })) } finally { $hash.Dispose() }
}

function Write-ODSWslConfig([int]$GiB) {
    <#
    .SYNOPSIS
        Write ODS's WSL memory limit to .wslconfig after backing up the old
        file, and record it. Result 'written', 'owner' (the owner's memory= is
        kept, nothing written) or 'unsupported' (a file ODS cannot edit as
        plain text, nothing written). Takes effect when WSL restarts: the
        record keeps the limit WSL runs with until then (Pending).
    #>
    $file = Read-ODSWslConfigFile
    $recorded = Get-ODSWslRecordedGiB
    if (-not $file.Editable) { return [pscustomobject]@{ Result = 'unsupported'; Path = $file.Path; Backup = '' } }
    $text = Set-ODSWslConfigMemoryText $file.Text $GiB $recorded
    if ($null -eq $text) { return [pscustomobject]@{ Result = 'owner'; Path = $file.Path; Backup = '' } }
    $inEffect = (Get-ODSWslConfigState).GiB
    $backup = ''
    if ($file.Exists) {
        $backup = Join-Path (Get-ODSWindowsSettingsDir) ('wslconfig-backup-' + [datetime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '.txt')
        Write-ODSPrivateFileBytes -Path $backup -Bytes $file.Bytes
    }
    $encoding = [Text.UTF8Encoding]::new($file.Bom)
    $bytes = [byte[]]($encoding.GetPreamble() + $encoding.GetBytes($text))
    # A plain write keeps the owner's file and its permissions as they were.
    [IO.File]::WriteAllBytes($file.Path, $bytes)
    Save-ODSWindowsWslConfigChange -Value $GiB -HadFile $file.Exists -Backup $backup -WrittenSha256 (Get-ODSSha256Hex $bytes) -InEffectGiB $inEffect
    return [pscustomobject]@{ Result = 'written'; Path = $file.Path; Backup = $backup }
}
