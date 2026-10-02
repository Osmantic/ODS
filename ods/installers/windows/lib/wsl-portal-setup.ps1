# Prerequisite orchestration; actual installation remains in install-core.sh.
. (Join-Path $PSScriptRoot 'wsl-portal-prereqs.ps1')
. (Join-Path $PSScriptRoot 'portal-model-control.ps1')

function Write-ODSPortalStage([int]$Step, [string]$Title, [string]$Detail) {
    Write-Host ''
    $style = @{}
    if (-not $env:NO_COLOR -and $env:ODS_UI_MODE -ne 'plain') { $style.ForegroundColor = 'Cyan' }
    Write-Host ("  [{0}/4]  {1}" -f $Step, $Title) @style
    Write-Host "         $Detail"
}

function Test-ODSPortalAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-ODSNativeWindowsInstall {
    $roots = @($env:ODS_HOME, (Join-Path $env:USERPROFILE 'ods')) | Where-Object { $_ }
    foreach ($root in $roots) {
        if ((Test-Path -LiteralPath (Join-Path $root '.env')) -and (Test-Path -LiteralPath (Join-Path $root 'ods.ps1'))) { return $true }
    }
    return $false
}

function Invoke-ODSPortalWsl([string[]]$Arguments) {
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $records = & wsl.exe @Arguments 2>&1
        $code = $LASTEXITCODE
        # Output is stdout only: WSL and Linux tools print warnings on stderr
        # (localhost proxy, terminal size) that must not change parsed values.
        # Error keeps them for failure messages.
        $stdout = @($records | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] })
        $stderr = @($records | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } | ForEach-Object { $_.ToString() })
        [pscustomobject]@{
            Code = $code
            Output = (($stdout | Out-String) -replace "`0", '').Trim()
            Error = (($stderr | Out-String) -replace "`0", '').Trim()
        }
    } finally { $ErrorActionPreference = $previousPreference }
}

function Confirm-ODSPortalPreparation([string]$Message, [bool]$NonInteractive) {
    Write-Host $Message
    if ($NonInteractive) {
        Write-Host 'Prepare this prerequisite interactively, then rerun the same command. Non-interactive setup does not install prerequisites.'
        return $false
    }
    return (Read-Host 'Continue? [y/N]') -match '^(y|yes)$'
}

function Install-ODSPortalWslFeatures([switch]$MissingExecutable) {
    # Elevate only Windows features, never ODS or distro ownership. No auto reboot.
    if ($MissingExecutable) {
        $helper = Join-Path (Split-Path -Parent $PSScriptRoot) 'enable-wsl-features.ps1'
        $shell = Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell/v1.0/powershell.exe'
        $process = Start-Process -FilePath $shell -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $helper + '"')) -Verb RunAs -WindowStyle Hidden -Wait -PassThru
        return $process.ExitCode
    }
    $process = Start-Process -FilePath 'wsl.exe' -ArgumentList @('--install', '--no-distribution') -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    return $process.ExitCode
}

function Initialize-ODSPortalUbuntuUser([string]$Distro) {
    Write-Host 'An Ubuntu window will open. Finish first-run setup, create your Linux user/password, then type exit to return here. Do not use root as the default user.'
    $process = Start-Process -FilePath 'wsl.exe' -ArgumentList @('--distribution', $Distro) -Wait -PassThru
    return $process.ExitCode
}

function ConvertTo-ODSPortalLiteral([string]$Value) {
    return "'" + $Value.Replace("'", "''") + "'"
}

function Assert-ODSPortalStateRoot([string]$StateRoot) {
    if (-not $StateRoot) { return }
    if ($StateRoot -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$))' -or
        $StateRoot -match '[\x00-\x1f"]' -or $StateRoot -match '(^|[\\/])\.\.?([\\/]|$)' -or
        $StateRoot -match '^(?:[A-Za-z]:[\\/]*|\\\\[^\\/]+[\\/][^\\/]+[\\/]*)$') {
        throw '-StateRoot requires an absolute Windows directory, not a filesystem root or traversal path.'
    }
}

function Invoke-ODSPortalLinuxInstaller([string]$InstallerRoot, [string]$Distro, [string[]]$LinuxArguments, [string]$InstallRoot, [bool]$OpenPortal, [string]$DockerDesktopPath = '', [string]$StateRoot = '') {
    # windows.ps1 runs in a child PowerShell that shares this console. Calling
    # it here would route wsl.exe output through this function's pipeline, so
    # the Linux installer would see no terminal: no progress during image
    # pulls, no cinematic UI and UTF-8 decoded with the OEM code page.
    $delegate = Join-Path $InstallerRoot 'windows.ps1'
    $openFlag = if ($OpenPortal) { '$true' } else { '$false' }
    $passthrough = @($LinuxArguments | ForEach-Object { ConvertTo-ODSPortalLiteral $_ }) -join ', '
    $desktopArgument = if ($DockerDesktopPath) { ' -DockerDesktopPath ' + (ConvertTo-ODSPortalLiteral $DockerDesktopPath) } else { '' }
    $stateArgument = if ($StateRoot) { ' -StateRoot ' + (ConvertTo-ODSPortalLiteral $StateRoot) } else { '' }
    $command = "`$global:LASTEXITCODE = 0; & $(ConvertTo-ODSPortalLiteral $delegate) -Distro $(ConvertTo-ODSPortalLiteral $Distro) -InstallRoot $(ConvertTo-ODSPortalLiteral $InstallRoot) -OpenPortal:$openFlag$desktopArgument$stateArgument -PassthroughArgs @($passthrough); " +
        "`$ok = `$?; if (`$global:LASTEXITCODE -ne 0) { exit `$global:LASTEXITCODE }; if (-not `$ok) { exit 1 }; exit 0"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $shell = (Get-Process -Id $PID).Path
    $process = Start-Process -FilePath $shell -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-OutputFormat', 'Text', '-EncodedCommand', $encoded) -NoNewWindow -PassThru
    # Reading Handle keeps ExitCode available; WaitForExit waits for this
    # process only (Start-Process -Wait also waits for a browser it opened).
    $null = $process.Handle
    $process.WaitForExit()
    return $process.ExitCode
}

function Get-ODSPortalLinuxArguments([System.Collections.IDictionary]$Options) {
    if ($Options['Hermes'] -or $Options['OpenClaw']) {
        throw 'Portal setup requires Pixel. -Hermes and the deprecated -OpenClaw are not supported by this entry point.'
    }
    $linuxArgs = @()
    # --all must precede explicit disable overrides.
    $flags = [ordered]@{
        All='--all'; Force='--force'; NonInteractive='--non-interactive';
        Voice='--voice'; Workflows='--workflows'; Rag='--rag';
        Recommended='--recommended'; NoRecommended='--no-recommended'; Cloud='--cloud';
        Comfyui='--comfyui'; NoComfyui='--no-comfyui';
        Langfuse='--langfuse'; NoLangfuse='--no-langfuse'; NoBootstrap='--no-bootstrap'; Lan='--lan'
    }
    foreach ($key in $flags.Keys) { if ($Options[$key]) { $linuxArgs += $flags[$key] } }
    if ($Options['Tier']) {
        if ([string]$Options['Tier'] -notmatch '^[1-4]$') { throw '-Tier must be 1, 2, 3 or 4.' }
        $linuxArgs += @('--tier', [string]$Options['Tier'])
    }
    foreach ($key in @('InstallDir', 'SummaryJsonPath')) {
        $path = [string]$Options[$key]
        if ($path -and ($path -notmatch '^/[^\x00-\x1f]+$' -or $path -match '(^|/)\.\.?(/|$)' -or $path.Contains('//') -or $path -eq '/')) {
            throw "-$key must be an absolute Linux path inside Ubuntu, not a Windows drive path. Existing native Windows installations are not migrated."
        }
    }
    if ($Options['SummaryJsonPath']) { $linuxArgs += @('--summary-json', [string]$Options['SummaryJsonPath']) }
    # Every WSL installation needs a durable Windows executable location for
    # owner-scoped sign-in/uninstall control, including NVIDIA and CPU hosts.
    $linuxArgs += @('--windows-system-directory', [Environment]::SystemDirectory)
    $linuxArgs += @('--pixel', '--no-hermes', '--no-openclaw')
    return $linuxArgs
}

function Resolve-ODSPortalDistro([string]$Requested, [string[]]$Names) {
    if ($Requested) {
        # WSL accepts case-insensitive names, but ownership records must use
        # the exact registered spelling consistently across reruns.
        $registered = @($Names | Where-Object { $_ -ieq $Requested })
        if ($registered.Count -eq 1) { return $registered[0] }
        if ($registered.Count -gt 1) { throw 'The requested distribution name is ambiguous.' }
        return $Requested
    }
    # Reuse a single recognizable Ubuntu installation; never guess between
    # existing user environments or select Docker's internal distribution.
    # Versioned names outside Pixel's qualified releases (e.g. Ubuntu-22.04)
    # are never auto-selected; the unversioned name is release-checked later.
    $ubuntu = @($Names | Where-Object { $_ -match '^Ubuntu(?:-(?:24|26)\.04)?$' } | Select-Object -Unique)
    if ($ubuntu.Count -eq 1) { return $ubuntu[0] }
    if ($ubuntu.Count -gt 1) {
        throw ('Multiple Ubuntu distributions exist: ' + ($ubuntu -join ', ') + '. Rerun with -Distro <name> to choose where ODS belongs. No distribution was changed.')
    }
    return 'Ubuntu-24.04'
}

function Assert-ODSPortalDistroRelease([string]$Distro) {
    # Same qualification as ods_pixel_host_qualified in pixel-integration.sh.
    $release = Invoke-ODSPortalWsl -Arguments @('--distribution', $Distro, '--exec', 'cat', '/etc/os-release')
    if ($release.Code -ne 0) {
        throw "$Distro did not start (wsl exit $($release.Code)): $($release.Output) $($release.Error)".Trim()
    }
    $id = if ($release.Output -match '(?m)^ID="?([A-Za-z0-9._-]+)"?\s*$') { $Matches[1] } else { 'unknown' }
    $version = if ($release.Output -match '(?m)^VERSION_ID="?([A-Za-z0-9._-]+)"?\s*$') { $Matches[1] } else { 'unknown' }
    $qualified = ($id -eq 'ubuntu' -and $version -in @('24.04', '26.04')) -or ($id -eq 'debian' -and $version -eq '12')
    if (-not $qualified) {
        throw "$Distro runs $id $version, but Pixel requires Ubuntu 24.04/26.04 (or Debian 12). Rerun with -Distro Ubuntu-24.04 to install a separate Ubuntu 24.04; $Distro is not changed."
    }
}

function Assert-ODSPortalWslVersion {
    $version = Invoke-ODSPortalWsl -Arguments @('--version')
    # The first dotted version is WSL itself; labels are localized. Later
    # versions describe the kernel, WSLg and Windows and must not be used.
    $match = [regex]::Match($version.Output, '(?m)^.*?:\s*(\d+\.\d+\.\d+(?:\.\d+)?)\s*\r?$')
    if ($version.Code -ne 0 -or -not $match.Success -or [version]$match.Groups[1].Value -lt [version]'0.67.6') {
        throw 'Pixel requires WSL 0.67.6 or newer for systemd. Run wsl --update, restart WSL when your work is saved, and rerun setup. If --update is unavailable, install the current WSL release using https://learn.microsoft.com/windows/wsl/install .'
    }
}

function Get-ODSPortalWindowsNvidiaDriver {
    # Major version of the Windows NVIDIA driver, or $null when Windows has no
    # working NVIDIA driver (non-NVIDIA GPU, or a leftover nvidia-smi).
    $smi = Get-Command nvidia-smi.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $smi) { return $null }
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $raw = & $smi.Source --query-gpu=driver_version --format=csv,noheader 2>$null
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousPreference }
    $match = [regex]::Match((($raw | Out-String).Trim()), '^(\d+)\.')
    if ($code -ne 0 -or -not $match.Success) { return $null }
    return [int]$match.Groups[1].Value
}

function Assert-ODSPortalNvidiaReady([string]$Distro, $WindowsDriver) {
    # Mirrors what the Linux installer needs on WSL, so an unready GPU stops
    # here instead of after sudo/apt changes inside Ubuntu.
    if ($null -eq $WindowsDriver) { return }
    Write-Host "         NVIDIA driver $WindowsDriver detected on Windows; checking GPU access from $Distro."
    if ($WindowsDriver -lt 570) {
        throw "The Windows NVIDIA driver ($WindowsDriver) is older than 570, which the CUDA runtime requires. Update it with the NVIDIA App or from nvidia.com, run wsl --shutdown, then rerun this command. Do not install NVIDIA drivers inside Ubuntu."
    }
    $gpu = Invoke-ODSPortalWsl -Arguments @('--distribution', $Distro, '--exec', '/usr/lib/wsl/lib/nvidia-smi', '-L')
    if ($gpu.Code -ne 0 -or $gpu.Output -notmatch '(?m)^GPU \d+:') {
        throw "Windows has NVIDIA driver $WindowsDriver, but $Distro cannot see the GPU. Run wsl --update, then wsl --shutdown, reopen Ubuntu and check that nvidia-smi lists your GPU. Do not install NVIDIA drivers inside Ubuntu."
    }
    $runtimes = Invoke-ODSPortalWsl -Arguments @('--distribution', $Distro, '--exec', 'docker', 'info', '--format', '{{json .Runtimes}}')
    if ($runtimes.Code -ne 0 -or $runtimes.Output -notmatch '"nvidia"\s*:') {
        throw "Docker Desktop is not exposing its NVIDIA runtime to $Distro. Update Docker Desktop, keep 'Use the WSL 2 based engine' enabled, restart Docker Desktop, then rerun. ODS does not install a second NVIDIA container toolkit inside Ubuntu."
    }
}

$script:ODSPortalDockerConsent = 'Docker Desktop is required to run the ODS containers. Install it now with winget? This accepts the Docker Subscription Service Agreement (https://www.docker.com/legal/docker-subscription-service-agreement/); Docker Desktop is free for personal use, education, non-commercial open source and small businesses. Windows will ask for administrator permission.'

function Install-ODSPortalDockerBeforeRestart([bool]$NonInteractive) {
    # Docker Desktop also needs a restart after installing, so share the one
    # restart WSL already requires.
    if ((Get-ODSPortalDockerDesktop).Installed) { return }
    if (Confirm-ODSPortalPreparation $script:ODSPortalDockerConsent $NonInteractive) { Install-ODSPortalDockerDesktop }
}

function Initialize-ODSPortalWindowsFoundation([System.Collections.IDictionary]$Options, [string]$InstallerRoot, [bool]$NonInteractive) {
    # Returns $null when WSL is ready, otherwise the exit code to stop with.
    $present = [bool](Get-Command wsl.exe -ErrorAction SilentlyContinue)
    $ready = $present -and ((Invoke-ODSPortalWsl -Arguments @('--status')).Code -eq 0)
    Assert-ODSPortalHostCapacity $ready
    if ($ready) { return $null }
    if (-not $present) {
        if (-not (Confirm-ODSPortalPreparation 'WSL is not installed. Enable Windows Subsystem for Linux and Virtual Machine Platform? Windows will ask for administrator permission, then a restart is needed.' $NonInteractive)) { return 1 }
        $featureCode = Install-ODSPortalWslFeatures -MissingExecutable
        if ($featureCode -notin @(0, 3010)) { throw "Windows feature preparation failed (exit $featureCode). Check Windows Update and virtualization support. See https://learn.microsoft.com/windows/wsl/install-manual ." }
    } else {
        if (-not (Confirm-ODSPortalPreparation 'WSL is not ready. Prepare it with wsl --install --no-distribution? Windows will ask for administrator permission; a restart may be needed.' $NonInteractive)) { return 1 }
        $featureCode = Install-ODSPortalWslFeatures
        if ($featureCode -notin @(0, 3010)) { throw "WSL feature preparation failed (exit $featureCode). Run wsl --status to inspect the Windows error." }
    }
    Install-ODSPortalDockerBeforeRestart $NonInteractive
    return (Request-ODSPortalRestart $InstallerRoot $Options 'WSL was prepared and Windows needs a restart.')
}

function Get-ODSPortalDistroNames {
    $list = Invoke-ODSPortalWsl -Arguments @('--list', '--quiet')
    if ($list.Code -ne 0) {
        # WSL versions can report an empty registration as an error. Match
        # only its stable diagnostic code, never localized display text or
        # an unrelated service/access failure, so setup can offer Ubuntu.
        $diagnostic = $list.Output + ' ' + $list.Error
        if ($diagnostic -cmatch '(?<![A-Za-z0-9_])WSL_E_DEFAULT_DISTRO_NOT_FOUND(?![A-Za-z0-9_])') { return @() }
        throw ('Cannot list WSL distributions: ' + $diagnostic)
    }
    return @($list.Output -split '\r?\n' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Install-ODSPortalUbuntu([string]$Distro, [System.Collections.IDictionary]$Options, [string]$InstallerRoot, [bool]$NonInteractive) {
    # Returns $null once the distro exists with a default user, else an exit code.
    if (-not (Confirm-ODSPortalPreparation "Download and install ${Distro}? It is installed under your Windows account; existing distributions are not changed." $NonInteractive)) { return 1 }
    $installed = Invoke-ODSPortalWsl -Arguments @('--install', '--distribution', $Distro, '--no-launch')
    if ($installed.Code -eq 3010) { return (Request-ODSPortalRestart $InstallerRoot $Options 'Windows needs a restart to finish installing Ubuntu.') }
    if ($installed.Code -ne 0) { throw ('Ubuntu installation did not complete: ' + $installed.Output + ' ' + $installed.Error) }
    if ($Distro -notin (Get-ODSPortalDistroNames)) {
        # When Windows must restart to finish enabling WSL, wsl --install
        # prints that and exits 0 without downloading the distribution.
        if (-not (Get-ODSPortalDistroLauncher $Distro)) { return (Request-ODSPortalRestart $InstallerRoot $Options 'Windows needs a restart to finish enabling WSL before Ubuntu can be installed.') }
        Register-ODSPortalDistro $Distro
    }
    if ($Distro -notin (Get-ODSPortalDistroNames)) { throw "$Distro was downloaded but is not registered. Open it once from the Start menu, then rerun this command." }
    New-ODSPortalLinuxAccount $Distro (Read-ODSPortalLinuxAccount)
    return $null
}

function Initialize-ODSPortalDocker([string]$Distro, [System.Collections.IDictionary]$Options, [string]$InstallerRoot, [bool]$NonInteractive) {
    # Returns $null when Docker and Compose work inside $Distro, else an exit code.
    $desktop = Get-ODSPortalDockerDesktop
    if (-not $desktop.Installed) {
        if (-not (Confirm-ODSPortalPreparation $script:ODSPortalDockerConsent $NonInteractive)) { return 1 }
        Install-ODSPortalDockerDesktop
        return (Request-ODSPortalRestart $InstallerRoot $Options 'Docker Desktop was installed and needs a Windows restart before its first start.')
    }
    if (-not (Test-ODSPortalDockerEngine $desktop)) { Start-ODSPortalDockerDesktop $desktop }
    # Setup never edits Docker's settings or restarts Docker: its own
    # Apply & restart is the supported way to change WSL integration.
    if (-not (Wait-ODSPortalDistroDocker $Distro $script:ODSPortalIntegrationWaitSeconds)) {
        $steps = "In Docker Desktop, open Settings (gear icon) > Resources > WSL integration, turn on $Distro, then click Apply & restart."
        if ($NonInteractive) { throw "Docker Desktop is not connected to $Distro. $steps Then rerun this command." }
        Write-Host "         Docker Desktop is running but is not connected to $Distro yet."
        Write-Host "         $steps"
        Write-Host "         Setup continues by itself as soon as Docker answers inside Ubuntu (up to $($script:ODSPortalDockerWaitSeconds / 60) minutes)."
        Start-Process -FilePath $desktop.Exe
        if (-not (Wait-ODSPortalDistroDocker $Distro $script:ODSPortalDockerWaitSeconds)) { throw "Docker Desktop is still not connected to $Distro. $steps Then rerun this command." }
    }
    foreach ($arguments in @(@('docker','info'), @('docker','compose','version'))) {
        $probe = Invoke-ODSPortalWsl -Arguments (@('--distribution', $Distro, '--exec') + $arguments)
        if ($probe.Code -ne 0) {
            throw "Docker is not ready inside $Distro. Open Docker Desktop > Settings > Resources > WSL Integration, turn on $Distro and click Apply & restart. In Ubuntu, docker info AND docker compose version must succeed as your normal user. See https://docs.docker.com/desktop/features/wsl/ ."
        }
    }
    return $null
}

function Get-ODSPortalWslCommittedModel([string]$Distro, [string]$InstallDir) {
    # Read only the last completed protected model choice. The journal belongs
    # to the Linux installation user (0600); never source .env or copy a secret
    # back into the Windows process. A missing journal is a normal first install.
    $reader = @'
import json, os, re, stat, sys
install = sys.argv[1]
path = os.path.join(install, 'data', 'pixel-model-transaction.json')
owner = os.geteuid()
if owner == 0:
    raise SystemExit('protected model journal requires the installation user')
try:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
except FileNotFoundError:
    print(json.dumps({'status': 'missing'}))
    raise SystemExit(0)
with os.fdopen(fd, encoding='utf-8') as source:
    info = os.fstat(source.fileno())
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != owner
            or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 65536):
        raise SystemExit('unsafe protected model journal')
    value = json.load(source)
if (not isinstance(value, dict) or value.get('schemaVersion') != 1
        or value.get('phase') != 'completed'
        or value.get('outcome') not in ('commit', 'rollback')
        or not re.fullmatch(r'[a-f0-9]{64}', str(value.get('transactionId') or ''))
        or not isinstance(value.get('after'), dict)):
    raise SystemExit('protected model transaction is not complete')
contract = value.get('target') if value['outcome'] == 'commit' else value.get('previous')
if not isinstance(contract, dict):
    raise SystemExit('protected model contract is missing')
model = contract.get('model')
context = contract.get('contextLength')
digest = value['after'].get('windows-runtime-plan')
if (not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}', model)
        or type(context) is not int or not 4096 <= context <= 10000000
        or not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest)):
    raise SystemExit('protected model evidence is incomplete')
print(json.dumps({'status': 'completed', 'modelId': model,
                  'contextLength': context, 'windowsPlanDigest': digest}))
'@
    $result = Invoke-ODSPortalWsl -Arguments @('--distribution', $Distro,
        '--exec', 'python3', '-c', $reader, $InstallDir)
    if ($result.Code -ne 0) {
        throw 'The protected WSL model choice could not be verified; the Windows model was not changed.'
    }
    try { $value = $result.Output | ConvertFrom-Json -ErrorAction Stop }
    catch { throw 'The protected WSL model choice returned an invalid result; the Windows model was not changed.' }
    if ($value.status -notin @('missing', 'completed')) {
        throw 'The protected WSL model choice is invalid; the Windows model was not changed.'
    }
    return $value
}

function Get-ODSPortalRetainedLemonadeArguments([string]$Distro, [string]$InstallDir) {
    $runtimeDir = Join-Path (Get-ODSPortalStateDir) 'portal-runtime'
    $planPath = Join-Path $runtimeDir 'runtime.json'
    $hasPlan = Test-Path -LiteralPath $planPath
    $task = Get-ODSPortalLemonadeTask
    if (-not $hasPlan -and $null -eq $task) { return $null }
    if (-not $hasPlan -or $null -eq $task) {
        throw 'The existing Windows model task and private plan are incomplete; the model was not changed.'
    }
    $request = @{ distro = $Distro; installDir = $InstallDir }
    $managed = Get-ODSPortalManagedConfiguration $request
    Assert-ODSPortalControlModel $managed.Plan
    $observed = Get-ODSPortalManagedObservation $managed
    $runtimeModel = Resolve-ODSLemonadeModelId -Port $managed.Plan.Port -GgufFile $managed.Plan.GgufFile
    if ($runtimeModel -cne $observed.modelId -or $observed.contextLength -ne $managed.Plan.ContextSize) {
        throw 'The existing Windows model does not match its private plan; the model was not changed.'
    }
    $committed = Get-ODSPortalWslCommittedModel $Distro $InstallDir
    if ($committed.status -eq 'completed' -and
        ($committed.modelId -cne $observed.modelId -or
         $committed.contextLength -ne $observed.contextLength -or
         $committed.windowsPlanDigest -cne $managed.PlanDigest)) {
        throw 'The Windows model differs from the last protected Pixel model choice; the model was not changed. Resolve the protected model state through Dashboard Models or supported ODS recovery before retrying.'
    }
    if ((Get-ODSPortalPlanDigest $managed.PlanPath) -cne $managed.PlanDigest) {
        throw 'The Windows model plan changed during verification; the model was not changed.'
    }
    $finalObservation = Get-ODSPortalManagedObservation $managed
    if ($finalObservation.modelId -cne $observed.modelId -or
        $finalObservation.contextLength -ne $observed.contextLength) {
        throw 'The Windows model changed during verification; the model was not changed.'
    }
    Assert-ODSPortalLemonadeVersion (Get-ODSLemonadeExecutableVersion $managed.Plan.ExecutablePath)
    Write-Host "         Reusing the verified Windows model: $($observed.modelId) ($($observed.contextLength) tokens)."
    return @('--lemonade-url', "http://localhost:$($managed.Plan.Port)",
        '--lemonade-host-transport', 'model-router', '--lemonade-model', [string]$observed.modelId)
}

function Add-ODSPortalAmdArguments([string[]]$LinuxArgs, [System.Collections.IDictionary]$Options, [string]$SourceRoot, [bool]$NonInteractive, [string]$WslDistro = '', $AmdPlan = $null) {
    # An AMD GPU runs the model through Lemonade Server on Windows; everything
    # else keeps the in-WSL route. An explicit -Tier stays the user's choice.
    $plan = if ($null -ne $AmdPlan) { $AmdPlan } else { Get-ODSPortalAmdPlan $SourceRoot }
    if ($null -eq $plan) { return $LinuxArgs }
    $wslInstallDir = [string]$Options['InstallDir']
    if ($WslDistro) {
        if (-not $wslInstallDir) {
            $linuxHome = Invoke-ODSPortalWsl -Arguments @('--distribution', $WslDistro, '--exec', 'printenv', 'HOME')
            if ($linuxHome.Code -ne 0 -or $linuxHome.Output -notmatch '^/[^\x00-\x1f\x7f]+$') {
                throw 'Could not resolve the normal WSL user home for the managed Windows model binding.'
            }
            $wslInstallDir = $linuxHome.Output.TrimEnd('/') + '/ods'
        }
        Assert-ODSPortalWslBinding $WslDistro $wslInstallDir
        # Pin the same directory into windows.ps1 and the Linux installer.
        $Options['InstallDir'] = $wslInstallDir
    }
    $retainedArgs = @(Get-ODSPortalRetainedLemonadeArguments $WslDistro $wslInstallDir |
        Where-Object { $null -ne $_ })
    if ($retainedArgs.Count -gt 0) {
        if ($Options['Tier']) {
            throw 'A retained Windows model is already selected. -Tier cannot change that model during setup; choose the model in Dashboard Models, then rerun without -Tier.'
        }
        Write-ODSPortalStage 3 'AMD GPU' "$($plan.GpuName): preserving the verified Windows model selected in Dashboard."
        $retainedArgs += @('--lemonade-gpu-name', $plan.GpuName,
            '--lemonade-gpu-vram-mb', [string]$plan.VramMB)
        if (-not $Options['Tier']) { $retainedArgs += @('--tier', $plan.LinuxTier) }
        return @($LinuxArgs + $retainedArgs)
    }
    Write-ODSPortalStage 3 'AMD GPU' "$($plan.GpuName) ($([math]::Round($plan.VramMB / 1024)) GB): model $($plan.Model) runs through Lemonade Server on Windows."
    $amdArgs = @(Initialize-ODSPortalAmdLemonade $plan $SourceRoot $NonInteractive $WslDistro $wslInstallDir)
    if ($amdArgs.Count -gt 0 -and -not $Options['Tier']) { $amdArgs += @('--tier', $plan.LinuxTier) }
    return @($LinuxArgs + $amdArgs)
}

function Get-ODSPortalInitProcess([string]$Distro) {
    $probe = Invoke-ODSPortalWsl -Arguments @('--distribution', $Distro, '--exec', 'cat', '/proc/1/comm')
    if ($probe.Code -ne 0) {
        throw ("Cannot read PID 1 in $Distro (wsl exit $($probe.Code)); systemd state is unknown. Resolve the WSL error and rerun setup: $($probe.Output) $($probe.Error)").Trim()
    }
    $name = ([string]$probe.Output).Trim()
    # /proc/1/comm is one process name, limited to 15 bytes by Linux. Empty or
    # multi-line output is not evidence that systemd is disabled.
    if ($name -notmatch '^[^\x00-\x1f\x7f]{1,15}$') {
        throw "Cannot read a valid PID 1 process name in $Distro; systemd state is unknown. Check WSL/distro availability and rerun setup."
    }
    return $name
}

function Invoke-ODSPortalSetup([System.Collections.IDictionary]$Options, [string]$InstallerRoot) {
    $linuxArgs = @(Get-ODSPortalLinuxArguments $Options)
    Assert-ODSPortalStateRoot ([string]$Options['StateRoot'])
    $distro = if ($Options['Distro']) { [string]$Options['Distro'] } else { 'Ubuntu-24.04' }
    if ($distro -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$' -or $distro -match '^docker-desktop') { throw 'Select a named Ubuntu WSL distribution, for example -Distro Ubuntu-24.04.' }
    $nonInteractive = [bool]$Options['NonInteractive']
    Write-Host ''
    Write-Host '  O D S  /  PORTAL'
    Write-Host '  Windows -> Ubuntu / WSL2 -> Pixel'
    Write-Host '  Your workspace runs in Ubuntu. Open Portal from Windows.'
    if ($Options['DryRun']) {
        Write-Host 'Dry run: no features, distributions, tasks, services or files will be changed.'
        Write-Host 'Plan: check disk space and virtualization; prepare WSL2, Ubuntu and Docker Desktop when missing (continuing after a restart); verify the Ubuntu user, systemd, Docker integration and, with an NVIDIA driver, GPU access; with an AMD GPU, install Lemonade Server on Windows and load the model on the GPU; run the Linux installer; verify Pixel ingress and Portal readiness; open Portal.'
        Write-Host ('Linux flags: ' + ($linuxArgs -join ' '))
        return 0
    }
    if ($env:OS -ne 'Windows_NT') { throw 'Run install.ps1 in Windows PowerShell. Inside Ubuntu use bash install.sh --pixel --no-hermes.' }
    if (Test-ODSPortalAdministrator) { throw 'This window is running as Administrator. Setup requires a non-elevated user session. Open PowerShell without "Run as administrator" and rerun. If every window is elevated because UAC is disabled or this is the built-in Administrator account, use a standard Windows account or enable UAC and sign in again. UAC-disabled elevated sessions are not supported. Setup requests elevation separately only for Windows prerequisites.' }
    if (Test-ODSNativeWindowsInstall) { throw 'An existing native Windows ODS installation was found. It is not automatically migrated or deleted. Stop and migrate/remove that installation before creating a WSL stack, to avoid shared ports and Compose project conflicts. See ods/docs/WINDOWS-QUICKSTART.md.' }
    Write-ODSPortalStage 1 'WINDOWS FOUNDATION' 'Checking disk space, virtualization and WSL.'
    $stop = Initialize-ODSPortalWindowsFoundation $Options $InstallerRoot $nonInteractive
    if ($null -ne $stop) { return $stop }
    Assert-ODSPortalWslVersion
    Write-ODSPortalStage 2 'YOUR UBUNTU WORKSPACE' 'Finding an existing Ubuntu before offering a download.'
    $names = Get-ODSPortalDistroNames
    $distro = Resolve-ODSPortalDistro ([string]$Options['Distro']) $names
    Write-Host "Selected Ubuntu distribution: $distro"
    if ($distro -notin $names) {
        $stop = Install-ODSPortalUbuntu $distro $Options $InstallerRoot $nonInteractive
        if ($null -ne $stop) { return $stop }
        $distro = Resolve-ODSPortalDistro $distro (Get-ODSPortalDistroNames)
    }
    $version = Invoke-ODSPortalWsl -Arguments @('--list', '--verbose')
    $versionPattern = '(?m)^\s*\*?\s*' + [regex]::Escape($distro) + '\s+.+\s+2\s*$'
    if ($version.Code -ne 0 -or $version.Output -notmatch $versionPattern) {
        throw "The selected distribution must use WSL2. Run wsl --set-version $distro 2, wait for conversion, then rerun this command."
    }
    Assert-ODSPortalDistroRelease $distro
    $identity = Invoke-ODSPortalWsl -Arguments @('--distribution', $distro, '--exec', 'id', '-u')
    if ($identity.Code -eq 0 -and $identity.Output -eq '0' -and -not $nonInteractive) {
        if (Confirm-ODSPortalPreparation "Ubuntu is present, but $distro still opens as root. Open its interactive setup to finish creating/selecting your normal Linux user? Exit Ubuntu after completing setup; ODS will recheck the default user." $false) {
            if ((Initialize-ODSPortalUbuntuUser $distro) -ne 0) { throw "Ubuntu user setup did not finish. Open $distro and complete it before rerunning ODS." }
            $identity = Invoke-ODSPortalWsl -Arguments @('--distribution', $distro, '--exec', 'id', '-u')
        }
    }
    if ($identity.Code -ne 0 -or $identity.Output -notmatch '^\d+$' -or $identity.Output -eq '0') {
        throw "Initialize a normal Linux user and make it the default in $distro. Open Ubuntu to finish account setup, then rerun this command; do not install ODS as root."
    }
    $init = Get-ODSPortalInitProcess $distro
    if ($init -cne 'systemd' -and
        (Confirm-ODSPortalPreparation "Pixel needs systemd, which is off in $distro. Turn it on now? This restarts $distro, so save work in any open Ubuntu window first." $nonInteractive)) {
        Enable-ODSPortalSystemd $distro
        $init = Get-ODSPortalInitProcess $distro
    }
    if ($init -cne 'systemd') {
        throw "Enable systemd=true under [boot] in /etc/wsl.conf inside Ubuntu (preserve other settings). Then run wsl --terminate $distro from PowerShell, reopen Ubuntu and rerun this command."
    }
    Write-ODSPortalStage 3 'CONTAINER CONNECTION' "Checking Docker Desktop and Compose inside $distro."
    $stop = Initialize-ODSPortalDocker $distro $Options $InstallerRoot $nonInteractive
    if ($null -ne $stop) { return $stop }
    $amdPlan = $null
    $modelMutex = $null
    $modelLockHeld = $false
    $preflightModelArgs = @()
    $preflightPlanDigest = $null
    try {
        if (-not $Options['Cloud']) {
            $nvidiaDriver = Get-ODSPortalWindowsNvidiaDriver
            Assert-ODSPortalNvidiaReady $distro $nvidiaDriver
            if ($null -eq $nvidiaDriver) {
                $amdPlan = Get-ODSPortalAmdPlan (Split-Path -Parent $InstallerRoot)
                if ($null -ne $amdPlan) {
                    # Dashboard model switches use this same per-user mutex.
                    # Keep it through the Linux install so a second choice
                    # cannot replace the route between verification and Pixel.
                    $modelMutex = [Threading.Mutex]::new($false, (Get-ODSPortalControlMutexName))
                    try { $modelLockHeld = $modelMutex.WaitOne(0) }
                    catch [Threading.AbandonedMutexException] { $modelLockHeld = $true }
                    if (-not $modelLockHeld) { throw 'A Dashboard model change is running. Wait for it to finish, then rerun ODS setup.' }
                    $linuxArgs = @(Add-ODSPortalAmdArguments $linuxArgs $Options (Split-Path -Parent $InstallerRoot) $nonInteractive $distro $amdPlan)
                    if ($linuxArgs -contains '--lemonade-url') {
                        $preflightModelArgs = @(Get-ODSPortalRetainedLemonadeArguments $distro ([string]$Options['InstallDir']) |
                            Where-Object { $null -ne $_ })
                        if ($preflightModelArgs.Count -eq 0) { throw 'The Windows model route could not be verified before Linux setup.' }
                        $preflightPlanDigest = (Get-ODSPortalManagedConfiguration @{distro=$distro;installDir=[string]$Options['InstallDir']}).PlanDigest
                    }
                }
            }
        }
        Write-ODSPortalStage 4 'INSTALL PIXEL / PORTAL' "Prerequisites passed for $distro. Starting the Linux installer."
        Write-Host '         When Ubuntu asks for your [sudo] password, type your Ubuntu password and press Enter. Nothing appears while you type.'
        $exitCode = Invoke-ODSPortalLinuxInstaller $InstallerRoot $distro $linuxArgs ([string]$Options['InstallDir']) (-not $nonInteractive) (Get-ODSPortalDockerDesktop).Exe ([string]$Options['StateRoot'])
        if ($exitCode -eq 0 -and $null -ne $amdPlan -and $linuxArgs -contains '--lemonade-url') {
            $after = @(Get-ODSPortalRetainedLemonadeArguments $distro ([string]$Options['InstallDir']) |
                Where-Object { $null -ne $_ })
            if ($after.Count -eq 0) { throw 'The Windows model could not be verified after Linux setup; setup cannot report success.' }
            $postflightPlanDigest = (Get-ODSPortalManagedConfiguration @{distro=$distro;installDir=[string]$Options['InstallDir']}).PlanDigest
            $routeChanged = $after.Count -ne $preflightModelArgs.Count
            if (-not $routeChanged) {
                for ($i = 0; $i -lt $after.Count; $i++) {
                    if ([string]$after[$i] -cne [string]$preflightModelArgs[$i]) { $routeChanged = $true; break }
                }
            }
            if ($routeChanged -or $postflightPlanDigest -cne $preflightPlanDigest) {
                throw 'The Windows model route changed during Linux setup; setup cannot report success. Verify Dashboard Models and the protected runtime before retrying.'
            }
        }
        return $exitCode
    } finally {
        if ($modelLockHeld) { $modelMutex.ReleaseMutex() }
        if ($null -ne $modelMutex) { $modelMutex.Dispose() }
    }
}
