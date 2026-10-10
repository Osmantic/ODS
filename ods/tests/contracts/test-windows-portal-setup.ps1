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
param([string]`$Distro, [string]`$InstallRoot, [switch]`$OpenPortal, [string[]]`$PassthroughArgs, [string]`$DockerDesktopPath, [string]`$StateRoot, [string[]]`$NewInstallationArgs)
Write-Output 'delegate stdout'
[IO.File]::WriteAllText('$record', (ConvertTo-Json -Compress @{ d = `$Distro; r = `$InstallRoot; o = [bool]`$OpenPortal; a = `$PassthroughArgs; docker = `$DockerDesktopPath; s = `$StateRoot; n = `$NewInstallationArgs }))
exit 23
"@
    $returned = @(Invoke-ODSPortalLinuxInstaller $delegateRoot 'Ubuntu-24.04' @('--pixel', "it's `$(x)", 'two words') "/home/o'brien/ODS data" $true 'D:\Custom Docker\Docker Desktop.exe')
    $seen = Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
    Check ($returned.Count -eq 1 -and $returned[0] -eq 23) 'delegate exit code is the only returned value'
    Check ($seen.docker -ceq 'D:\Custom Docker\Docker Desktop.exe') 'delegate preserves the installer-resolved Docker executable for durable startup'
    Check ($seen.d -eq 'Ubuntu-24.04' -and $seen.r -eq "/home/o'brien/ODS data" -and $seen.o -eq $true -and (@($seen.a) -join '|') -eq "--pixel|it's `$(x)|two words") 'delegate receives arguments intact'
    $stateLocation="C:\ODS state\it's [private]"
    $returned=Invoke-ODSPortalLinuxInstaller $delegateRoot 'Ubuntu-24.04' @('--pixel') '/home/user/ods' $false '' $stateLocation
    $seen=Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
    Check ($returned -eq 23 -and $seen.s -ceq $stateLocation -and (@($seen.a) -join ' ') -eq '--pixel') 'explicit state directory reaches the child as one literal Windows argument, separate from Linux flags'
    $returned=Invoke-ODSPortalLinuxInstaller $delegateRoot 'Ubuntu-24.04' @('--pixel') '/home/user/ods' $true 'D:\Custom Docker\Docker Desktop.exe' $stateLocation
    $seen=Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
    Check ($returned -eq 23 -and $seen.s -ceq $stateLocation -and $seen.o -eq $true -and $seen.docker -ceq 'D:\Custom Docker\Docker Desktop.exe') 'state location, Docker location and Portal opening coexist in the delegated process'
    Check ($null -eq $seen.n) 'no new-installation flags reach the delegate unless setup passes them'
    $returned=Invoke-ODSPortalLinuxInstaller $delegateRoot 'Ubuntu-24.04' @('--pixel') '/home/user/ods' $false '' '' @('--no-hermes', "it's new")
    $seen=Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
    Check ($returned -eq 23 -and (@($seen.n) -join '|') -ceq "--no-hermes|it's new" -and (@($seen.a) -join ' ') -ceq '--pixel') 'new-installation flags reach the delegate intact and separate from the Linux flags'
    Set-Content -LiteralPath (Join-Path $delegateRoot 'windows.ps1') -Encoding UTF8 -Value "throw 'delegate failed'"
    Check ((Invoke-ODSPortalLinuxInstaller $delegateRoot 'Ubuntu-24.04' @() '' $false) -ne 0) 'delegate that throws is a failure'
    # The llama.cpp API key reaches the Linux installer through WSLENV only.
    $envRecord = Join-Path $delegateRoot 'env.json'
    Set-Content -LiteralPath (Join-Path $delegateRoot 'windows.ps1') -Encoding UTF8 -Value @"
param([string]`$Distro, [string]`$InstallRoot, [switch]`$OpenPortal, [string[]]`$PassthroughArgs, [string]`$DockerDesktopPath, [string]`$StateRoot)
[IO.File]::WriteAllText('$envRecord', (ConvertTo-Json -Compress @{ key = `$env:ODS_NATIVE_LLM_API_KEY; wslenv = `$env:WSLENV; command = [Environment]::CommandLine; a = `$PassthroughArgs }))
exit 0
"@
    $fixtureKey = 'f' * 64
    $previousWslEnv = $env:WSLENV
    $previousKey = $env:ODS_NATIVE_LLM_API_KEY
    try {
        $env:WSLENV = 'PSModulePath/w:ODS_NATIVE_LLM_API_KEY/w:USERPROFILE/p'
        Remove-Item Env:ODS_NATIVE_LLM_API_KEY -ErrorAction SilentlyContinue
        $returned = Invoke-ODSPortalLinuxInstaller $delegateRoot 'Ubuntu-24.04' @('--pixel', '--native-llm-api-key-env', 'ODS_NATIVE_LLM_API_KEY') '/home/user/ods' $false '' '' @() @{ ODS_NATIVE_LLM_API_KEY = $fixtureKey }
        $seen = Get-Content -LiteralPath $envRecord -Raw | ConvertFrom-Json
        Check ($returned -eq 0 -and $seen.key -ceq $fixtureKey) 'the API key reaches the delegated installer process as an environment variable'
        Check (($seen.wslenv -split ':') -contains 'ODS_NATIVE_LLM_API_KEY/u' -and ($seen.wslenv -split ':') -contains 'PSModulePath/w' -and
            ($seen.wslenv -split ':') -contains 'USERPROFILE/p' -and -not (($seen.wslenv -split ':') -contains 'ODS_NATIVE_LLM_API_KEY/w')) 'WSLENV forwards the key Win32-to-WSL only and keeps the caller''s other entries'
        Check (-not ([string]$seen.command).Contains($fixtureKey) -and -not ((@($seen.a) -join ' ').Contains($fixtureKey)) -and
            (@($seen.a) -join ' ') -ceq '--pixel --native-llm-api-key-env ODS_NATIVE_LLM_API_KEY') 'the key is never on any command line; only its variable name is passed'
        Check ($env:WSLENV -ceq 'PSModulePath/w:ODS_NATIVE_LLM_API_KEY/w:USERPROFILE/p' -and -not (Test-Path Env:ODS_NATIVE_LLM_API_KEY)) 'the setup process environment is restored after the delegate starts'
    } finally {
        $env:WSLENV = $previousWslEnv
        if ($null -eq $previousKey) { Remove-Item Env:ODS_NATIVE_LLM_API_KEY -ErrorAction SilentlyContinue } else { $env:ODS_NATIVE_LLM_API_KEY = $previousKey }
    }
} finally { Remove-Item -LiteralPath $delegateRoot -Recurse -Force }
function Reset-Scenario {
    $script:calls = [Collections.Generic.List[string]]::new()
    $script:prompts = [Collections.Generic.List[string]]::new()
    $script:scenario = 'ready'
    $script:delegateCode = 0
    $script:featureCode = 0
    $script:allowPreparation = $true
    $script:capturedArguments = @()
    $script:capturedNewInstallation = @()
    $script:capturedRoot = ''
    $script:capturedStateRoot = ''
    $script:downloadCode = 0
    $script:userSetupCode = 0
    $script:nvidiaDriver = $null
    $script:releaseOverride = $null
    $script:downloaded = $false
    $script:registerNeeded = $false
    $script:virtualization = $true
    $script:freeGB = 200
    $script:dockerInstalled = $true
    $script:dockerAlreadyInstalled = $false
    $script:engineUp = $true
    $script:integrated = $true
    $script:userEnablesIntegration = $true
    $script:launcherPresent = $true
    $script:installNeedsRestart = $false
    $script:amdPlan = $null
    $script:amdArgs = @()
    $script:amdEnvironment = @{}
    $script:capturedEnvironment = @{}
    $script:linuxHome = '/home/user'
    $script:amdBinding = @()
    $script:distroListFailure = $null
    $script:initProbes = @()
}
function Test-ODSPortalVirtualization { $script:calls.Add('virt-check'); return $script:virtualization }
function Get-ODSPortalFreeSystemGB { return $script:freeGB }
function Get-ODSPortalDockerDesktop { return [pscustomobject]@{ Installed=$script:dockerInstalled; Exe='docker-desktop.exe'; Cli='docker.exe' } }
function Install-ODSPortalDockerDesktop {
    $script:calls.Add('docker-install'); $script:dockerInstalled = $true
    return [pscustomobject]@{ AlreadyInstalled=$script:dockerAlreadyInstalled; Desktop=(Get-ODSPortalDockerDesktop) }
}
function Test-ODSPortalDockerEngine($Desktop) { return $script:engineUp }
function Start-ODSPortalDockerDesktop($Desktop) { $script:calls.Add('docker-start'); $script:engineUp = $true }
function Wait-ODSPortalDistroDocker([string]$Distro, [int]$Seconds) {
    $script:calls.Add('docker-wait:' + $Distro + ':' + $Seconds)
    # The long wait is the one after the user was shown the Docker settings.
    if ($Seconds -gt $script:ODSPortalIntegrationWaitSeconds) { $script:integrated = $script:userEnablesIntegration }
    return ($script:integrated -and $script:scenario -ne 'docker')
}
function Start-Process([string]$FilePath) { $script:calls.Add('open:' + $FilePath) }
function Get-ODSPortalDistroLauncher([string]$Distro) { if ($script:launcherPresent) { return 'ubuntu2404.exe' }; return $null }
function Enable-ODSPortalSystemd([string]$Distro) { $script:calls.Add('systemd:' + $Distro); if ($script:scenario -eq 'init') { $script:scenario = 'ready' } }
function Register-ODSPortalResume([string]$InstallerRoot, [System.Collections.IDictionary]$Options) { $script:calls.Add('resume') }
function Register-ODSPortalDistro([string]$Distro) { $script:calls.Add('register:' + $Distro); $script:registerNeeded = $false }
function Read-ODSPortalLinuxAccount { $script:calls.Add('account-prompt'); return [pscustomobject]@{ Name='maria'; Password='not-logged' } }
function New-ODSPortalLinuxAccount([string]$Distro, $Account) {
    $script:calls.Add('account:' + $Distro + ':' + $Account.Name)
    if ($script:userSetupCode -ne 0) { throw 'account creation failed' }
    if ($script:scenario -eq 'resume-user') { $script:scenario='ready' }
}
function Get-ODSPortalWindowsNvidiaDriver { return $script:nvidiaDriver }
function Get-ODSPortalAmdPlan([string]$SourceRoot) { $script:calls.Add('amd-plan'); return $script:amdPlan }
function Initialize-ODSPortalAmdRuntime($Plan, [string]$SourceRoot, [bool]$NonInteractive, [string]$WslDistro, [string]$WslInstallDir) {
    $script:calls.Add('amd-runtime:' + $Plan.GpuName)
    $script:amdBinding = @($WslDistro, $WslInstallDir)
    if (@($script:amdArgs).Count -eq 0) { return $null }
    return [pscustomobject]@{ Arguments = $script:amdArgs; Environment = $script:amdEnvironment }
}
function Test-ODSPortalAdministrator { return $script:scenario -eq 'admin' }
function Test-ODSNativeWindowsInstall { return $script:scenario -eq 'native' }
function Get-Command { if ($script:scenario -eq 'no-wsl') { return $null }; return [pscustomobject]@{ Name='wsl.exe' } }
function Confirm-ODSPortalPreparation([string]$Message, [bool]$NonInteractive) {
    $script:calls.Add('confirm')
    $script:prompts.Add($Message)
    return (-not $NonInteractive -and $script:allowPreparation)
}
function Install-ODSPortalWslFeatures([switch]$MissingExecutable) { $script:calls.Add('features'); if ($MissingExecutable) { $script:calls.Add('enable-optional-features') }; return $script:featureCode }
function Initialize-ODSPortalUbuntuUser([string]$Distro) {
    $script:calls.Add('user:' + $Distro)
    if ($script:scenario -eq 'resume-user') { $script:scenario='ready' }
    return $script:userSetupCode
}
function Invoke-ODSPortalLinuxInstaller([string]$InstallerRoot, [string]$Distro, [string[]]$LinuxArguments, [string]$InstallRoot, [bool]$OpenPortal, [string]$DockerDesktopPath = '', [string]$StateRoot = '', [string[]]$NewInstallationArguments = @(), [System.Collections.IDictionary]$ForwardEnvironment = @{}) {
    $script:calls.Add('install:' + $Distro)
    $script:openPortal = $OpenPortal
    $script:capturedArguments = $LinuxArguments
    $script:capturedNewInstallation = $NewInstallationArguments
    $script:capturedRoot = $InstallRoot
    $script:capturedStateRoot = $StateRoot
    $script:capturedEnvironment = $ForwardEnvironment
    return $script:delegateCode
}
function Invoke-ODSPortalWsl([string[]]$Arguments) {
    $key = $Arguments -join ' '
    $script:calls.Add($key)
    $code = 0
    $output = ''
    $stderr = ''
    switch -Regex ($key) {
        '^--distribution (Ubuntu|Ubuntu-24.04) --exec printenv HOME$' { $output = $script:linuxHome; break }
        '^--version$' { $output="Versao do WSL: 2.6.1.0`nVersao do kernel: 6.6.87.2"; if ($script:scenario -eq 'old-wsl') { $output="WSL version: 0.60.0`nKernel version: 6.6.87.2" }; if ($script:scenario -eq 'inbox-wsl') { $code=1; $output='Invalid command line option' }; break }
        '^--status$' { if ($script:scenario -eq 'features') { $code=1 }; break }
        '^--list --quiet$' {
            if ($script:distroListFailure -and -not $script:downloaded) {
                $code=$script:distroListFailure.Code; $output=$script:distroListFailure.Output; $stderr=$script:distroListFailure.Error
                break
            }
            if ($script:scenario -ne 'missing' -or ($script:downloaded -and -not $script:registerNeeded)) { $output='Ubuntu-24.04' }
            if ($script:scenario -eq 'existing-ubuntu') { $output='Ubuntu' }
            break
        }
        '^--install --distribution Ubuntu-24.04 --no-launch$' { $code=$script:downloadCode; if ($code -eq 0 -and -not $script:installNeedsRestart) { $script:downloaded = $true }; break }
        '^--list --verbose$' { $output='* Ubuntu-24.04    Em Execucao   2'; if ($script:scenario -eq 'wsl1') { $output=$output -replace '2$', '1' }; if ($script:scenario -eq 'existing-ubuntu') { $output='* Ubuntu    Stopped    2' }; break }
        '^--distribution Ubuntu --exec id -u$' { $output='1000'; break }
        '^--distribution Ubuntu --exec cat /etc/os-release$' { $output="NAME=`"Ubuntu`"`nID=ubuntu`nVERSION_ID=`"24.04`""; if ($script:releaseOverride) { $output=$script:releaseOverride }; break }
        '^--distribution Ubuntu-24.04 --exec cat /etc/os-release$' { $output="NAME=`"Ubuntu`"`nID=ubuntu`nVERSION_ID=`"24.04`""; if ($script:scenario -eq 'distro-broken') { $code=-1; $output='Catastrophic failure' }; break }
        '^--distribution Ubuntu --exec cat /proc/1/comm$' { $output='systemd'; break }
        '^--distribution Ubuntu --exec docker (info|compose version)$' { break }
        '^--distribution Ubuntu-24.04 --exec id -u$' { $output='1000'; if ($script:scenario -in @('root','resume-user')) { $output='0' }; break }
        '^--distribution Ubuntu-24.04 --exec cat /proc/1/comm$' {
            if ($script:initProbes.Count -gt 0) {
                $probe = $script:initProbes[0]
                $script:initProbes = @($script:initProbes | Select-Object -Skip 1)
                $code=$probe.Code; $output=$probe.Output; $stderr=$probe.Error
            } else {
                $output='systemd'; if ($script:scenario -in @('init','init-stuck')) { $output='init' }
            }
            break
        }
        '^--distribution Ubuntu-24.04 --exec docker info$' { if ($script:scenario -eq 'docker' -or -not $script:integrated) { $code=1 }; break }
        '^--distribution Ubuntu-24.04 --exec docker compose version$' { if ($script:scenario -eq 'compose') { $code=1 }; break }
        '^--distribution Ubuntu-24.04 --exec /usr/lib/wsl/lib/nvidia-smi -L$' { $output='GPU 0: NVIDIA GeForce RTX 4060 (UUID: GPU-00000000)'; if ($script:scenario -eq 'gpu-hidden') { $code=1; $output='command not found' }; break }
        '^--distribution Ubuntu-24.04 --exec docker info --format \{\{json \.Runtimes\}\}$' { $output='{"io.containerd.runc.v2":{"path":"runc"},"nvidia":{"path":"/usr/bin/nvidia-container-runtime"},"runc":{"path":"runc"}}'; if ($script:scenario -eq 'no-nvidia-runtime') { $output='{"io.containerd.runc.v2":{"path":"runc"},"runc":{"path":"runc"}}' }; break }
        default { throw "Unexpected native invocation: $key" }
    }
    return [pscustomobject]@{ Code=$code; Output=$output; Error=$stderr }
}
try {
    foreach ($file in @('wsl-portal-setup.ps1', 'wsl-portal-prereqs.ps1')) {
        $parsed = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot "../../installers/windows/lib/$file"), [ref]$null, [ref]$null)
        $bad = $parsed.FindAll({ param($n) $n -is [System.Management.Automation.Language.VariableExpressionAst] -and $n.VariablePath.UserPath -ne '?' -and $n.VariablePath.UserPath.EndsWith('?') }, $true)
        Check (@($bad).Count -eq 0) "$file never interpolates a variable followed by ? (PowerShell reads `$name? as one variable)"
    }
    Check ((Resolve-ODSPortalDistro '' @('docker-desktop', 'Ubuntu')) -eq 'Ubuntu') 'reuses existing Ubuntu without creating another distro'
    Check ((Resolve-ODSPortalDistro '' @('Ubuntu-26.04')) -eq 'Ubuntu-26.04') 'reuses a single Pixel-qualified versioned Ubuntu'
    Check ((Resolve-ODSPortalDistro '' @('Ubuntu-22.04')) -eq 'Ubuntu-24.04') 'never auto-selects an unqualified Ubuntu release'
    Check ((Resolve-ODSPortalDistro '' @('Ubuntu', 'Ubuntu-20.04')) -eq 'Ubuntu') 'unqualified versioned names do not make selection ambiguous'
    Check ((Resolve-ODSPortalDistro '' @('docker-desktop')) -eq 'Ubuntu-24.04') 'Docker internal distro is never selected'
    Check ((Resolve-ODSPortalDistro 'Ubuntu' @('Ubuntu', 'Ubuntu-24.04')) -eq 'Ubuntu') 'explicit distribution wins'
    Check ((Resolve-ODSPortalDistro 'ubuntu-24.04' @('Ubuntu-24.04')) -ceq 'Ubuntu-24.04') 'explicit distribution uses its registered casing for ownership'
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
    $options = @{All=$true; NoLangfuse=$true; Tier='2'; InstallDir='/home/user/ODS data'; SummaryJsonPath='/home/user/result.json'; StateRoot='C:\ODS private\state'}
    Check ((Invoke-ODSPortalSetup $options 'unused') -eq 0) 'ready host delegates successfully'
    Check (($script:capturedArguments[-2..-1] -join ' ') -eq '--pixel --no-hermes') 'mandatory Pixel policy wins after --all'
    Check (($script:capturedArguments -join ' ') -match '--all --no-langfuse') 'explicit disable follows all'
    Check ($script:capturedRoot -eq '/home/user/ODS data') 'Linux install path forwarded intact'
    Check ($script:capturedStateRoot -ceq 'C:\ODS private\state' -and ($script:capturedArguments -join ' ') -notmatch 'StateRoot|ODS private') 'setup forwards Windows state location without injecting it into Linux flags'
    Check ((@($script:capturedNewInstallation) -join ' ') -ceq '--no-hermes') 'a new installation still starts without Hermes'
    Reset-Scenario
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 0) 'ready host without -All delegates successfully'
    Check ($script:capturedArguments[-1] -ceq '--pixel' -and $script:capturedArguments -notcontains '--no-hermes') 'Linux flags leave --no-hermes out, so a rerun keeps the installed selection'
    Check ((@($script:capturedNewInstallation) -join ' ') -ceq '--no-hermes') 'Hermes is turned off only through the new-installation flag'
    Reset-Scenario
    $null = Invoke-ODSPortalSetup @{NoHermes=$true} 'unused'
    Check ($script:capturedArguments -contains '--no-hermes') '-NoHermes turns Hermes off on a rerun too'
    # windows.ps1 owns the new-installation probe (no <root>/.env before
    # install-core). Run its real flag block for a new installation and a rerun.
    $windowsAst = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot '../../installers/windows.ps1'), [ref]$null, [ref]$null)
    $envProbe = $windowsAst.Find({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$newInstallation' }, $true)
    $flagBlock = $windowsAst.Find({ param($n) $n -is [System.Management.Automation.Language.IfStatementAst] -and $n.Clauses[0].Item1.Extent.Text -eq '$newInstallation -and $NewInstallationArgs' }, $true)
    $launch = $windowsAst.Find({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and $n.Extent.Text -eq '& wsl.exe -d $Distro bash -lc $wslCommand' }, $true)
    Check ($envProbe -and $flagBlock -and $launch -and $envProbe.Extent.EndOffset -lt $flagBlock.Extent.StartOffset -and $flagBlock.Extent.EndOffset -lt $launch.Extent.StartOffset) 'windows.ps1 decides the new-installation flags after its .env probe and before the Linux installer runs'
    function New-ODSWslInstallerCommand([string]$RepoRoot, [string[]]$Arguments, [string]$ResolvedRoot) { "install-core $($Arguments -join ' ')" }
    $repoRootWsl = '/mnt/c/source'
    $lifetimeIdentity = [pscustomobject]@{ installRoot = '/home/user/ods' }
    $NewInstallationArgs = @('--no-hermes')
    foreach ($case in @(
        @{ New=$false; Given=@('--windows-system-directory', 'C:\Windows\system32', '--pixel'); Expected='--windows-system-directory C:\Windows\system32 --pixel'; Command='unchanged'; Name='a rerun omits --no-hermes, so the installed selection stays' },
        @{ New=$true; Given=@('--windows-system-directory', 'C:\Windows\system32', '--pixel'); Expected='--windows-system-directory C:\Windows\system32 --pixel --no-hermes'; Command='install-core --windows-system-directory C:\Windows\system32 --pixel --no-hermes'; Name='a new installation gets --no-hermes' },
        @{ New=$true; Given=@('--all', '--pixel', '--no-hermes'); Expected='--all --pixel --no-hermes'; Command='install-core --all --pixel --no-hermes'; Name='a new installation with -All gets the flag once' }
    )) {
        $newInstallation = $case.New
        $PassthroughArgs = $case.Given
        $wslCommand = 'unchanged'
        . ([scriptblock]::Create($flagBlock.Extent.Text))
        Check ((@($PassthroughArgs) -join ' ') -ceq $case.Expected -and $wslCommand -ceq $case.Command) ('windows.ps1: ' + $case.Name)
    }
    foreach ($badState in @('relative\state','C:\','\\server\share','C:\a\..\state','C:\bad"state')) {
        Reset-Scenario
        $message=''
        try { $null=Invoke-ODSPortalSetup @{StateRoot=$badState} 'unused' } catch { $message=$_.Exception.Message }
        Check ($message -match '-StateRoot requires' -and $script:calls.Count -eq 0) 'unsafe state location fails before prerequisite or installation mutation'
    }
    Reset-Scenario
    $null=Invoke-ODSPortalSetup $options 'unused'
    Check ($script:calls.Contains('--distribution Ubuntu-24.04 --exec docker compose version')) 'checks Compose inside selected distro'
    foreach ($failure in @('admin','native','wsl1','root','init-stuck','docker','compose','old-wsl','inbox-wsl')) {
        Reset-Scenario
        $script:scenario = $failure
        $rejected = $false
        try { $null = Invoke-ODSPortalSetup @{} 'unused' } catch { $rejected=$true }
        Check $rejected "$failure blocks installation"
        Check (-not $script:calls.Contains('install:Ubuntu-24.04')) "$failure never falls back or delegates"
    }
    foreach ($nonInteractive in @($false, $true)) {
        foreach ($probe in @(
            @{Code=-1; Output='Wsl/Service/0x8007274c'; Error='connection timed out'},
            @{Code=1; Output=''; Error='Wsl/Service/0x8007274c'},
            @{Code=1; Output='systemd'; Error='Wsl/Service/0x8007274c'},
            @{Code=0; Output=''; Error=''},
            @{Code=0; Output="init`nUnexpected second line"; Error=''}
        )) {
            Reset-Scenario
            $script:initProbes = @($probe)
            $message = ''
            try { $null = Invoke-ODSPortalSetup @{NonInteractive=$nonInteractive} 'unused' } catch { $message=$_.Exception.Message }
            Check ($message -match 'systemd state is unknown' -and $message -notmatch 'Enable systemd=true|wsl --terminate') 'unreadable PID 1 reports unknown systemd state without configuration advice'
            if ($probe.Code -ne 0) {
                Check ($message -match '0x8007274c' -and $message.Contains("wsl exit $($probe.Code)")) 'failed PID 1 preserves the WSL exit code and diagnostic'
            }
            Check (-not $script:calls.Contains('confirm') -and -not $script:calls.Contains('systemd:Ubuntu-24.04')) 'unknown systemd state never offers or enables systemd'
            Check (-not ($script:calls -match '^(install:|docker-wait:|amd-runtime:)')) 'unknown systemd state stops before Docker preparation or installation'
        }
    }
    Reset-Scenario
    $script:initProbes = @(
        @{Code=0; Output='init'; Error=''},
        @{Code=1; Output=''; Error='Wsl/Service/0x8007274c'}
    )
    $message = ''
    try { $null = Invoke-ODSPortalSetup @{} 'unused' } catch { $message=$_.Exception.Message }
    Check ($message -match 'systemd state is unknown' -and $message -match '0x8007274c' -and $message -notmatch 'Enable systemd=true|wsl --terminate') 'failed post-enable PID 1 recheck preserves the transport failure'
    Check (@($script:calls | Where-Object { $_ -eq 'systemd:Ubuntu-24.04' }).Count -eq 1 -and @($script:calls | Where-Object { $_ -eq 'confirm' }).Count -eq 1) 'post-enable failure does not retry systemd changes or ask again'
    Check (-not $script:calls.Contains('install:Ubuntu-24.04')) 'failed post-enable recheck never delegates installation'
    Reset-Scenario
    $script:scenario = 'init'
    $message = ''
    try { $null = Invoke-ODSPortalSetup @{NonInteractive=$true} 'unused' } catch { $message=$_.Exception.Message }
    Check ($message -match 'Enable systemd=true' -and -not $script:calls.Contains('systemd:Ubuntu-24.04')) 'confirmed non-systemd retains manual guidance in non-interactive mode'
    Reset-Scenario
    $script:scenario = 'init'
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 0) 'existing Ubuntu without systemd is fixed after consent'
    $order = $script:calls.ToArray()
    Check ([Array]::IndexOf($order, 'systemd:Ubuntu-24.04') -ge 0 -and [Array]::IndexOf($order, 'systemd:Ubuntu-24.04') -lt [Array]::IndexOf($order, 'install:Ubuntu-24.04')) 'systemd is turned on before ODS installs'
    Check (@($script:calls | Where-Object { $_ -eq '--distribution Ubuntu-24.04 --exec cat /proc/1/comm' }).Count -eq 2) 'PID 1 is rechecked after turning on systemd'
    Reset-Scenario
    $script:scenario = 'init'; $script:allowPreparation = $false
    $rejected = $false
    try { $null = Invoke-ODSPortalSetup @{} 'unused' } catch { $rejected = $true }
    Check ($rejected -and -not $script:calls.Contains('systemd:Ubuntu-24.04')) 'declined systemd change edits nothing and stops'
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
    Check (-not $script:calls.Contains('amd-plan')) 'cloud mode does not plan an AMD GPU route'
    # AMD: the model runs in llama.cpp on Windows; Linux gets --native-llm-*.
    $fixturePlan = [pscustomobject]@{ GpuName='AMD Radeon RX 9070 XT'; VramMB=16304; Model='qwen3.5-9b'; LinuxTier='2' }
    $fixtureNativeArgs = @('--native-llm-url', 'http://localhost:8080', '--native-llm-host-transport', 'model-router',
        '--native-llm-model', 'Qwen3.5-9B-Q4_K_M.gguf', '--native-llm-context-size', '65536',
        '--native-llm-gpu-name', 'AMD Radeon RX 9070 XT', '--native-llm-gpu-vram-mb', '16304', '--native-llm-api-key-env', 'ODS_NATIVE_LLM_API_KEY')
    $fixtureEnvironment = @{ ODS_NATIVE_LLM_API_KEY = ('e' * 64) }
    Reset-Scenario
    $script:amdPlan = $fixturePlan
    $script:amdArgs = $fixtureNativeArgs
    $script:amdEnvironment = $fixtureEnvironment
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 0) 'AMD host installs through llama.cpp on Windows'
    Check (($script:capturedArguments -join ' ') -match '--pixel --native-llm-url http://localhost:8080 --native-llm-host-transport model-router --native-llm-model Qwen3\.5-9B-Q4_K_M\.gguf --native-llm-context-size 65536 --native-llm-gpu-name AMD Radeon RX 9070 XT --native-llm-gpu-vram-mb 16304 --native-llm-api-key-env ODS_NATIVE_LLM_API_KEY --tier 2$') 'AMD host passes the native llama.cpp route and GPU tier to Linux'
    Check ((@($script:capturedNewInstallation) -join ' ') -ceq '--no-hermes') 'AMD host keeps the new-installation flag'
    Check (-not (($script:capturedArguments -join ' ') -match 'lemonade') -and -not (($script:capturedArguments -join ' ').Contains($fixtureEnvironment.ODS_NATIVE_LLM_API_KEY))) 'no Lemonade flag and no key value reach the Linux arguments'
    Check ($script:capturedEnvironment['ODS_NATIVE_LLM_API_KEY'] -ceq $fixtureEnvironment.ODS_NATIVE_LLM_API_KEY) 'the key is handed to the Linux installer launch for WSLENV forwarding'
    Check ($script:calls.IndexOf('amd-runtime:AMD Radeon RX 9070 XT') -lt $script:calls.IndexOf('install:Ubuntu-24.04')) 'the Windows model server is ready before the Linux installer starts'
    Check (($script:amdBinding -join '|') -ceq 'Ubuntu-24.04|/home/user/ods' -and $script:capturedRoot -ceq '/home/user/ods') 'default AMD binding and delegated install use the same explicit Linux path'
    Reset-Scenario
    $script:amdPlan = $fixturePlan; $script:amdArgs = $fixtureNativeArgs; $script:amdEnvironment = $fixtureEnvironment; $script:delegateCode = 9
    $failureNotice = @(& { $script:failedResult = Invoke-ODSPortalSetup @{} 'unused' } 6>&1 | ForEach-Object { [string]$_ }) -join "`n"
    Check ($script:failedResult -eq 9 -and $failureNotice -match 'chat stays unavailable until it does\. Rerun the same install\.ps1 command') 'a failed Linux step after the Windows cutover tells the user to rerun'
    Reset-Scenario
    $script:amdPlan = $fixturePlan; $script:amdArgs = $fixtureNativeArgs; $script:linuxHome = "/home/some user's home"
    $null = Invoke-ODSPortalSetup @{} 'unused'
    Check ($script:amdBinding[1] -ceq "/home/some user's home/ods" -and $script:capturedRoot -ceq $script:amdBinding[1]) 'HOME spaces and apostrophes survive binding without shell interpolation'
    Reset-Scenario
    $script:amdPlan = $fixturePlan; $script:amdArgs = $fixtureNativeArgs
    $null = Invoke-ODSPortalSetup @{InstallDir='/srv/ODS data'} 'unused'
    Check ($script:amdBinding[1] -ceq '/srv/ODS data' -and $script:capturedRoot -ceq '/srv/ODS data' -and
        -not ($script:calls -like '*printenv HOME')) 'custom install directory is bound verbatim without querying HOME'
    foreach ($badHome in @("/home/user`n/tmp/other", '/home/user/../other', 'relative/home', '/')) {
        Reset-Scenario
        $script:amdPlan = $fixturePlan; $script:linuxHome = $badHome
        $message = ''
        try { $null = Invoke-ODSPortalSetup @{} 'unused' } catch { $message = $_.Exception.Message }
        Check ($message -and -not ($script:calls -like 'amd-runtime:*') -and -not ($script:calls -like 'install:*')) 'ambiguous or non-normalized HOME stops before model and Linux installation'
    }
    Reset-Scenario
    $script:amdPlan = $fixturePlan
    $script:amdArgs = $fixtureNativeArgs
    Check ((Invoke-ODSPortalSetup @{Tier='3'} 'unused') -eq 0) 'AMD host with an explicit tier installs'
    Check ((@($script:capturedArguments | Where-Object { $_ -eq '--tier' })).Count -eq 1 -and ($script:capturedArguments -join ' ') -match '--tier 3') 'explicit -Tier is kept and not duplicated by the AMD route'
    Reset-Scenario
    $script:amdPlan = $fixturePlan
    $script:amdArgs = @()
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 0) 'AMD host that keeps the CPU route still installs'
    Check (-not (($script:capturedArguments -join ' ') -match 'native-llm|lemonade|--tier') -and $script:capturedEnvironment.Count -eq 0) 'the CPU route passes no native flags and no key'
    Reset-Scenario
    $script:nvidiaDriver = 576
    $script:amdPlan = $fixturePlan
    $null = Invoke-ODSPortalSetup @{} 'unused'
    Check (-not $script:calls.Contains('amd-plan')) 'NVIDIA host never takes the AMD route'
    Reset-Scenario
    $script:scenario='missing'
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 0) 'new Ubuntu initializes then installs'
    Check ($script:calls.Contains('--install --distribution Ubuntu-24.04 --no-launch')) 'downloads selected Ubuntu only'
    Check ($script:calls.Contains('account:Ubuntu-24.04:maria')) 'first Ubuntu account is created from PowerShell'
    Check (-not $script:calls.Contains('user:Ubuntu-24.04')) 'new Ubuntu never opens the interactive Ubuntu window'
    Check ([Array]::IndexOf($script:calls.ToArray(), 'account:Ubuntu-24.04:maria') -lt [Array]::IndexOf($script:calls.ToArray(), 'install:Ubuntu-24.04')) 'account exists before ODS installs'
    foreach ($channel in @('Output', 'Error')) {
        Reset-Scenario
        $script:scenario='missing'
        $script:distroListFailure=@{Code=-1; Output=''; Error=''}
        $script:distroListFailure[$channel]=([string][char]0x65E5) + " localized diagnostic`nWsl/Service/WSL_E_DEFAULT_DISTRO_NOT_FOUND"
        Check (@(Get-ODSPortalDistroNames).Count -eq 0) "known no-distribution code in $channel is an empty list"
        $script:dockerInstalled=$false
        Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 3010) "fresh WSL without a distro or Docker reaches the required restart ($channel diagnostic)"
        Check ($script:calls.Contains('--install --distribution Ubuntu-24.04 --no-launch') -and $script:calls.Contains('docker-install') -and -not $script:calls.Contains('install:Ubuntu-24.04')) 'missing distro installs Ubuntu and Docker without premature delegation'
        $script:engineUp=$false
        Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 0) 'resuming after Docker installation reaches Pixel setup'
        Check ($script:calls.Contains('docker-start') -and $script:calls.Contains('install:Ubuntu-24.04')) 'resumed setup starts Docker and delegates to Ubuntu'
        Check ($script:capturedArguments -contains '--pixel' -and $script:capturedNewInstallation -contains '--no-hermes') 'empty-host recovery retains Pixel and excludes Hermes from the new installation'
    }
    foreach ($failure in @('Wsl/E_ACCESSDENIED', 'Wsl/WSL_E_SERVICE_NOT_AVAILABLE', 'Wsl/WSL_E_DEFAULT_DISTRO_NOT_FOUND_OTHER', 'Wsl/NOT_WSL_E_DEFAULT_DISTRO_NOT_FOUND', '')) {
        Reset-Scenario
        $script:distroListFailure=@{Code=1; Output=''; Error=$failure}
        $message=''
        try { $null=Invoke-ODSPortalSetup @{} 'unused' } catch { $message=$_.Exception.Message }
        Check ($message -like 'Cannot list WSL distributions:*') 'unknown listing failures stay visible'
        Check (-not $script:calls.Contains('--install --distribution Ubuntu-24.04 --no-launch') -and -not $script:calls.Contains('docker-install') -and -not $script:calls.Contains('install:Ubuntu-24.04')) 'unknown listing failure never provisions or delegates'
    }
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
    # Real wsl.exe exits 0 without downloading when Windows must restart first.
    $script:scenario='missing'; $script:installNeedsRestart=$true; $script:launcherPresent=$false
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 3010) 'wsl --install that needs a restart (exit 0, nothing downloaded) requests the restart'
    Check (-not $script:calls.Contains('account-prompt') -and -not $script:calls.Contains('install:Ubuntu-24.04')) 'restart stops before user setup and ODS'
    Check ($script:calls.Contains('resume')) 'Ubuntu restart registers automatic continuation'
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
    Check ($script:calls.Contains('resume')) 'WSL feature restart registers automatic continuation'
    Check ($script:calls.Contains('virt-check')) 'virtualization is checked before enabling WSL'
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
    foreach ($bad in @(@{Hermes=$true}, @{InstallDir='D:\ODS'}, @{InstallDir='/'}, @{SummaryJsonPath='/tmp/../secret'}, @{Tier='9'}, @{Distro='x --user root'})) {
        Reset-Scenario
        $rejected=$false
        try { $null=Invoke-ODSPortalSetup $bad 'unused' } catch { $rejected=$true }
        Check ($rejected -and $script:calls.Count -eq 0) 'invalid options fail before system operations'
    }
    # The legacy OpenClaw extension was removed. Existing commands that still
    # pass -OpenClaw keep working: the switch prints a notice and is ignored.
    Reset-Scenario
    # Join the host records directly; Out-String would wrap at console width.
    $legacyNotice = @(& { $script:legacyResult = Invoke-ODSPortalSetup @{OpenClaw=$true} 'unused' } 6>&1 | ForEach-Object { [string]$_ }) -join "`n"
    Check ($script:legacyResult -eq 0 -and $script:calls.Contains('install:Ubuntu-24.04')) 'legacy -OpenClaw is accepted'
    Check (($script:capturedArguments -join ' ') -notmatch 'openclaw') 'legacy -OpenClaw passes nothing to the Linux installer'
    Check ($legacyNotice -match 'The legacy OpenClaw extension was removed; -OpenClaw is ignored\.') 'legacy -OpenClaw prints the removal notice'
    Reset-Scenario
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 0) 'ready host installs'
    Check ($script:openPortal) 'interactive install opens Portal at the end'
    Check (-not $script:calls.Contains('virt-check')) 'working WSL skips the firmware virtualization probe'
    Reset-Scenario
    $null = Invoke-ODSPortalSetup @{NonInteractive=$true} 'unused'
    Check (-not $script:openPortal) 'non-interactive install never opens a browser'
    Reset-Scenario
    $script:freeGB = 12; $script:scenario = 'features'
    $message=''
    try { $null = Invoke-ODSPortalSetup @{} 'unused' } catch { $message = $_.Exception.Message }
    Check ($message -match '40 GB' -and -not $script:calls.Contains('features')) 'low disk space stops before WSL is installed'
    Reset-Scenario
    $script:freeGB = 12
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 0) 'low disk space on a host with WSL (rerun) only warns'
    Reset-Scenario
    $script:scenario='no-wsl'; $script:virtualization=$false
    $message=''
    try { $null = Invoke-ODSPortalSetup @{} 'unused' } catch { $message = $_.Exception.Message }
    Check ($message -match 'BIOS' -and -not $script:calls.Contains('features')) 'disabled virtualization stops before enabling WSL'
    Reset-Scenario
    $script:scenario='no-wsl'; $script:dockerInstalled=$false
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 3010) 'WSL and Docker Desktop share one restart'
    Check ($script:calls.Contains('features') -and $script:calls.Contains('docker-install') -and $script:calls.Contains('resume')) 'missing Docker Desktop is installed before the WSL restart'
    Reset-Scenario
    $script:scenario='missing'; $script:registerNeeded=$true
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 0) 'Store Ubuntu is registered without its account wizard'
    Check ($script:calls.Contains('register:Ubuntu-24.04')) 'unregistered download uses the Ubuntu launcher'
    Reset-Scenario
    $script:dockerInstalled=$false
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 3010) 'installing Docker Desktop requests a restart'
    Check ($script:calls.Contains('docker-install') -and $script:calls.Contains('resume') -and -not $script:calls.Contains('install:Ubuntu-24.04')) 'Docker Desktop install continues after restart, not before'
    Reset-Scenario
    $script:dockerInstalled=$false; $script:dockerAlreadyInstalled=$true; $script:engineUp=$false
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 0) 'a verified winget no-update result continues with the existing Desktop'
    Check ($script:calls.Contains('docker-start') -and -not $script:calls.Contains('resume')) 'existing Desktop starts without scheduling a Windows restart'
    $order = $script:calls.ToArray()
    $delegate = [Array]::IndexOf($order, 'install:Ubuntu-24.04')
    foreach ($probe in @('--distribution Ubuntu-24.04 --exec docker info', '--distribution Ubuntu-24.04 --exec docker compose version')) {
        $index = [Array]::IndexOf($order, $probe)
        Check ($index -ge 0 -and $index -lt $delegate) 'existing Desktop must pass its WSL Docker and Compose probes before Linux setup'
    }
    Reset-Scenario
    $script:dockerInstalled=$false; $script:dockerAlreadyInstalled=$true
    $script:integrated=$false; $script:userEnablesIntegration=$false
    $message=''
    try { $null = Invoke-ODSPortalSetup @{} 'unused' } catch { $message = $_.Exception.Message }
    Check ($message -match 'Resources > WSL integration, turn on Ubuntu-24\.04' -and -not $script:calls.Contains('install:Ubuntu-24.04') -and -not $script:calls.Contains('resume')) 'winget no-update never bypasses missing WSL integration'
    Reset-Scenario
    $script:dockerInstalled=$false; $script:dockerAlreadyInstalled=$true; $script:scenario='compose'
    $message=''
    try { $null = Invoke-ODSPortalSetup @{} 'unused' } catch { $message = $_.Exception.Message }
    Check ($message -match 'docker compose version must succeed' -and -not $script:calls.Contains('install:Ubuntu-24.04') -and -not $script:calls.Contains('resume')) 'winget no-update never bypasses a failed Compose probe'
    Reset-Scenario
    $script:dockerInstalled=$false; $script:allowPreparation=$false
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 1) 'declining Docker Desktop cancels setup'
    Check (-not $script:calls.Contains('docker-install')) 'declined Docker Desktop is never installed'
    Reset-Scenario
    $script:engineUp=$false
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 0) 'stopped Docker Desktop is started automatically'
    Check ($script:calls.Contains('docker-start')) 'Docker Desktop start was requested'
    Reset-Scenario
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 0 -and -not ($script:calls -like 'open:*')) 'integrated Docker needs no user action'
    Reset-Scenario
    $script:integrated=$false
    Check ((Invoke-ODSPortalSetup @{} 'unused') -eq 0) 'missing WSL integration continues once the user turns it on'
    $order = $script:calls.ToArray()
    $short = [Array]::IndexOf($order, 'docker-wait:Ubuntu-24.04:' + $script:ODSPortalIntegrationWaitSeconds)
    $open = [Array]::IndexOf($order, 'open:docker-desktop.exe')
    $long = [Array]::IndexOf($order, 'docker-wait:Ubuntu-24.04:' + $script:ODSPortalDockerWaitSeconds)
    Check ($short -ge 0 -and $short -lt $open -and $open -lt $long -and $long -lt [Array]::IndexOf($order, 'install:Ubuntu-24.04')) 'setup waits, shows Docker Desktop, waits for the user, then installs'
    Reset-Scenario
    $script:integrated=$false; $script:userEnablesIntegration=$false
    $message=''
    try { $null = Invoke-ODSPortalSetup @{} 'unused' } catch { $message = $_.Exception.Message }
    Check ($message -match 'Resources > WSL integration, turn on Ubuntu-24\.04' -and -not $script:calls.Contains('install:Ubuntu-24.04')) 'integration still off stops with the exact Docker Desktop steps'
    Reset-Scenario
    $script:integrated=$false
    $rejected=$false
    try { $null = Invoke-ODSPortalSetup @{NonInteractive=$true} 'unused' } catch { $rejected=$true }
    Check ($rejected -and -not ($script:calls -like 'open:*') -and -not $script:calls.Contains('install:Ubuntu-24.04')) 'non-interactive setup reports missing integration without waiting for the user'
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

    # API mode: an external OpenAI-compatible server serves the model. The key
    # file stays on Windows and only its variable name reaches the Linux flags.
    $api = @(Get-ODSPortalLinuxArguments @{ ExternalLlmUrl = 'https://api.example.test'; ExternalLlmModel = 'deepseek-v4.1-flash'; ExternalLlmKeyFile = 'C:\keys\api.key' })
    $joined = $api -join ' '
    Check ($joined.Contains('--external-llm-url https://api.example.test --external-llm-provider openai-compatible --external-llm-model deepseek-v4.1-flash') -and
        $joined.Contains('--external-llm-key-env ODS_EXTERNAL_LLM_API_KEY') -and -not $joined.Contains('api.key')) 'API mode passes the server, model and key variable name, never the key file or key'
    $ollama = (@(Get-ODSPortalLinuxArguments @{ ExternalLlmUrl = 'http://192.168.1.20:11434'; ExternalLlmModel = 'qwen3:8b'; ExternalLlmProvider = 'ollama' }) -join ' ')
    Check ($ollama.Contains('--external-llm-provider ollama') -and -not $ollama.Contains('--external-llm-key-env')) 'API mode accepts Ollama and needs no key'
    # Back from API mode: -NoExternalLlm drops the API route (fleet row 24:
    # Windows had no way back short of uninstalling).
    $local = (@(Get-ODSPortalLinuxArguments @{ NoExternalLlm = $true }) -join ' ')
    Check ($local.Contains('--no-external-llm') -and -not $local.Contains('--external-llm-url')) '-NoExternalLlm asks the Linux installer to leave API mode'
    Check (-not ((@(Get-ODSPortalLinuxArguments @{}) -join ' ').Contains('--no-external-llm'))) 'a plain rerun does not leave API mode'
    foreach ($bad in @(
        @{ ExternalLlmUrl = 'https://api.example.test'; ExternalLlmModel = 'm'; NoExternalLlm = $true },
        @{ ExternalLlmUrl = 'https://api.example.test'; ExternalLlmModel = 'm'; Cloud = $true },
        @{ ExternalLlmModel = 'm' },
        @{ ExternalLlmUrl = 'ftp://api.example.test'; ExternalLlmModel = 'm' },
        @{ ExternalLlmUrl = 'https://api.example.test'; ExternalLlmModel = '' },
        @{ ExternalLlmUrl = 'https://api.example.test'; ExternalLlmModel = 'm'; ExternalLlmProvider = 'other' })) {
        $refused = $false
        try { $null = Get-ODSPortalLinuxArguments $bad } catch { $refused = $true }
        Check $refused ("API mode refuses " + (($bad.Keys | Sort-Object) -join '+'))
    }
    $keyDir = Join-Path ([IO.Path]::GetTempPath()) ('ods-api-key-' + [guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $keyDir
    try {
        $keyFile = Join-Path $keyDir 'api.key'
        Set-Content -LiteralPath $keyFile -Value "  sk-fixture-123  `r`n" -NoNewline
        Check ((Read-ODSPortalExternalLlmKey $keyFile) -ceq 'sk-fixture-123') 'the API key file is read as one trimmed line'
        Set-Content -LiteralPath $keyFile -Value "sk-one`r`nsk-two`r`n" -NoNewline
        $refused = $false
        try { $null = Read-ODSPortalExternalLlmKey $keyFile } catch { $refused = $_.Exception.Message -match 'exactly one API key' }
        Check $refused 'a key file with two keys is refused'
        $refused = $false
        try { $null = Read-ODSPortalExternalLlmKey (Join-Path $keyDir 'missing.key') } catch { $refused = $_.Exception.Message -match 'was not found' }
        Check $refused 'a missing key file is named'
    } finally { Remove-Item -LiteralPath $keyDir -Recurse -Force }
    Write-Host "Passed $script:checks Windows Portal setup contracts."
} finally { $env:OS=$originalOS }
