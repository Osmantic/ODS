# No real WSL, UAC, downloads or services are invoked by these contracts.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../installers/windows/lib/wsl-portal-setup.ps1')
$originalOS = $env:OS
if ($IsLinux) {
    # Real Invoke-ODSPortalWsl: stderr warnings never reach parsed Output.
    $fake = Join-Path ([IO.Path]::GetTempPath()) ('ods-fake-wsl-' + [guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $fake
    try {
        Set-Content -LiteralPath (Join-Path $fake 'wsl.exe') -Value "#!/bin/sh`necho 'your 131072x1 screen size is bogus. expect trouble' >&2`necho systemd`nexit 0" -NoNewline
        chmod +x (Join-Path $fake 'wsl.exe')
        $previousPath = $env:PATH
        $env:PATH = $fake + [IO.Path]::PathSeparator + $env:PATH
        try { $warned = Invoke-ODSPortalWsl -Arguments @('--distribution', 'Ubuntu-24.04', '--exec', 'cat', '/proc/1/comm') } finally { $env:PATH = $previousPath }
        if ($warned.Output -ne 'systemd' -or $warned.Error -notmatch 'screen size is bogus' -or $warned.Code -ne 0) { throw 'WSL stderr warnings leak into parsed output' }
        Write-Host 'PASS WSL stderr warnings stay out of parsed output'
    } finally { Remove-Item -LiteralPath $fake -Recurse -Force }
}
$env:OS = 'Windows_NT'
$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
    Write-Host "PASS $Message"
}
# Real Invoke-ODSPortalLinuxInstaller: the delegate runs in a child PowerShell
# on this console (not through this pipeline), gets its arguments intact and
# its exit code is the only value returned.
$delegateRoot = Join-Path ([IO.Path]::GetTempPath()) ('ods-portal-delegate-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $delegateRoot
try {
    $record = Join-Path $delegateRoot 'args.json'
    Set-Content -LiteralPath (Join-Path $delegateRoot 'windows.ps1') -Encoding UTF8 -Value @"
param([string]`$Distro, [string]`$InstallRoot, [string[]]`$PassthroughArgs)
Write-Output 'delegate stdout'
[IO.File]::WriteAllText('$record', (ConvertTo-Json -Compress @{ d = `$Distro; r = `$InstallRoot; a = `$PassthroughArgs }))
exit 23
"@
    $returned = @(Invoke-ODSPortalLinuxInstaller $delegateRoot 'Ubuntu-24.04' @('--pixel', "it's `$(x)", 'two words') "/home/o'brien/ODS data")
    $seen = Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
    Check ($returned.Count -eq 1 -and $returned[0] -eq 23) 'delegate exit code is the only returned value'
    Check ($seen.d -eq 'Ubuntu-24.04' -and $seen.r -eq "/home/o'brien/ODS data" -and (@($seen.a) -join '|') -eq "--pixel|it's `$(x)|two words") 'delegate receives arguments intact'
    Set-Content -LiteralPath (Join-Path $delegateRoot 'windows.ps1') -Encoding UTF8 -Value "throw 'delegate failed'"
    Check ((Invoke-ODSPortalLinuxInstaller $delegateRoot 'Ubuntu-24.04' @() '') -ne 0) 'delegate that throws is a failure'
} finally { Remove-Item -LiteralPath $delegateRoot -Recurse -Force }
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
    $script:nvidiaDriver = $null
    $script:releaseOverride = $null
}
function Get-ODSPortalWindowsNvidiaDriver { return $script:nvidiaDriver }
function Test-ODSPortalAdministrator { return $script:scenario -eq 'admin' }
function Test-ODSNativeWindowsInstall { return $script:scenario -eq 'native' }
function Get-Command { if ($script:scenario -eq 'no-wsl') { return $null }; return [pscustomobject]@{ Name='wsl.exe' } }
function Confirm-ODSPortalPreparation([string]$Message, [bool]$NonInteractive) {
    $script:calls.Add('confirm')
    return (-not $NonInteractive -and $script:allowPreparation)
}
function Install-ODSPortalWslFeatures([switch]$MissingExecutable) { $script:calls.Add('features'); if ($MissingExecutable) { $script:calls.Add('enable-optional-features') }; return $script:featureCode }
function Initialize-ODSPortalUbuntuUser([string]$Distro) {
    $script:calls.Add('user:' + $Distro)
    if ($script:scenario -eq 'resume-user') { $script:scenario='ready' }
    return $script:userSetupCode
}
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
        '^--distribution Ubuntu --exec cat /etc/os-release$' { $output="NAME=`"Ubuntu`"`nID=ubuntu`nVERSION_ID=`"24.04`""; if ($script:releaseOverride) { $output=$script:releaseOverride }; break }
        '^--distribution Ubuntu-24.04 --exec cat /etc/os-release$' { $output="NAME=`"Ubuntu`"`nID=ubuntu`nVERSION_ID=`"24.04`""; if ($script:scenario -eq 'distro-broken') { $code=-1; $output='Catastrophic failure' }; break }
        '^--distribution Ubuntu --exec cat /proc/1/comm$' { $output='systemd'; break }
        '^--distribution Ubuntu --exec docker (info|compose version)$' { break }
        '^--distribution Ubuntu-24.04 --exec id -u$' { $output='1000'; if ($script:scenario -in @('root','resume-user')) { $output='0' }; break }
        '^--distribution Ubuntu-24.04 --exec cat /proc/1/comm$' { $output='systemd'; if ($script:scenario -eq 'init') { $output='init' }; break }
        '^--distribution Ubuntu-24.04 --exec docker info$' { if ($script:scenario -eq 'docker') { $code=1 }; break }
        '^--distribution Ubuntu-24.04 --exec docker compose version$' { if ($script:scenario -eq 'compose') { $code=1 }; break }
        '^--distribution Ubuntu-24.04 --exec /usr/lib/wsl/lib/nvidia-smi -L$' { $output='GPU 0: NVIDIA GeForce RTX 4060 (UUID: GPU-00000000)'; if ($script:scenario -eq 'gpu-hidden') { $code=1; $output='command not found' }; break }
        '^--distribution Ubuntu-24.04 --exec docker info --format \{\{json \.Runtimes\}\}$' { $output='{"io.containerd.runc.v2":{"path":"runc"},"nvidia":{"path":"/usr/bin/nvidia-container-runtime"},"runc":{"path":"runc"}}'; if ($script:scenario -eq 'no-nvidia-runtime') { $output='{"io.containerd.runc.v2":{"path":"runc"},"runc":{"path":"runc"}}' }; break }
        default { throw "Unexpected native invocation: $key" }
    }
    return [pscustomobject]@{ Code=$code; Output=$output }
}
try {
    Check ((Resolve-ODSPortalDistro '' @('docker-desktop', 'Ubuntu')) -eq 'Ubuntu') 'reuses existing Ubuntu without creating another distro'
    Check ((Resolve-ODSPortalDistro '' @('Ubuntu-26.04')) -eq 'Ubuntu-26.04') 'reuses a single Pixel-qualified versioned Ubuntu'
    Check ((Resolve-ODSPortalDistro '' @('Ubuntu-22.04')) -eq 'Ubuntu-24.04') 'never auto-selects an unqualified Ubuntu release'
    Check ((Resolve-ODSPortalDistro '' @('Ubuntu', 'Ubuntu-20.04')) -eq 'Ubuntu') 'unqualified versioned names do not make selection ambiguous'
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
    foreach ($release in @("ID=ubuntu`nVERSION_ID=`"22.04`"", "ID=ubuntu`nVERSION_ID=`"20.04`"", "ID=kali`nVERSION_ID=`"2026.1`"")) {
        Reset-Scenario
        $script:scenario='existing-ubuntu'; $script:releaseOverride=$release
        $message=''
        try { $null = Invoke-ODSPortalSetup @{} 'unused' } catch { $message = $_.Exception.Message }
        Check ($message -match '-Distro Ubuntu-24.04' -and -not $script:calls.Contains('install:Ubuntu')) "unqualified release under the Ubuntu name stops before ODS ($($release -replace '\s+', ' '))"
    }
    Reset-Scenario
    $script:scenario='distro-broken'
    $message=''
    try { $null = Invoke-ODSPortalSetup @{} 'unused' } catch { $message = $_.Exception.Message }
    Check ($message -match 'did not start' -and $message -match 'Catastrophic failure' -and $message -notmatch 'Pixel requires') 'a distro that fails to start reports the WSL error, not a wrong release'
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
    foreach ($failure in @('admin','native','wsl1','root','init','docker','compose','old-wsl','inbox-wsl')) {
        Reset-Scenario
        $script:scenario = $failure
        $rejected = $false
        try { $null = Invoke-ODSPortalSetup @{} 'unused' } catch { $rejected=$true }
        Check $rejected "$failure blocks installation"
        Check (-not $script:calls.Contains('install:Ubuntu-24.04')) "$failure never falls back or delegates"
    }
    Reset-Scenario
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 0) 'host without an NVIDIA driver installs'
    Check (-not ($script:calls -match 'nvidia-smi|Runtimes')) 'non-NVIDIA host performs no GPU probes'
    Reset-Scenario
    $script:nvidiaDriver = 576
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 0) 'NVIDIA host with WSL GPU and Docker Desktop runtime installs'
    Check ($script:calls.Contains('--distribution Ubuntu-24.04 --exec /usr/lib/wsl/lib/nvidia-smi -L')) 'NVIDIA GPU visibility is checked inside Ubuntu'
    foreach ($nvidiaFailure in @(@{name='old-driver'; driver=566; scenario='ready'}, @{name='gpu-hidden'; driver=576; scenario='gpu-hidden'}, @{name='no-nvidia-runtime'; driver=576; scenario='no-nvidia-runtime'})) {
        Reset-Scenario
        $script:nvidiaDriver = $nvidiaFailure.driver
        $script:scenario = $nvidiaFailure.scenario
        $message = ''
        try { $null = Invoke-ODSPortalSetup @{} 'unused' } catch { $message = $_.Exception.Message }
        Check ($message -and -not $script:calls.Contains('install:Ubuntu-24.04')) "$($nvidiaFailure.name) stops before the Linux installer"
        Check ($message -notmatch 'apt|nvidia-driver-') "$($nvidiaFailure.name) never suggests an in-distro driver or toolkit install"
    }
    Reset-Scenario
    $script:nvidiaDriver = 566
    Check ((Invoke-ODSPortalSetup @{Cloud=$true} 'unused') -eq 0) 'cloud mode does not require local NVIDIA readiness'
    Check (-not ($script:calls -match 'nvidia-smi|Runtimes')) 'cloud mode performs no GPU probes'
    Reset-Scenario
    $script:scenario='missing'
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 0) 'new Ubuntu initializes then installs'
    Check ($script:calls.Contains('--install --distribution Ubuntu-24.04 --no-launch')) 'downloads selected Ubuntu only'
    Check ($script:calls.Contains('user:Ubuntu-24.04')) 'first-run user setup remains interactive'
    Reset-Scenario
    $script:scenario='resume-user'
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 0) 'resume completes Ubuntu account setup before ODS'
    Check ($script:calls.Contains('user:Ubuntu-24.04') -and -not $script:calls.Contains('--install --distribution Ubuntu-24.04 --no-launch')) 'resume reuses downloaded Ubuntu'
    Check (@($script:calls | Where-Object { $_ -eq '--distribution Ubuntu-24.04 --exec id -u' }).Count -eq 2) 'default user is rechecked after interactive setup'
    Reset-Scenario
    $script:scenario='root'
    $rejected=$false
    try { $null=Invoke-ODSPortalSetup @{NonInteractive=$true} 'unused' } catch { $rejected=$true }
    Check ($rejected -and -not $script:calls.Contains('user:Ubuntu-24.04')) 'noninteractive root never opens user setup or installs ODS'
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
    Reset-Scenario
    $script:scenario='no-wsl'
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 3010) 'missing WSL executable offers feature activation and requires restart'
    Check ($script:calls.Contains('enable-optional-features')) 'missing executable uses Windows optional features instead of unavailable wsl command'
    Check (-not $script:calls.Contains('--status') -and -not $script:calls.Contains('install:Ubuntu-24.04')) 'missing executable never invokes WSL or ODS before restart'
    foreach ($case in @('missing','features','no-wsl')) {
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
