# Contract: Invoke-ODSDockerCompose shows Compose's stderr output (progress
# and errors) and still returns Compose's exit code. Windows PowerShell 5.1
# discards merged native stderr under ErrorActionPreference SilentlyContinue,
# which hid every pull line and every Compose error. A docker.cmd shim on
# PATH stands in for the real CLI so the native stderr path is exercised.
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { Write-Host 'SKIP: docker.cmd shim needs Windows'; exit 0 }

. (Join-Path $PSScriptRoot '../../installers/windows/lib/compose-diagnostics.ps1')
function Get-ODSUserDockerConfigDir { param($DockerConfigOverride) return $null }
function Initialize-ODSComposeDockerClientConfig { param($InstallDir, $UserDockerConfigDir) return $InstallDir }

$root = Join-Path ([IO.Path]::GetTempPath()) ('ods-compose-output-' + [guid]::NewGuid().ToString('N'))
$bin = Join-Path $root 'bin'
New-Item -ItemType Directory -Path $bin -Force | Out-Null
$previousPath = $env:PATH
try {
    Set-Content -LiteralPath (Join-Path $bin 'docker.cmd') -Encoding ASCII -Value @(
        '@echo off',
        'echo  Image probe Pulling 1>&2',
        'echo  Container probe Started 1>&2',
        'if "%ODS_TEST_COMPOSE_EXIT%"=="" exit /b 0',
        'echo Error response from daemon: range of CPUs is from 0.01 to 8.00 1>&2',
        'exit /b %ODS_TEST_COMPOSE_EXIT%'
    )
    $env:PATH = "$bin;$previousPath"

    foreach ($case in @(
        @{ Exit = ''; Expected = 0; Lines = @('Image probe Pulling', 'Container probe Started') },
        @{ Exit = '3'; Expected = 3; Lines = @('range of CPUs is from 0.01 to 8.00') }
    )) {
        $env:ODS_TEST_COMPOSE_EXIT = $case.Exit
        $callerPreference = $ErrorActionPreference
        $output = @(Invoke-ODSDockerCompose -InstallDir $root -ComposeFlags @('-f', 'compose.yml') `
            -ComposeArgs @('up', '-d') 6>&1)
        $exitCode = $output[-1]
        $shown = ($output | Select-Object -SkipLast 1 | ForEach-Object { [string]$_ }) -join "`n"

        if ($exitCode -ne $case.Expected) { throw "expected exit $($case.Expected), got $exitCode" }
        foreach ($line in $case.Lines) {
            if ($shown -notmatch [regex]::Escape($line)) {
                throw "Compose stderr line was not shown: '$line'. Shown: [$shown]"
            }
        }
        if ($ErrorActionPreference -ne $callerPreference) { throw 'caller ErrorActionPreference was not restored' }
        Write-Host "PASS compose stderr is shown and exit $($case.Expected) is returned"
    }
} finally {
    $env:PATH = $previousPath
    Remove-Item Env:ODS_TEST_COMPOSE_EXIT -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
