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
    $script:downloadCode = 0
    $script:userSetupCode = 0
}
function Test-ODSPortalAdministrator { return $script:scenario -eq 'admin' }
function Test-ODSNativeWindowsInstall { return $script:scenario -eq 'native' }
function Get-Command { if ($script:scenario -eq 'no-wsl') { return $null }; return [pscustomobject]@{ Name='wsl.exe' } }
function Confirm-ODSPortalPreparation([string]$Message, [bool]$NonInteractive) {
    $script:calls.Add('confirm')
    return (-not $NonInteractive -and $script:allowPreparation)
}
function Install-ODSPortalWslFeatures { $script:calls.Add('features'); return $script:featureCode }
function Initialize-ODSPortalUbuntuUser([string]$Distro) { $script:calls.Add('user:' + $Distro); return $script:userSetupCode }
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
        '^--version$' { $output="Versao do WSL: 2.6.1.0`nVersao do kernel: 6.6.87.2"; if ($script:scenario -eq 'old-wsl') { $output="WSL version: 0.60.0`nKernel version: 6.6.87.2" }; if ($script:scenario -eq 'inbox-wsl') { $code=1; $output='Invalid command line option' }; break }
        '^--status$' { if ($script:scenario -eq 'features') { $code=1 }; break }
        '^--list --quiet$' { if ($script:scenario -ne 'missing') { $output='Ubuntu-24.04' }; if ($script:scenario -eq 'existing-ubuntu') { $output='Ubuntu' }; break }
        '^--install --distribution Ubuntu-24.04 --no-launch$' { $code=$script:downloadCode; break }
        '^--list --verbose$' { $output='* Ubuntu-24.04    Em Execucao   2'; if ($script:scenario -eq 'wsl1') { $output=$output -replace '2$', '1' }; if ($script:scenario -eq 'existing-ubuntu') { $output='* Ubuntu    Stopped    2' }; break }
        '^--distribution Ubuntu --exec id -u$' { $output='1000'; break }
        '^--distribution Ubuntu --exec ps -p 1 -o comm=$' { $output='systemd'; break }
        '^--distribution Ubuntu --exec docker (info|compose version)$' { break }
        '^--distribution Ubuntu-24.04 --exec id -u$' { $output='1000'; if ($script:scenario -eq 'root') { $output='0' }; break }
        '^--distribution Ubuntu-24.04 --exec ps -p 1 -o comm=$' { $output='systemd'; if ($script:scenario -eq 'init') { $output='init' }; break }
        '^--distribution Ubuntu-24.04 --exec docker info$' { if ($script:scenario -eq 'docker') { $code=1 }; break }
        '^--distribution Ubuntu-24.04 --exec docker compose version$' { if ($script:scenario -eq 'compose') { $code=1 }; break }
        default { throw "Unexpected native invocation: $key" }
    }
    return [pscustomobject]@{ Code=$code; Output=$output }
}
try {
    Check ((Resolve-ODSPortalDistro '' @('docker-desktop', 'Ubuntu')) -eq 'Ubuntu') 'reuses existing Ubuntu without creating another distro'
    Check ((Resolve-ODSPortalDistro '' @('Ubuntu-22.04')) -eq 'Ubuntu-22.04') 'reuses a single versioned Ubuntu'
    Check ((Resolve-ODSPortalDistro '' @('docker-desktop')) -eq 'Ubuntu-24.04') 'Docker internal distro is never selected'
    Check ((Resolve-ODSPortalDistro 'Ubuntu' @('Ubuntu', 'Ubuntu-24.04')) -eq 'Ubuntu') 'explicit distribution wins'
    $ambiguous = $false
    try { $null = Resolve-ODSPortalDistro '' @('Ubuntu', 'Ubuntu-24.04') } catch { $ambiguous = $true }
    Check $ambiguous 'multiple Ubuntu environments require explicit selection'
    Reset-Scenario
    $script:scenario='existing-ubuntu'
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 0) 'existing unversioned Ubuntu passes full orchestration'
    Check ($script:calls.Contains('install:Ubuntu')) 'delegate receives detected Ubuntu name'
    Check (-not $script:calls.Contains('confirm')) 'existing Ubuntu requires no download offer'
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
    foreach ($failure in @('admin','native','wsl1','root','init','docker','compose','old-wsl','inbox-wsl','no-wsl')) {
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
    Reset-Scenario
    $script:scenario='missing'; $script:downloadCode=3010
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 3010) 'Ubuntu requiring reboot preserves restart exit'
    Check (-not $script:calls.Contains('user:Ubuntu-24.04') -and -not $script:calls.Contains('install:Ubuntu-24.04')) 'restart stops before user setup and ODS'
    foreach ($phase in @('download', 'user-setup')) {
        Reset-Scenario
        $script:scenario='missing'
        if ($phase -eq 'download') { $script:downloadCode=1 } else { $script:userSetupCode=1 }
        $rejected=$false
        try { $null=Invoke-ODSPortalSetup @{} 'unused' } catch { $rejected=$true }
        Check ($rejected -and -not $script:calls.Contains('install:Ubuntu-24.04')) "$phase failure never starts ODS"
    }
    Reset-Scenario
    $script:scenario='missing'; $script:allowPreparation=$false
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 1) 'declining Ubuntu download cancels setup'
    Check (-not $script:calls.Contains('--install --distribution Ubuntu-24.04 --no-launch')) 'declining performs no download'
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
    # Exercise the actual root script in a child PowerShell, with only its
    # destination replaced. This catches failures swallowed at script boundaries.
    $fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-portal-entry-' + [guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path (Join-Path $fixture 'ods/installers') -Force
    try {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot '../../../install.ps1') -Destination $fixture
        $destination = Join-Path $fixture 'ods/installers/windows-portal.ps1'
        $shell = (Get-Process -Id $PID).Path
        foreach ($exitCode in @(0, 17, 42)) {
            Set-Content -LiteralPath $destination -Value "param([switch]`$DryRun)`nWrite-Host 'stub delegate output'`nexit $exitCode" -Encoding UTF8
            & $shell -NoProfile -File (Join-Path $fixture 'install.ps1') -DryRun | Out-Host
            Check ($LASTEXITCODE -eq $exitCode) "actual root preserves delegated exit $exitCode"
            # The nonzero exit is expected test data, not the contract's result.
            # GitHub's PowerShell runner propagates LASTEXITCODE after the script.
            $global:LASTEXITCODE = 0
        }
    } finally {
        $resolved = [IO.Path]::GetFullPath($fixture)
        $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        if (-not $resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or (Split-Path $resolved -Leaf) -notlike 'ods-portal-entry-*') { throw 'Unsafe test cleanup path' }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
    Write-Host "Passed $script:checks Windows Portal setup contracts."
} finally { $env:OS=$originalOS }
