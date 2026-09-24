$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../installers/windows/lib/backend-contract.ps1')
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$fixture = [IO.Path]::GetFullPath((Join-Path ([IO.Path]::GetTempPath()) ('ods-lemonade-test-' + [guid]::NewGuid().ToString('N'))))
$script:available = $true
$script:downloadOk = $true
$script:msiCode = 0
$script:publishExecutable = $true
$script:downloads = 0
$script:launches = 0
function Check($Condition, $Message) { if (-not $Condition) { throw $Message }; Write-Host "PASS $Message" }
function Resolve-ODSLemonadeExe {
    param($ExecutableName)
    if ($script:available) { return 'C:\Fixture\Lemonade\lemonade-server.exe' }
    return $null
}
function Get-ODSLemonadeUserInstallDir { return 'C:\Fixture User\lemonade_server' }
function Invoke-DownloadWithRetry {
    param($Url,$Destination,$Label)
    $script:downloads++
    Check ($Url -match '^https://github.com/lemonade-sdk/lemonade/releases/download/v[0-9.]+/lemonade-server-minimal.msi$') 'uses the existing pinned public runtime package'
    return $script:downloadOk
}
function Start-Process {
    param($FilePath,$ArgumentList,$WindowStyle,[switch]$Wait,[switch]$PassThru)
    $script:launches++
    Check ($FilePath -ceq 'msiexec.exe' -and $WindowStyle -ceq 'Hidden' -and $Wait -and $PassThru) 'waits for the MSI result without opening a console'
    Check ($ArgumentList.Contains('INSTALLDIR="C:\Fixture User\lemonade_server"')) 'retains the supported per-user installation path with spaces'
    $script:available = $script:publishExecutable
    return [pscustomobject]@{ ExitCode=$script:msiCode }
}
try {
    $result = Install-ODSLemonadeRuntime -RootPath $root -WorkDirectory $fixture
    Check ($result.Installed -and $result.Reused -and $script:downloads -eq 0 -and $script:launches -eq 0) 'reuses an installed runtime without downloading or reinstalling'
    $script:available = $false
    $result = Install-ODSLemonadeRuntime -RootPath $root -WorkDirectory $fixture -DryRun
    Check (-not $result.Installed -and -not (Test-Path -LiteralPath $fixture) -and $script:downloads -eq 0) 'dry run neither downloads nor claims an installation'
    $script:downloadOk = $false
    $rejected=$false
    try { $null = Install-ODSLemonadeRuntime -RootPath $root -WorkDirectory $fixture } catch { $rejected=$true }
    Check ($rejected -and $script:launches -eq 0) 'failed download never starts the MSI'
    $script:downloadOk = $true
    foreach ($code in @(1603, 0, 3010)) {
        $script:available=$false
        $script:msiCode=$code
        $rejected=$false
        try { $result=Install-ODSLemonadeRuntime -RootPath $root -WorkDirectory $fixture } catch { $rejected=$true }
        if ($code -eq 1603) { Check $rejected 'MSI failure is not reported as installed' }
        else { Check (-not $rejected -and $result.Installed -and ($result.RestartRequired -eq ($code -eq 3010))) "MSI $code preserves installation and restart status" }
    }
    $script:available=$false
    $script:msiCode=0
    $script:publishExecutable=$false
    $rejected=$false
    try { $null=Install-ODSLemonadeRuntime -RootPath $root -WorkDirectory $fixture } catch { $rejected=$true }
    Check $rejected 'successful MSI without executable cannot complete runtime preparation'
} finally {
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $fixture.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Fixture escaped temporary directory' }
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Recurse -Force }
}
