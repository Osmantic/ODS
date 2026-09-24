$ErrorActionPreference = 'Stop'
$root = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent
$executable = (Get-Process -Id $PID).Path
foreach ($entry in @('install.ps1', 'ods/installers/windows/install-windows.ps1')) {
    $ErrorActionPreference = 'Continue' # Expected native stderr on Windows PowerShell 5.1.
    $output = & $executable -NoProfile -File (Join-Path $root $entry) -Pixel -NonInteractive 2>&1
    $ErrorActionPreference = 'Stop'
    if ($LASTEXITCODE -eq 0 -or "$output" -notmatch 'Portal requires the ODS Linux installer') {
        throw "Explicit Portal request was not rejected with WSL guidance: $entry"
    }
}
Write-Output 'PASS: both Windows entrypoints reject unsupported Portal provisioning before setup'
