. (Join-Path $PSScriptRoot 'portal-model-path.ps1')

function Initialize-ODSPortalModelStore {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][string]$WindowsPath,
        [Parameter(Mandatory=$true)][hashtable]$Model,
        [switch]$DryRun
    )
    $paths = Get-ODSPortalModelStorePaths $WindowsPath
    $filename = [string]$Model.GgufFile
    if ([string]::IsNullOrWhiteSpace($filename) -or $filename -match '[\\/:\x00-\x1f"<>|?*]' -or $filename -notmatch '(?i)\.gguf$') {
        throw 'The catalog did not supply a valid GGUF filename.'
    }
    $hash = [string]$Model.GgufSha256
    if ($hash -and $hash -notmatch '^[a-fA-F0-9]{64}$') { throw 'Invalid catalog model checksum.' }
    $destination = Join-Path $paths.WindowsPath $filename
    if ($DryRun) {
        return @{ WindowsPath=$paths.WindowsPath; WslPath=$paths.WslPath; Filename=$filename; Prepared=$false }
    }
    New-Item -ItemType Directory -Path $paths.WindowsPath -Force | Out-Null
    $ancestor = Get-Item -LiteralPath $paths.WindowsPath
    while ($ancestor) {
        if ($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Shared model directory cannot contain a junction.' }
        $ancestor = $ancestor.Parent
    }
    # Serialize preparation before publishing a complete model file.
    $lock = [IO.File]::Open((Join-Path $paths.WindowsPath '.ods-install.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    try {
        if (-not (Test-Path -LiteralPath $destination)) {
            $uri = $null
            if (-not [Uri]::TryCreate([string]$Model.GgufUrl, [UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -ne 'https' -or $uri.UserInfo) {
                throw 'A valid HTTPS catalog URL is required to download the model.'
            }
            # Each attempt owns its temporary artifact. An interrupted earlier
            # install must not block retries or be mistaken for a complete GGUF.
            $partial = $destination + '.' + [guid]::NewGuid().ToString('N') + '.ods-download'
            try {
                if (-not (Invoke-DownloadWithRetry -Url $uri.AbsoluteUri -Destination $partial -Label "Downloading $filename" -MaxRetries 4)) {
                    throw 'Model download failed; no complete model was published.'
                }
                Assert-ODSPortalModelFile -Path $partial -Hash $hash
                Move-Item -LiteralPath $partial -Destination $destination
            } finally {
                # Delete only this invocation's unpublished file; retain any
                # earlier partial or existing model for the owner's inspection.
                if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force }
            }
        } else {
            Assert-ODSPortalModelFile -Path $destination -Hash $hash
        }
        return @{ WindowsPath=$paths.WindowsPath; WslPath=$paths.WslPath; Filename=$filename; Prepared=$true }
    } finally { $lock.Dispose() }
}

function Assert-ODSPortalModelFile {
    param([string]$Path, [string]$Hash)
    $item = Get-Item -LiteralPath $Path
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -lt 24) {
        throw 'The model artifact is not a regular GGUF file.'
    }
    $stream = [IO.File]::OpenRead($Path)
    try {
        $header = New-Object byte[] 4
        if ($stream.Read($header, 0, 4) -ne 4 -or [Text.Encoding]::ASCII.GetString($header) -cne 'GGUF') {
            throw 'The model artifact does not contain a GGUF header.'
        }
    } finally { $stream.Dispose() }
    if ($Hash -and (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ine $Hash) {
        throw 'The model artifact failed its catalog checksum; it was preserved for diagnosis.'
    }
}
