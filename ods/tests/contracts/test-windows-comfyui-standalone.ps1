$ErrorActionPreference = 'Stop'
$installer = Join-Path $PSScriptRoot '..\..\installers\windows\install-comfyui-standalone.ps1'
$tempRoot = [System.IO.Path]::GetFullPath($env:TEMP).TrimEnd('\', '/')
$scratch = [System.IO.Path]::GetFullPath(
    (Join-Path $tempRoot ('ods-comfyui-contract-' + [guid]::NewGuid().ToString('N'))))
if (-not $scratch.StartsWith(($tempRoot + '\'),
        [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Test scratch path escaped the intended temporary directory.'
}
$priorHome = $env:ODS_HOME
try {
    New-Item -ItemType Directory -Path $scratch -Force | Out-Null
    $env:ODS_HOME = Join-Path $scratch 'existing-full-ods'
    New-Item -ItemType Directory -Path $env:ODS_HOME -Force | Out-Null
    $unsafeData = Join-Path $env:ODS_HOME 'data\comfyui'
    $rejected = $false
    try { & $installer -DataRoot $unsafeData -DryRun | Out-Null }
    catch {
        if ($_.Exception.Message -match 'inside the existing ODS installation') {
            $rejected = $true
        } else { throw }
    }
    if (-not $rejected) { throw 'Installer accepted data inside a full ODS install.' }
    if (Test-Path -LiteralPath $unsafeData) {
        throw 'Rejected install created a data directory.'
    }
    Write-Host 'PASS: standalone install rejects the full ODS data root without mutation'
    # PowerShell 7 keeps a variable set to "" (what $null becomes) defined and
    # empty, and an empty COMPOSE_FILE breaks later compose calls.
    $source = Get-Content -LiteralPath $installer -Raw
    if ($source -notmatch "\[Environment\]::SetEnvironmentVariable\(\`$name, \[NullString\]::Value, 'Process'\)") {
        throw 'The environment restore must remove variables that were unset with [NullString]::Value.'
    }
    Write-Host 'PASS: the environment restore removes variables that were unset'
} finally {
    $env:ODS_HOME = $priorHome
    if (Test-Path -LiteralPath $scratch) {
        Remove-Item -LiteralPath $scratch -Recurse -Force
    }
}
