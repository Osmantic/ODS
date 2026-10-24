# ============================================================================
# Contract Test: Windows Preflight Filesystem Gate & Drive Root Resolution
# ============================================================================
# Exercises the actual production preflight filesystem logic from Phase 01:
#   1. Verifies that existing subdirectories resolve to their volume root before
#      DriveInfo is constructed, proving baseline ArgumentException failure
#      and patched success on Windows runtimes.
#   2. Verifies that $_fsProbe preserves the existing ancestor directory for
#      the downstream Docker bind-mount probe.
#   3. Exercises production decision logic directly against NTFS, exFAT, FAT32,
#      FAT, and WSL mounts, ensuring fatal filesystems throw ODS_INSTALL_ABORTED.
#   4. Exercises production network-drive logic directly for UNC shares and
#      mapped network drives.
# ============================================================================
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', '', Justification='Fixture mocks preserve the production command signatures.')]
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseDeclaredVarsMoreThanAssignment', '', Justification='Variables are consumed by dynamically executed production scriptblocks.')]
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '', Justification='Internal test telemetry cleanup.')]
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

# Extract production scriptblocks directly from 01-preflight.ps1
$fsMatch = [regex]::Match($phaseText, '(?s)(# [^\r\n]*Filesystem POSIX-permission check.*?)(?=# [^\r\n]*Docker Desktop file-sharing allowlist check)')
if (-not $fsMatch.Success) { throw 'Could not extract filesystem preflight section from 01-preflight.ps1' }
$productionFsSection = [scriptblock]::Create($fsMatch.Groups[1].Value)

$decisionMatch = [regex]::Match($phaseText, '(?s)(\$_fsFatal\s*=\s*\$false.*?Write-AISuccess[^\r\n]*)')
if (-not $decisionMatch.Success) { throw 'Could not extract decision block from 01-preflight.ps1' }
$productionDecisionBlock = [scriptblock]::Create($decisionMatch.Groups[1].Value)

$networkMatch = [regex]::Match($phaseText, '(?s)(\$_fsNetworked\s*=\s*\$false.*?if \(\$_fsNetworked\) \{.*?Write-AIWarn[^\r\n]*\r?\n\})')
if (-not $networkMatch.Success) { throw 'Could not extract network block from 01-preflight.ps1' }
$productionNetworkBlock = [scriptblock]::Create($networkMatch.Groups[1].Value)

# 2. Setup telemetry and test assertion helper
$script:checks = 0
function Assert-Contract([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "CONTRACT VIOLATION: $Message" }
    $script:checks++
    Microsoft.PowerShell.Utility\Write-Host "PASS $Message"
}

$script:infoBoxes = [Collections.Generic.List[string]]::new()
$script:errors = [Collections.Generic.List[string]]::new()
$script:warnings = [Collections.Generic.List[string]]::new()
$script:successes = [Collections.Generic.List[string]]::new()

function Write-InfoBox { param($Label, $Value) $script:infoBoxes.Add("$Label $Value") }
function Write-AIError { param($Msg) $script:errors.Add($Msg) }
function Write-AIWarn { param($Msg) $script:warnings.Add($Msg) }
function Write-AISuccess { param($Msg) $script:successes.Add($Msg) }
function Write-AI { param($Msg) $null = $Msg }

function Clear-TestTelemetry {
    $script:infoBoxes.Clear()
    $script:errors.Clear()
    $script:warnings.Clear()
    $script:successes.Clear()
}

$fixtureRoot = Join-Path ([IO.Path]::GetTempPath()) ('ods-fs-probe-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $fixtureRoot
try {
    # 3. Live Directory Resolution & Baseline Failure Reproduction
    $parentDir = Join-Path $fixtureRoot 'existing-parent'
    $null = New-Item -ItemType Directory -Path $parentDir
    $installDir = Join-Path (Join-Path $parentDir 'subfolder') 'target-ods'
    $null = $installDir

    # Execute the actual production preflight filesystem section
    Clear-TestTelemetry
    . $productionFsSection

    # Case 1 & 6: Verify volume root resolution and $_fsProbe preservation for Docker probe
    Assert-Contract ($_fsProbe -eq $parentDir) '$_fsProbe retains existing parent directory for Docker bind-mount probe'
    Assert-Contract (-not [string]::IsNullOrWhiteSpace($_fsRoot)) '$_fsRoot is resolved from probe path'

    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        # Genuine reproduction of original defect: passing $_fsProbe directly throws on Windows
        $baselineThrew = $false
        try {
            $null = [IO.DriveInfo]::new($_fsProbe)
        } catch [ArgumentException] {
            $baselineThrew = $true
        }
        Assert-Contract $baselineThrew 'Baseline reproduction: passing subdirectory directly to DriveInfo throws ArgumentException'

        # Fixed code path: passing $_fsRoot succeeds and retrieves volume DriveFormat
        $fixedDi = [IO.DriveInfo]::new($_fsRoot)
        Assert-Contract (-not [string]::IsNullOrWhiteSpace($fixedDi.DriveFormat)) 'Fixed runtime path: DriveInfo(root) retrieves valid DriveFormat'
        Assert-Contract ($_fsType -eq $fixedDi.DriveFormat) 'Production preflight section populated $_fsType from resolved volume root'
        Assert-Contract ($script:successes -contains 'Filesystem supports POSIX-style permissions') 'Native Windows NTFS follows intended pass path'
    }

    # 4. Exercise Production Filesystem Decision Logic
    foreach ($fatalType in @('exFAT', 'FAT32', 'FAT')) {
        Clear-TestTelemetry
        $_fsType = $fatalType
        $_isWslPath = $false
        $null = $_isWslPath
        $aborted = $false
        try {
            . $productionDecisionBlock
        } catch {
            if ($_.Exception.Message -eq 'ODS_INSTALL_ABORTED') { $aborted = $true }
        }
        Assert-Contract $aborted "Production decision logic aborts with ODS_INSTALL_ABORTED for $fatalType"
        Assert-Contract ($script:errors.Count -gt 0) "Production decision logic logs AI error explaining $fatalType ACL limitation"
    }

    # NTFS under native Windows passes cleanly
    Clear-TestTelemetry
    $_fsType = 'NTFS'
    $_isWslPath = $false
    $null = $_isWslPath
    $aborted = $false
    try {
        . $productionDecisionBlock
    } catch {
        $aborted = $true
    }
    Assert-Contract (-not $aborted) 'Production decision logic accepts native NTFS without aborting'
    Assert-Contract ($script:successes -contains 'Filesystem supports POSIX-style permissions') 'Native NTFS records POSIX permission success'

    # NTFS under WSL mount (DrvFs/9p) triggers fatal abort
    Clear-TestTelemetry
    $_fsType = 'NTFS'
    $_isWslPath = $true
    $null = $_isWslPath
    $aborted = $false
    try {
        . $productionDecisionBlock
    } catch {
        if ($_.Exception.Message -eq 'ODS_INSTALL_ABORTED') { $aborted = $true }
    }
    Assert-Contract $aborted 'Production decision logic aborts on NTFS when mounted inside WSL'

    # 5. Exercise Production Network-Drive Detection Logic
    # Case A: UNC share path (e.g. \\server\share\ods)
    Clear-TestTelemetry
    $installDir = '\\nas01\shared\ods'
    $null = $installDir
    $_di = $null
    $null = $_di
    . $productionNetworkBlock
    Assert-Contract ($_fsNetworked -and $_fsNetworkType -eq 'UNC share') 'Production network block detects UNC share'
    Assert-Contract ($script:warnings.Count -gt 0 -and $script:warnings[0] -match 'UNC share') 'Production network block emits advisory warning for UNC share'

    # Case B: Mapped network drive letter (e.g. Z:\ods)
    Clear-TestTelemetry
    $installDir = 'Z:\ods'
    $null = $installDir
    $_di = [PSCustomObject]@{ DriveType = [IO.DriveType]::Network }
    $null = $_di
    . $productionNetworkBlock
    Assert-Contract ($_fsNetworked -and $_fsNetworkType -eq 'mapped network drive') 'Production network block detects mapped network drive'
    Assert-Contract ($script:warnings.Count -gt 0 -and $script:warnings[0] -match 'mapped network drive') 'Production network block emits advisory warning for mapped drive'

    # Case C: Local non-networked drive
    Clear-TestTelemetry
    $installDir = 'C:\ods'
    $null = $installDir
    $_di = [PSCustomObject]@{ DriveType = [IO.DriveType]::Fixed }
    $null = $_di
    . $productionNetworkBlock
    Assert-Contract (-not $_fsNetworked -and [string]::IsNullOrEmpty($_fsNetworkType)) 'Production network block leaves local fixed drive unflagged'
    Assert-Contract ($script:warnings.Count -eq 0) 'Production network block emits zero warnings for local fixed drive'
} finally {
    if (Test-Path -LiteralPath $fixtureRoot) {
        Remove-Item -LiteralPath $fixtureRoot -Recurse -Force
    }
}

Microsoft.PowerShell.Utility\Write-Host "Completed $script:checks production preflight filesystem contract checks."
