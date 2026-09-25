# No real WSL, UAC, downloads or services are invoked by these contracts.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../installers/windows/lib/wsl-portal-setup.ps1')
$originalOS = $env:OS
$env:OS = 'Windows_NT'
$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Write-Host "PASS $Message"
}
function Reset-Scenario {
    $script:calls = [Collections.Generic.List[string]]::new()
    $script:scenario = 'ready'
    $script:delegateCode = 0
    $script:featureCode = 0
    $script:allowPreparation = $true
    $script:capturedArguments = @()
    $script:capturedRoot = ''
}
function Test-ODSPortalAdministrator { return $script:scenario -eq 'admin' }
function Test-ODSNativeWindowsInstall { return $script:scenario -eq 'native' }
function Get-Command { return [pscustomobject]@{ Name='wsl.exe' } }
function Confirm-ODSPortalPreparation([string]$Message, [bool]$NonInteractive) {
    $script:calls.Add('confirm')
    return (-not $NonInteractive -and $script:allowPreparation)
}
function Install-ODSPortalWslFeatures { $script:calls.Add('features'); return $script:featureCode }
function Initialize-ODSPortalUbuntuUser([string]$Distro) { $script:calls.Add('user:' + $Distro); return 0 }
function Invoke-ODSPortalLinuxInstaller([string]$InstallerRoot, [string]$Distro, [string[]]$LinuxArguments, [string]$InstallRoot) {
    $script:calls.Add('install:' + $Distro)
    $script:capturedArguments = $LinuxArguments
    $script:capturedRoot = $InstallRoot
    return $script:delegateCode
}
function Invoke-ODSPortalWsl([string[]]$Arguments) {
    $key = $Arguments -join ' '
    $script:calls.Add($key)
    $code = 0
    $output = ''
    switch -Regex ($key) {
        '^--status$' { if ($script:scenario -eq 'features') { $code=1 }; break }
        '^--list --quiet$' { if ($script:scenario -ne 'missing') { $output='Ubuntu-24.04' }; break }
        '^--install --distribution Ubuntu-24.04 --no-launch$' { break }
        '^--list --verbose$' { $output='* Ubuntu-24.04    Em Execucao   2'; if ($script:scenario -eq 'wsl1') { $output=$output -replace '2$', '1' }; break }
        '^--distribution Ubuntu-24.04 --exec id -u$' { $output='1000'; if ($script:scenario -eq 'root') { $output='0' }; break }
        '^--distribution Ubuntu-24.04 --exec ps -p 1 -o comm=$' { $output='systemd'; if ($script:scenario -eq 'init') { $output='init' }; break }
        '^--distribution Ubuntu-24.04 --exec docker info$' { if ($script:scenario -eq 'docker') { $code=1 }; break }
        '^--distribution Ubuntu-24.04 --exec docker compose version$' { if ($script:scenario -eq 'compose') { $code=1 }; break }
        default { throw "Unexpected native invocation: $key" }
    }
    return [pscustomobject]@{ Code=$code; Output=$output }
}
try {
    Reset-Scenario
    Check ((Invoke-ODSPortalSetup @{DryRun=$true} 'unused') -eq 0) 'dry run succeeds'
    Check ($script:calls.Count -eq 0) 'dry run performs no native calls'
    Reset-Scenario
    $options = @{All=$true; NoLangfuse=$true; Tier='2'; InstallDir='/home/user/ODS data'; SummaryJsonPath='/home/user/result.json'}
    Check ((Invoke-ODSPortalSetup $options 'unused') -eq 0) 'ready host delegates successfully'
    Check (($script:capturedArguments[-3..-1] -join ' ') -eq '--pixel --no-hermes --no-openclaw') 'mandatory Pixel policy wins after --all'
    Check (($script:capturedArguments -join ' ') -match '--all --no-langfuse') 'explicit disable follows all'
    Check ($script:capturedRoot -eq '/home/user/ODS data') 'Linux install path forwarded intact'
    Check ($script:calls.Contains('--distribution Ubuntu-24.04 --exec docker compose version')) 'checks Compose inside selected distro'
    foreach ($failure in @('admin','native','wsl1','root','init','docker','compose')) {
        Reset-Scenario
        $script:scenario = $failure
        $rejected = $false
        try { $null = Invoke-ODSPortalSetup @{} 'unused' } catch { $rejected=$true }
        Check $rejected "$failure blocks installation"
        Check (-not $script:calls.Contains('install:Ubuntu-24.04')) "$failure never falls back or delegates"
    }
    Reset-Scenario
    $script:scenario='missing'
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 0) 'new Ubuntu initializes then installs'
    Check ($script:calls.Contains('--install --distribution Ubuntu-24.04 --no-launch')) 'downloads selected Ubuntu only'
    Check ($script:calls.Contains('user:Ubuntu-24.04')) 'first-run user setup remains interactive'
    foreach ($case in @('missing','features')) {
        Reset-Scenario
        $script:scenario=$case
        Check ((Invoke-ODSPortalSetup @{NonInteractive=$true} 'unused') -eq 1) "$case non-interactive stops"
        Check (-not $script:calls.Contains('features') -and -not $script:calls.Contains('--install --distribution Ubuntu-24.04 --no-launch')) "$case non-interactive never installs prerequisites"
    }
    Reset-Scenario
    $script:scenario='features'
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 3010) 'feature setup requires explicit resume'
    Check (-not $script:calls.Contains('install:Ubuntu-24.04')) 'no ODS installation before feature restart'
    Reset-Scenario
    $script:scenario='features'; $script:featureCode=5
    $rejected=$false
    try { $null=Invoke-ODSPortalSetup @{} 'unused' } catch { $rejected=$true }
    Check $rejected 'failed UAC/feature preparation is fatal'
    Reset-Scenario
    $script:delegateCode=17
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 17) 'delegated install/health failure remains failure'
    foreach ($bad in @(@{Hermes=$true}, @{OpenClaw=$true}, @{InstallDir='D:\ODS'}, @{InstallDir='/'}, @{SummaryJsonPath='/tmp/../secret'}, @{Tier='9'}, @{Distro='x --user root'})) {
        Reset-Scenario
        $rejected=$false
        try { $null=Invoke-ODSPortalSetup $bad 'unused' } catch { $rejected=$true }
        Check ($rejected -and $script:calls.Count -eq 0) 'invalid options fail before system operations'
    }
    Write-Host "Passed $script:checks Windows Portal setup contracts."
} finally { $env:OS=$originalOS }
