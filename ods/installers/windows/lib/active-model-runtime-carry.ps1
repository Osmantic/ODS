# ============================================================================
# ODS Windows Installer -- Active Model Runtime Carry
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Preserve LLAMA_ARG_MMPROJ and the guarded paired chat-template
#          override across New-ODSEnv reruns when the active model identity is
#          unchanged. Follows the Linux reference in scripts/preserve-active-model.py
#          (carried_projector / carried_chat_template).
#
# Dot-sourced by env-generator.ps1 at definition time. The helper is called
# only from New-ODSEnv's carry phase, after the usual model tuning carry and
# before extension carry. No network, no execution, no registry writes.
# ============================================================================

function Get-ODSCarryLiteral {
    <# Parse a dotenv value literally, matching Get-TuningLiteral in
       env-generator.ps1: trim, then strip one matching pair of quotes. #>
    param([AllowEmptyString()][string]$Value)
    $text = ([string]$Value).Trim()
    if ($text.Length -ge 2 -and (
        ($text[0] -eq "'" -and $text[$text.Length - 1] -eq "'") -or
        ($text[0] -eq '"' -and $text[$text.Length - 1] -eq '"'))) {
        $text = $text.Substring(1, $text.Length - 2).Trim()
    }
    return $text
}

function Test-ODSCarrySafeBasename {
    <# A single path component: no separators, colon, NUL, control chars,
       and not '.' or '..'. #>
    param([AllowEmptyString()][string]$Name)
    if ([string]::IsNullOrEmpty($Name)) { return $false }
    if ($Name -ceq '.' -or $Name -ceq '..') { return $false }
    foreach ($ch in $Name.ToCharArray()) {
        if ($ch -eq '/' -or $ch -eq '\' -or $ch -eq ':' -or $ch -eq [char]0) { return $false }
        if ([char]::IsControl($ch)) { return $false }
    }
    return $true
}

function Test-ODSCarryRegularFile {
    <# True when $Path is a regular file (not a directory, not a reparse
       point / symlink) with size > 0. Uses lstat semantics via
       FileAttributes so a reparse point is rejected before following it. #>
    param([string]$Path)
    try {
        $info = [System.IO.FileInfo]::new($Path)
        if (-not $info.Exists) { return $false }
        if (($info.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { return $false }
        if ($info.Length -le 0) { return $false }
        return $true
    } catch {
        return $false
    }
}

function Get-ODSCarryPositiveInt {
    <# Return a positive Int64 from a JSON value, or $null when the value is
       absent, non-integer, or not positive. #>
    param($Value)
    if ($null -eq $Value) { return $null }
    if ($Value -is [int] -or $Value -is [long]) {
        if ([int64]$Value -gt 0) { return [int64]$Value }
        return $null
    }
    if ($Value -is [string]) {
        $parsed = [int64]0
        if ([int64]::TryParse($Value, [ref]$parsed) -and $parsed -gt 0) { return $parsed }
    }
    return $null
}

function Read-ODSCarryJson {
    <# Read and parse a JSON file, returning $null on any failure. #>
    param([string]$Path)
    try {
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
        $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 -ErrorAction Stop
        if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
        return ($raw | ConvertFrom-Json -ErrorAction Stop)
    } catch {
        return $null
    }
}

function Get-ODSCarryActiveRecord {
    <# Resolve the unique library record for the active GGUF from
       config/model-library.json (catalog) plus data/model-imports.json
       (source=huggingface), matching load_records precedence: catalog first,
       imports skipped when id or case-insensitive gguf_file already seen.
       Returns $null when no unique intended record exists. #>
    param(
        [string]$InstallDir,
        [string]$GgufFile
    )
    if ([string]::IsNullOrEmpty($GgufFile)) { return $null }
    $catalogPath = Join-Path (Join-Path $InstallDir 'config') 'model-library.json'
    $importsPath = Join-Path (Join-Path $InstallDir 'data') 'model-imports.json'

    $records = New-Object 'System.Collections.Generic.List[object]'
    $seenIds = @{}
    $seenFiles = @{}

    $catalog = Read-ODSCarryJson -Path $catalogPath
    if ($null -eq $catalog -or $catalog.models -isnot [array]) { return $null }
    if ($null -ne $catalog -and $null -ne $catalog.models -and $catalog.models -is [System.Array]) {
        foreach ($item in $catalog.models) {
            if ($null -eq $item -or -not ($item -is [pscustomobject])) { return $null }
            [void]$records.Add($item)
            $id = [string]$item.id
            if ($id) { $seenIds[$id] = $true }
            $file = ([string]$item.gguf_file).ToLowerInvariant()
            if ($file) { $seenFiles[$file] = $true }
        }
    }

    $imports = Read-ODSCarryJson -Path $importsPath
    if ($null -ne $imports -and $null -ne $imports.models -and $imports.models -is [System.Array]) {
        foreach ($item in $imports.models) {
            if ($null -eq $item -or -not ($item -is [pscustomobject])) { continue }
            if ([string]$item.source -cne 'huggingface') { continue }
            $id = [string]$item.id
            $file = ([string]$item.gguf_file).ToLowerInvariant()
            if (-not $id -or -not $file) { continue }
            if ($seenIds.ContainsKey($id) -or $seenFiles.ContainsKey($file)) { continue }
            [void]$records.Add($item)
            $seenIds[$id] = $true
            $seenFiles[$file] = $true
        }
    }

    $matchingRecords = @()
    foreach ($record in $records) {
        if ([string]$record.gguf_file -ceq $GgufFile) { $matchingRecords += $record }
    }
    if ($matchingRecords.Count -ne 1) { return $null }
    return $matchingRecords[0]
}

function Get-ODSCarryModelStore {
    <# Resolve the active model store directory. Default is
       InstallDir/data/models. A registered store must have a unique id
       matching the current store, a rooted existing hostPath, and
       containerPath /model-stores/<id>. Returns $null on any failure. #>
    param(
        [string]$InstallDir,
        [string]$StoreId
    )
    $dataDir = Join-Path $InstallDir 'data'
    if ([string]::IsNullOrEmpty($StoreId) -or $StoreId -ceq 'default') {
        return (Join-Path $dataDir 'models')
    }
    if ($StoreId -cnotmatch '^[a-z][a-z0-9-]{0,47}$') { return $null }
    $registryPath = Join-Path $dataDir 'model-stores.json'
    $registry = Read-ODSCarryJson -Path $registryPath
    if ($null -eq $registry) { return $null }
    if (($registry.schemaVersion -isnot [int] -and $registry.schemaVersion -isnot [long]) -or $registry.schemaVersion -ne 1) { return $null }
    if ($null -eq $registry.stores -or -not ($registry.stores -is [System.Array])) { return $null }
    $matched = @()
    foreach ($entry in $registry.stores) {
        if ($null -eq $entry -or -not ($entry -is [pscustomobject])) { continue }
        if ([string]$entry.id -ceq $StoreId) { $matched += $entry }
    }
    if ($matched.Count -ne 1) { return $null }
    $entry = $matched[0]
    $hostPath = [string]$entry.hostPath
    if ([string]::IsNullOrEmpty($hostPath)) { return $null }
    if (-not [System.IO.Path]::IsPathRooted($hostPath)) { return $null }
    if ([string]$entry.containerPath -cne "/model-stores/$StoreId") { return $null }
    try {
        if (-not (Test-Path -LiteralPath $hostPath -PathType Container)) { return $null }
        $resolved = (Resolve-Path -LiteralPath $hostPath).Path
        return $resolved
    } catch {
        return $null
    }
}

function Get-ODSCarryProjectorLine {
    <# Return the LLAMA_ARG_MMPROJ line to carry, or $null. Requires the
       record's mmproj_file to be a safe basename, a regular non-reparse
       positive-size file in the selected store, not a shard/weight, and
       matching the recorded positive size when present. #>
    param(
        $Record,
        [string]$StoreDir,
        [string]$GgufFile
    )
    if ($null -eq $Record -or [string]::IsNullOrEmpty($StoreDir)) { return $null }
    if (-not (Test-ODSCarrySafeBasename $GgufFile)) { return $null }
    if (-not (Test-ODSCarryRegularFile (Join-Path $StoreDir $GgufFile))) { return $null }
    $mmproj = [string]$Record.mmproj_file
    if (-not (Test-ODSCarrySafeBasename -Name $mmproj)) { return $null }
    if ($mmproj -cnotmatch '\.gguf$') { return $null }
    if ($mmproj -ceq $GgufFile) { return $null }
    if ($null -ne $Record.gguf_parts -and $Record.gguf_parts -is [System.Array]) {
        foreach ($part in $Record.gguf_parts) {
            if ($null -eq $part) { continue }
            if ([string]$part.file -ceq $mmproj) { return $null }
        }
    }
    $projectorPath = Join-Path $StoreDir $mmproj
    if (-not (Test-ODSCarryRegularFile -Path $projectorPath)) { return $null }
    $expected = Get-ODSCarryPositiveInt -Value $Record.mmproj_size_bytes
    if ($null -ne $Record.mmproj_size_bytes -and $null -eq $expected) { return $null }
    if ($null -ne $expected) {
        try {
            $actual = ([System.IO.FileInfo]::new($projectorPath)).Length
        } catch {
            return $null
        }
        if ($actual -ne $expected) { return $null }
    }
    return ('LLAMA_ARG_MMPROJ=/models/{0}' -f $mmproj)
}

function Get-ODSCarryChatTemplateLines {
    <# Return the paired MODEL_CHAT_TEMPLATE_OVERRIDE and
       LLAMA_ARG_CHAT_TEMPLATE_FILE lines, or an empty array. Both prior
       keys must be set; the index must be schemaVersion 1 with a unique
       matching id; the container path must be /chat-templates/<entry.file>;
       entry.file must be upstream-b<digits>/<safe>.jinja; the file must be a
       regular non-reparse file whose SHA-256 matches fileSha256. #>
    param(
        [hashtable]$PreviousEnv,
        [string]$InstallDir
    )
    $result = @()
    if ($null -eq $PreviousEnv) { return $result }
    if (-not $PreviousEnv.ContainsKey('MODEL_CHAT_TEMPLATE_OVERRIDE')) { return $result }
    if (-not $PreviousEnv.ContainsKey('LLAMA_ARG_CHAT_TEMPLATE_FILE')) { return $result }
    $override = Get-ODSCarryLiteral ([string]$PreviousEnv['MODEL_CHAT_TEMPLATE_OVERRIDE'])
    $containerFile = Get-ODSCarryLiteral ([string]$PreviousEnv['LLAMA_ARG_CHAT_TEMPLATE_FILE'])
    if ([string]::IsNullOrEmpty($override) -or [string]::IsNullOrEmpty($containerFile)) { return $result }
    if ($override -cnotmatch '^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$') { return $result }

    $root = Join-Path (Join-Path $InstallDir 'config') 'chat-templates'
    $indexPath = Join-Path $root 'index.json'
    $index = Read-ODSCarryJson -Path $indexPath
    if ($null -eq $index) { return $result }
    if (($index.schemaVersion -isnot [int] -and $index.schemaVersion -isnot [long]) -or $index.schemaVersion -ne 1) { return $result }
    if (@($index.PSObject.Properties.Name).Count -ne 2) { return $result }
    if ($null -eq $index.overrides -or -not ($index.overrides -is [System.Array])) { return $result }

    $matched = @()
    $ids = @{}
    $claimed = @{}
    foreach ($entry in $index.overrides) {
        if ($null -eq $entry -or -not ($entry -is [pscustomobject])) { return $result }
        $expectedFields = @('id','embeddedTemplateSha256','file','fileSha256','builds','reason')
        if (@($entry.PSObject.Properties.Name).Count -ne $expectedFields.Count) { return $result }
        foreach ($field in $expectedFields) { if ($null -eq $entry.PSObject.Properties[$field]) { return $result } }
        $id = [string]$entry.id
        if ($entry.id -isnot [string] -or $id -cnotmatch '^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$') { return $result }
        foreach ($field in @('embeddedTemplateSha256','fileSha256')) {
            if ($entry.$field -isnot [string] -or $entry.$field -cnotmatch '^[0-9a-f]{64}$') { return $result }
        }
        if ($entry.file -isnot [string] -or $entry.file -cnotmatch '^upstream-b[0-9]+/[A-Za-z0-9][A-Za-z0-9._-]*\.jinja$') { return $result }
        if ($entry.reason -isnot [string] -or [string]::IsNullOrWhiteSpace($entry.reason) -or $entry.reason.Length -gt 200 -or $entry.reason.Contains("`n")) { return $result }
        if ($entry.builds -isnot [array] -or $entry.builds.Count -eq 0) { return $result }
        foreach ($build in $entry.builds) {
            if ($build -isnot [string] -or $build -cnotmatch '^b[0-9]+$') { return $result }
            $pair = [string]$entry.embeddedTemplateSha256 + ':' + $build
            if ($claimed.ContainsKey($pair)) { return $result }
            $claimed[$pair] = $true
        }
        if ($id) {
            if ($ids.ContainsKey($id)) { return $result }
            $ids[$id] = $true
        }
        if ($id -ceq $override) { $matched += $entry }
    }
    if ($matched.Count -ne 1) { return $result }
    $entry = $matched[0]

    $file = [string]$entry.file
    if ($file -cnotmatch '^upstream-b[0-9]+/[A-Za-z0-9][A-Za-z0-9._-]*\.jinja$') { return $result }
    if ($containerFile -cne "/chat-templates/$file") { return $result }
    $fileSha = [string]$entry.fileSha256
    if ($fileSha -cnotmatch '^[0-9a-f]{64}$') { return $result }

    $parts = $file -split '/', 2
    $templatePath = Join-Path (Join-Path $root $parts[0]) $parts[1]
    if (-not (Test-ODSCarryRegularFile -Path $templatePath)) { return $result }
    try {
        $actual = (Get-FileHash -LiteralPath $templatePath -Algorithm SHA256).Hash.ToLowerInvariant()
    } catch {
        return $result
    }
    if ($actual -cne $fileSha) { return $result }

    $result += ('MODEL_CHAT_TEMPLATE_OVERRIDE={0}' -f $PreviousEnv['MODEL_CHAT_TEMPLATE_OVERRIDE'])
    $result += ('LLAMA_ARG_CHAT_TEMPLATE_FILE={0}' -f $PreviousEnv['LLAMA_ARG_CHAT_TEMPLATE_FILE'])
    return $result
}

function Get-ODSCarriedActiveModelRuntimeLines {
    <#
    .SYNOPSIS
        Return previous .env lines for LLAMA_ARG_MMPROJ and the paired
        MODEL_CHAT_TEMPLATE_OVERRIDE / LLAMA_ARG_CHAT_TEMPLATE_FILE that the
        new template does not write, when the active model identity is
        unchanged.
    .DESCRIPTION
        Identity requires the same GGUF_FILE and ODS_ACTIVE_MODEL_STORE
        (case-sensitive, after literal quote parsing), prior and current
        LLM_BACKEND both llama-server, prior and current ODS_MODE both local,
        with the same model store. GPU changes do not change which projector
        belongs to this model when the selected engine remains llama-server.
        New generated values win; the template pair is all-or-nothing.
    #>
    param(
        [hashtable]$PreviousEnv,
        [AllowEmptyString()][string]$NewContent,
        [AllowEmptyString()][string]$CurrentGgufFile,
        [AllowEmptyString()][string]$CurrentModelStore,
        [AllowEmptyString()][string]$CurrentOdsMode,
        [AllowEmptyString()][string]$CurrentLlmBackend,
        [string]$InstallDir
    )
    $result = @()
    if ($null -eq $PreviousEnv) { return $result }
    if (-not $PreviousEnv.ContainsKey('LLAMA_ARG_MMPROJ') -and
        -not $PreviousEnv.ContainsKey('MODEL_CHAT_TEMPLATE_OVERRIDE') -and
        -not $PreviousEnv.ContainsKey('LLAMA_ARG_CHAT_TEMPLATE_FILE')) { return $result }
    if ([string]::IsNullOrEmpty($CurrentGgufFile)) { return $result }
    if ($CurrentOdsMode -cne 'local') { return $result }
    if ($CurrentLlmBackend -cne 'llama-server') { return $result }

    if (-not $PreviousEnv.ContainsKey('GGUF_FILE')) { return $result }
    if (-not $PreviousEnv.ContainsKey('ODS_MODE')) { return $result }
    if (-not $PreviousEnv.ContainsKey('LLM_BACKEND')) { return $result }

    $prevGguf = Get-ODSCarryLiteral ([string]$PreviousEnv['GGUF_FILE'])
    $prevOdsMode = Get-ODSCarryLiteral ([string]$PreviousEnv['ODS_MODE'])
    $prevLlmBackend = Get-ODSCarryLiteral ([string]$PreviousEnv['LLM_BACKEND'])
    $prevModelStore = 'default'
    if ($PreviousEnv.ContainsKey('ODS_ACTIVE_MODEL_STORE')) {
        $prevModelStore = Get-ODSCarryLiteral ([string]$PreviousEnv['ODS_ACTIVE_MODEL_STORE'])
    }

    if (-not [string]::Equals($prevGguf, $CurrentGgufFile, [StringComparison]::Ordinal)) { return $result }
    if (-not [string]::Equals($prevModelStore, $CurrentModelStore, [StringComparison]::Ordinal)) { return $result }
    if (-not [string]::Equals($prevOdsMode, $CurrentOdsMode, [StringComparison]::Ordinal)) { return $result }
    if (-not [string]::Equals($prevLlmBackend, $CurrentLlmBackend, [StringComparison]::Ordinal)) { return $result }

    $record = Get-ODSCarryActiveRecord -InstallDir $InstallDir -GgufFile $CurrentGgufFile
    $storeDir = Get-ODSCarryModelStore -InstallDir $InstallDir -StoreId $CurrentModelStore
    if ($null -eq $record -or $null -eq $storeDir -or
        -not (Test-ODSCarrySafeBasename $CurrentGgufFile) -or
        -not (Test-ODSCarryRegularFile (Join-Path $storeDir $CurrentGgufFile))) { return $result }

    if ($null -ne $record -and $null -ne $storeDir -and $PreviousEnv.ContainsKey('LLAMA_ARG_MMPROJ')) {
        if ($NewContent -cnotmatch '(?m)^LLAMA_ARG_MMPROJ=') {
            $projectorLine = Get-ODSCarryProjectorLine -Record $record -StoreDir $storeDir -GgufFile $CurrentGgufFile
            $previousProjector = Get-ODSCarryLiteral ([string]$PreviousEnv['LLAMA_ARG_MMPROJ'])
            if ($null -ne $projectorLine -and $projectorLine -ceq ('LLAMA_ARG_MMPROJ=' + $previousProjector)) {
                $result += ('LLAMA_ARG_MMPROJ=' + $PreviousEnv['LLAMA_ARG_MMPROJ'])
            }
        }
    }

    $hasOverride = $NewContent -cmatch '(?m)^MODEL_CHAT_TEMPLATE_OVERRIDE='
    $hasTemplateFile = $NewContent -cmatch '(?m)^LLAMA_ARG_CHAT_TEMPLATE_FILE='
    if (-not $hasOverride -and -not $hasTemplateFile) {
        $templateLines = @(Get-ODSCarryChatTemplateLines -PreviousEnv $PreviousEnv -InstallDir $InstallDir)
        if ($templateLines.Count -eq 2) { $result += $templateLines }
    }

    return $result
}
