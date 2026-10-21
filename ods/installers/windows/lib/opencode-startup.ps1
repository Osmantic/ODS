# Ordinary-user login persistence when Task Scheduler denies registration.
# Reuse the installed CLI's port/process checks and the existing web launcher.

function Get-ODSOpenCodeStartupPath {
    $startup = [Environment]::GetFolderPath('Startup')
    if ([string]::IsNullOrWhiteSpace($startup)) { throw 'The owner Startup folder is unavailable.' }
    return (Join-Path $startup 'ods-opencode.vbs')
}

function Get-ODSOpenCodeStartupContent {
    param([Parameter(Mandatory = $true)][string]$InstallDir)
    $root = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
    $literal = $root.Replace("'", "''")
    $cli = (Join-Path $root 'ods.ps1').Replace("'", "''")
    # The CLI normally derives ODS_HOME from the environment. Pin this child
    # to the installation that created the entry, including custom paths.
    $command = "`$env:ODS_HOME = '$literal'; & '$cli' start opencode"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    return "' ODS OpenCode login startup launcher`r`nSet WshShell = CreateObject(`"WScript.Shell`")`r`nWshShell.Run `"powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -EncodedCommand $encoded`", 0, False`r`n"
}

function Test-ODSOpenCodeStartupOwned {
    param([string]$Path, [string]$InstallDir)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return $false }
    $owner = (Get-Acl -LiteralPath $Path -ErrorAction Stop).GetOwner([Security.Principal.SecurityIdentifier])
    if ($owner -ne [Security.Principal.WindowsIdentity]::GetCurrent().User) { return $false }
    $content = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 -ErrorAction Stop
    return ($content -ceq (Get-ODSOpenCodeStartupContent -InstallDir $InstallDir))
}

function Set-ODSOpenCodeStartupFallback {
    param([Parameter(Mandatory = $true)][string]$InstallDir)
    if (-not (Test-Path -LiteralPath (Join-Path $InstallDir 'ods.ps1') -PathType Leaf)) {
        throw 'The installed ODS CLI is missing; OpenCode login persistence was not configured.'
    }
    $entry = Get-ODSOpenCodeStartupPath
    if (Get-Item -LiteralPath $entry -Force -ErrorAction SilentlyContinue) {
        if (-not (Test-ODSOpenCodeStartupOwned -Path $entry -InstallDir $InstallDir)) {
            throw 'An unowned OpenCode Startup entry was preserved.'
        }
        return $entry
    }
    . (Join-Path $PSScriptRoot 'private-file.ps1')
    # Publish create-only: a foreign entry appearing since the check above
    # must not be overwritten by the private writer's replacement path.
    $temporary = Join-Path (Split-Path -Parent $entry) ('.ods-opencode-' + [guid]::NewGuid().ToString('N'))
    try {
        Write-ODSPrivateEnvFile -Path $temporary -Content (Get-ODSOpenCodeStartupContent -InstallDir $InstallDir)
        # An elevated Windows token can default new-file ownership to the
        # Administrators group even with a current-user-only DACL.
        # Set only Owner, preserving the private DACL without touching SACLs.
        $acl = [Security.AccessControl.FileSecurity]::new()
        $acl.SetOwner([Security.Principal.WindowsIdentity]::GetCurrent().User)
        $item = Get-Item -LiteralPath $temporary -Force -ErrorAction Stop
        if ($PSVersionTable.PSEdition -eq 'Core') {
            [IO.FileSystemAclExtensions]::SetAccessControl($item, $acl)
        } else {
            $item.SetAccessControl($acl)
        }
        if (-not (Test-ODSOpenCodeStartupOwned -Path $temporary -InstallDir $InstallDir)) {
            throw 'Could not verify the new OpenCode Startup entry owner.'
        }
        [IO.File]::Move($temporary, $entry)
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
    return $entry
}

function Remove-ODSOpenCodeStartupFallback {
    param([Parameter(Mandatory = $true)][string]$InstallDir)
    $entry = Get-ODSOpenCodeStartupPath
    if (-not (Get-Item -LiteralPath $entry -Force -ErrorAction SilentlyContinue)) { return }
    if (-not (Test-ODSOpenCodeStartupOwned -Path $entry -InstallDir $InstallDir)) {
        Write-AIWarn 'An unowned OpenCode Startup entry was preserved.'
        return
    }
    Remove-Item -LiteralPath $entry -Force -ErrorAction Stop
}
