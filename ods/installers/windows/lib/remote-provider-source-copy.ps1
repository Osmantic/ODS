function Get-ODSRemoteRecipeDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not [IO.Path]::IsPathRooted($Path) -or [IO.Path]::GetPathRoot($Path).Length -lt 3) {
        throw 'Remote-provider source copy requires absolute directories'
    }
    $item = Get-Item -LiteralPath ([IO.Path]::GetFullPath($Path)) -Force -ErrorAction Stop
    if (-not $item.PSIsContainer) { throw "Not a remote-provider directory: $Path" }
    $current = $item
    while ($null -ne $current) {
        if ($current.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Unsafe remote-provider directory: $($current.FullName)"
        }
        $current = $current.Parent
    }
    return $item.FullName
}

function Get-ODSRemoteRecipeBytes {
    param([Parameter(Mandatory = $true)][string]$Path)
    $null = Get-ODSRemoteRecipeDirectory (Split-Path -Parent $Path)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or $item.LinkType -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -gt 1048576) {
        throw "Unsafe remote-provider file: $Path"
    }
    $bytes = [IO.File]::ReadAllBytes($item.FullName)
    if ($bytes.Length -gt 1048576) { throw "Oversized remote-provider file: $Path" }
    return ,$bytes
}

function Invoke-ODSWindowsExtensionGraphLock {
    param([Parameter(Mandatory = $true)][string]$InstallDir,
          [Parameter(Mandatory = $true)][scriptblock]$Action)
    $install = Get-ODSRemoteRecipeDirectory $InstallDir
    $data = Get-ODSRemoteRecipeDirectory (Join-Path $install 'data')
    $lockPath = Join-Path $data '.extensions-lock'
    if (Test-Path -LiteralPath $lockPath) {
        # Inspect metadata only: another process may already lock byte zero.
        $lockItem = Get-Item -LiteralPath $lockPath -Force -ErrorAction Stop
        if ($lockItem.PSIsContainer -or $lockItem.LinkType -or
            ($lockItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Unsafe extensions lock file'
        }
    }
    $stream = [IO.FileStream]::new($lockPath, [IO.FileMode]::OpenOrCreate,
        [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite)
    $locked = $false
    try {
        $deadline = [DateTime]::UtcNow.AddSeconds(15)
        while (-not $locked) {
            try { $stream.Lock(0, 1); $locked = $true }
            catch [IO.IOException] {
                if ([DateTime]::UtcNow -ge $deadline) { throw 'Timed out waiting for extensions lock' }
                Start-Sleep -Milliseconds 50
            }
        }
        if ($stream.Length -eq 0) { $stream.WriteByte(0); $stream.Flush($true) }
        & $Action $install
    } finally {
        if ($locked) { $stream.Unlock(0, 1) }
        $stream.Dispose()
    }
}

function Sync-ODSWindowsRemoteProviderRecipes {
    param([Parameter(Mandatory = $true)][string]$InstallDir,
          [Parameter(Mandatory = $true)][string]$SourceRoot)
    $install = Get-ODSRemoteRecipeDirectory $InstallDir
    $source = Get-ODSRemoteRecipeDirectory $SourceRoot
    if ($install -ieq $source) { throw 'Remote-provider recipes need an independent source' }
    . (Join-Path $source 'installers\windows\lib\remote-provider-docker-transaction.ps1')
    # Host mutex serializes native installers; the Docker process owns the
    # authoritative Dashboard flock plus every selection read and write.
    Invoke-ODSWindowsExtensionGraphLock -InstallDir $install -Action {
        param($install)
        $null = Invoke-ODSWindowsRemoteTransaction -InstallDir $install -SourceRoot $source -Request @{ operation = 'sync' }
    }
}

function Write-ODSWindowsRemoteProviderComposeFlags {
    param([Parameter(Mandatory = $true)][string]$InstallDir,
          [Parameter(Mandatory = $true)][string]$SourceRoot,
          [Parameter(Mandatory = $true)][string[]]$ComposeFlags)
    $source = Get-ODSRemoteRecipeDirectory $SourceRoot
    $requestedFlags = @($ComposeFlags)
    . (Join-Path $source 'installers\windows\lib\remote-provider-docker-transaction.ps1')
    Invoke-ODSWindowsExtensionGraphLock -InstallDir $InstallDir -Action {
        param($install)
        $result = Invoke-ODSWindowsRemoteTransaction -InstallDir $install -SourceRoot $source -Request @{
            operation = 'flags'; flags = $requestedFlags
        }
        if ($result.flags -isnot [array] -or $result.flags.Count -eq 0) { throw 'Invalid Compose transaction receipt' }
        return [string[]]$result.flags
    }
}
