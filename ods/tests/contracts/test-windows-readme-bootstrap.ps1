# Execute the public paste block with local download/extraction fixtures.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
$readme = Get-Content -LiteralPath (Join-Path $root 'README.md') -Raw
$quickstart = Get-Content -LiteralPath (Join-Path $root 'ods/docs/WINDOWS-QUICKSTART.md') -Raw
$pattern = '(?s)```powershell\s*(.*?)\s*```'
$code = [regex]::Match($readme, $pattern).Groups[1].Value
if (-not $code -or $code -cne [regex]::Match($quickstart, $pattern).Groups[1].Value) { throw 'Public Windows installation blocks differ.' }
foreach ($relative in @('ods/README.md', 'ods/QUICKSTART.md', 'ods/docs/FAQ.md')) {
    $document = (Get-Content -LiteralPath (Join-Path $root $relative) -Raw).Replace("`r`n", "`n")
    if (-not $document.Contains($code.Replace("`r`n", "`n"))) { throw "Public Windows installation block differs in $relative." }
}
$tokens = $null; $parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput($code, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -or $ast.EndBlock.Statements.Count -ne 1) { throw 'Paste block must parse as one complete statement.' }
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-bootstrap-contract-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $fixture
$previousTemp = $env:TEMP
$env:TEMP = $fixture
try {
    foreach ($failure in @('download', 'extract', 'missing-entry', 'none')) {
        $script:fixtureFailure = $failure
        $script:extracted = $false
        $script:policySet = $false
        function Invoke-WebRequest {
            param([string]$Uri, [string]$OutFile, [switch]$UseBasicParsing)
            if ($script:fixtureFailure -eq 'download') { Write-Error 'fixture download failure'; return }
            [IO.File]::WriteAllText($OutFile, 'fixture archive')
        }
        function Expand-Archive {
            param([string]$LiteralPath, [string]$DestinationPath)
            $script:extracted = $true
            if ($script:fixtureFailure -eq 'extract') { Write-Error 'fixture extraction failure'; return }
            if ($script:fixtureFailure -eq 'missing-entry') { return }
            $folder = Join-Path $DestinationPath 'ODS-main'
            $null = New-Item -ItemType Directory -Path $folder
            [IO.File]::WriteAllText((Join-Path $folder 'install.ps1'), '[IO.File]::WriteAllText((Join-Path $env:TEMP "entry-ran"), "yes")')
        }
        function Set-ExecutionPolicy {
            param([string]$Scope, [string]$ExecutionPolicy)
            if ($Scope -cne 'Process' -or $ExecutionPolicy -cne 'Bypass') { throw 'Unexpected execution policy scope.' }
            $script:policySet = $true
        }
        $failed = $false
        try { & ([scriptblock]::Create($code)) } catch { $failed = $true }
        $entryRan = Test-Path -LiteralPath (Join-Path $fixture 'entry-ran')
        if ($failure -eq 'none') {
            if ($failed -or -not $entryRan -or -not $script:policySet) { throw 'Valid source did not reach its installer.' }
        } elseif (-not $failed -or $entryRan -or $script:policySet) { throw "Bootstrap continued after $failure failure." }
        if ($failure -eq 'download' -and $script:extracted) { throw 'Extraction ran after download failure.' }
        Write-Host "PASS public bootstrap: $failure"
    }
} finally {
    $env:TEMP = $previousTemp
    $resolved = (Resolve-Path -LiteralPath $fixture).Path
    if ($resolved -ne [IO.Path]::GetFullPath($fixture) -or (Split-Path -Leaf $resolved) -notlike 'ods-bootstrap-contract-*') { throw 'Unexpected fixture cleanup target.' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
