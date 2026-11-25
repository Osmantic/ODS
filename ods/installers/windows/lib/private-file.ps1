# ============================================================================
# ODS Windows Installer -- Private file writers
# ============================================================================
# Part of: installers/windows/lib/
# Purpose: Publish credentials and runtime plans as current-user-only files.
#          env-generator.ps1 and native-llama-runtime.ps1 load this file; the
#          Portal's durable launcher copies it beside its private plan, so it
#          must stay small, ASCII-only and free of other ODS dependencies.
# ============================================================================

function Write-Utf8NoBom {
    <#
    .SYNOPSIS
        Write text to file as UTF-8 WITHOUT BOM. PS 5.1's Set-Content -Encoding UTF8
        writes a BOM which corrupts Docker Compose .env parsing and YAML files.
    #>
    param(
        [string]$Path,
        [string]$Content
    )
    $parent = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    if (Test-Path -LiteralPath $Path -PathType Container) {
        Remove-Item -LiteralPath $Path -Recurse -Force
        Write-AIWarn "Removed malformed $Path directory from a previous partial install."
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

function Protect-ODSPrivateEnvFile {
    param([string]$Path)
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'The ODS credential file must be a regular file.'
    }
    if ($PSVersionTable.PSEdition -eq 'Core') {
        $acl = [IO.FileSystemAclExtensions]::GetAccessControl($item, [Security.AccessControl.AccessControlSections]::Access)
    } else {
        $acl = $item.GetAccessControl([Security.AccessControl.AccessControlSections]::Access)
    }
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($rule in @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))) {
        $acl.RemoveAccessRuleSpecific($rule)
    }
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl.SetAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'Allow'))
    if ($PSVersionTable.PSEdition -eq 'Core') {
        [IO.FileSystemAclExtensions]::SetAccessControl($item, $acl)
    } else {
        $item.SetAccessControl($acl)
    }
    $verified = Get-Acl -LiteralPath $Path
    $rules = @($verified.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    if (-not $verified.AreAccessRulesProtected -or $rules.Count -ne 1 -or
        $rules[0].IdentityReference -ne $sid -or $rules[0].IsInherited -or
        $rules[0].AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
        $rules[0].FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl) {
        throw 'Could not verify current-user-only access to the ODS credential file.'
    }
}

function Write-ODSPrivateFileBytes {
    # Byte-exact private publication. Used for credentials, runtime plans and
    # byte-for-byte backups of private files.
    param([string]$Path, [byte[]]$Bytes)
    if ($null -eq $Bytes) { $Bytes = [byte[]]@() }
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        $parent = Split-Path -Parent $Path
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
        $existed = Test-Path -LiteralPath $Path
        if ($existed) {
            # File.Replace preserves destination metadata. Verify its private
            # DACL before publication, but never overwrite the old file's bytes:
            # tightening a DACL cannot revoke already-open reader handles.
            Protect-ODSPrivateEnvFile $Path
        }
        $temporary = Join-Path $parent ('.ods-private-env-' + [guid]::NewGuid().ToString('N'))
        $security = [Security.AccessControl.FileSecurity]::new()
        $security.SetAccessRuleProtection($true, $false)
        $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
        $security.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'Allow'))
        $stream = $null
        try {
            # Supply the DACL at CreateNew so even the empty staging file never
            # inherits public read access. Keep the handle until payload flush.
            if ($PSVersionTable.PSEdition -eq 'Core') {
                $stream = [IO.FileSystemAclExtensions]::Create([IO.FileInfo]::new($temporary),
                    [IO.FileMode]::CreateNew, [Security.AccessControl.FileSystemRights]::FullControl,
                    [IO.FileShare]::None, 4096, [IO.FileOptions]::None, $security)
            } else {
                $stream = [IO.FileStream]::new($temporary, [IO.FileMode]::CreateNew,
                    [Security.AccessControl.FileSystemRights]::FullControl,
                    [IO.FileShare]::None, 4096, [IO.FileOptions]::None, $security)
            }
            Protect-ODSPrivateEnvFile $temporary
            $stream.Write($Bytes, 0, $Bytes.Length)
            $stream.Flush($true)
            $stream.Dispose()
            $stream = $null
            if ($existed) {
                [IO.File]::Replace($temporary, $Path, [System.Management.Automation.Language.NullString]::Value)
            } else {
                [IO.File]::Move($temporary, $Path)
            }
        } finally {
            if ($null -ne $stream) { $stream.Dispose() }
            if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
        }
        return
    }
    $parent = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    if (Test-Path -LiteralPath $Path -PathType Container) { throw 'The ODS private file target must be a regular file.' }
    [IO.File]::WriteAllBytes($Path, $Bytes)
}

function Write-ODSPrivateEnvFile {
    # UTF-8 without a BOM, written exactly as given (no trailing newline added).
    param([string]$Path, [string]$Content)
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        Write-ODSPrivateFileBytes -Path $Path -Bytes ([Text.UTF8Encoding]::new($false).GetBytes([string]$Content))
        return
    }
    Write-Utf8NoBom -Path $Path -Content $Content
}
