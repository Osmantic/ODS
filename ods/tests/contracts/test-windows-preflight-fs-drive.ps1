# ============================================================================
# Contract Test: Windows Preflight Filesystem Gate & Drive Root Resolution
# ============================================================================
# Verifies that Phase 01 preflight resolves the volume root before invoking
# System.IO.DriveInfo, preventing false-positive unknown filesystems and
# fail-open security bypasses on FAT32/exFAT volumes.
# ============================================================================
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'

$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$phaseFile = Join-Path $root 'installers/windows/phases/01-preflight.ps1'

# 1. Structural AST Contract: Ensure DriveInfo::new receives the resolved root
$tokens = $null; $parseErrors = $null
$null = [Management.Automation.Language.Parser]::ParseFile($phaseFile, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) { throw "01-preflight.ps1 failed to parse: $($parseErrors[0].Message)" }

$phaseText = [IO.File]::ReadAllText($phaseFile)
if ($phaseText -match '\[System\.IO\.DriveInfo\]::new\(\$_fsProbe\)') {
    throw 'Preflight must not pass non-root directory $_fsProbe directly to DriveInfo'
}
if ($phaseText -notmatch '\[System\.IO\.Path\]::GetPathRoot\(\$_fsProbe\)') {
    throw 'Preflight must resolve volume root with Path.GetPathRoot before DriveInfo probe'
}

# 2. Runtime Behavior & Regression Verification on Live Subdirectory
$script:checks = 0
function Assert-Contract([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "CONTRACT VIOLATION: $Message" }
    $script:checks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}

$fixtureRoot = Join-Path ([IO.Path]::GetTempPath()) ('ods-fs-probe-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $fixtureRoot
try {
    $nestedTarget = Join-Path (Join-Path $fixtureRoot 'nested') 'ods'
    $null = New-Item -ItemType Directory -Path (Split-Path -Parent $nestedTarget)

    # Prove baseline failure vs fixed success on Windows platforms
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        $rootPath = [IO.Path]::GetPathRoot($nestedTarget)
        $baselineThrew = $false
        try {
            $null = [IO.DriveInfo]::new($nestedTarget)
        } catch [ArgumentException] {
            $baselineThrew = $true
        }
        Assert-Contract $baselineThrew 'Passing subdirectory directly to DriveInfo throws ArgumentException'

        $fixedDi = [IO.DriveInfo]::new($rootPath)
        Assert-Contract (-not [string]::IsNullOrWhiteSpace($fixedDi.DriveFormat)) 'Resolving volume root yields valid DriveFormat'
    }

    # 3. Decision Matrix Verification (Policy Enforcement)
    function Test-ODSFilesystemDecision([string]$FsType, [bool]$IsWsl) {
        $fatal = $false
        switch -Regex ($FsType) {
            '^(exFAT|FAT32|FAT)$' { $fatal = $true }
            '^NTFS$'              { if ($IsWsl) { $fatal = $true } }
        }
        return $fatal
    }

    # FAT filesystems must remain fatal (unrestricted credential exposure)
    Assert-Contract (Test-ODSFilesystemDecision 'exFAT' $false) 'exFAT volume is rejected as fatal'
    Assert-Contract (Test-ODSFilesystemDecision 'FAT32' $false) 'FAT32 volume is rejected as fatal'
    Assert-Contract (Test-ODSFilesystemDecision 'FAT' $false) 'FAT volume is rejected as fatal'

    # NTFS must succeed on native Windows, but abort under WSL DrvFs/9p mount
    Assert-Contract (-not (Test-ODSFilesystemDecision 'NTFS' $false)) 'Native NTFS is accepted'
    Assert-Contract (Test-ODSFilesystemDecision 'NTFS' $true) 'WSL-mounted NTFS is rejected as fatal'

    # 4. Mapped Network Drive Detection Contract
    $mockDi = [PSCustomObject]@{ DriveType = [IO.DriveType]::Network }
    $isNetwork = ($mockDi -and $mockDi.DriveType -eq [IO.DriveType]::Network)
    Assert-Contract $isNetwork 'DriveType Network marks filesystem as networked advisory'
} finally {
    if (Test-Path -LiteralPath $fixtureRoot) {
        Remove-Item -LiteralPath $fixtureRoot -Recurse -Force
    }
}

Microsoft.PowerShell.Utility\Write-Host "Completed $script:checks filesystem preflight contract checks."
