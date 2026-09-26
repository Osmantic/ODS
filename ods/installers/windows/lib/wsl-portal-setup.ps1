# Prerequisite orchestration; actual installation remains in install-core.sh.
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

function Invoke-ODSPortalLinuxInstaller([string]$InstallerRoot, [string]$Distro, [string[]]$LinuxArguments, [string]$InstallRoot) {
    # windows.ps1 runs in a child PowerShell that shares this console. Calling
    # it here would route wsl.exe output through this function's pipeline, so
    # the Linux installer would see no terminal: no progress during image
    # pulls, no cinematic UI and UTF-8 decoded with the OEM code page.
    $delegate = Join-Path $InstallerRoot 'windows.ps1'
    $passthrough = @($LinuxArguments | ForEach-Object { ConvertTo-ODSPortalLiteral $_ }) -join ', '
    $command = "`$global:LASTEXITCODE = 0; & $(ConvertTo-ODSPortalLiteral $delegate) -Distro $(ConvertTo-ODSPortalLiteral $Distro) -InstallRoot $(ConvertTo-ODSPortalLiteral $InstallRoot) -PassthroughArgs @($passthrough); " +
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
    $linuxArgs += @('--pixel', '--no-hermes', '--no-openclaw')
    return $linuxArgs
}

function Resolve-ODSPortalDistro([string]$Requested, [string[]]$Names) {
    if ($Requested) { return $Requested }
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

function Invoke-ODSPortalSetup([System.Collections.IDictionary]$Options, [string]$InstallerRoot) {
    $linuxArgs = @(Get-ODSPortalLinuxArguments $Options)
    $distro = if ($Options['Distro']) { [string]$Options['Distro'] } else { 'Ubuntu-24.04' }
    if ($distro -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$' -or $distro -match '^docker-desktop') { throw 'Select a named Ubuntu WSL distribution, for example -Distro Ubuntu-24.04.' }
    $nonInteractive = [bool]$Options['NonInteractive']
    Write-Host ''
    Write-Host '  O D S  /  PORTAL'
    Write-Host '  Windows -> Ubuntu / WSL2 -> Pixel'
    Write-Host '  Your workspace runs in Ubuntu. Open Portal from Windows.'
    if ($Options['DryRun']) {
        Write-Host 'Dry run: no features, distributions, tasks, services or files will be changed.'
        Write-Host 'Plan: verify WSL2, Ubuntu user, systemd, Docker integration and, with an NVIDIA driver, GPU access from Ubuntu and Docker; run the Linux installer; verify Pixel ingress and Portal HTTP readiness.'
        Write-Host ('Linux flags: ' + ($linuxArgs -join ' '))
        return 0
    }
    if ($env:OS -ne 'Windows_NT') { throw 'Run install.ps1 in Windows PowerShell. Inside Ubuntu use bash install.sh --pixel --no-hermes.' }
    if (Test-ODSPortalAdministrator) { throw 'Open a normal, non-Administrator PowerShell window and rerun this command. Only Windows feature preparation will request elevation.' }
    if (Test-ODSNativeWindowsInstall) { throw 'An existing native Windows ODS installation was found. It is not automatically migrated or deleted. Stop and migrate/remove that installation before creating a WSL stack, to avoid shared ports and Compose project conflicts. See ods/docs/WINDOWS-QUICKSTART.md.' }
    Write-ODSPortalStage 1 'WINDOWS FOUNDATION' 'Checking WSL availability and systemd support.'
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        if (-not (Confirm-ODSPortalPreparation 'WSL is unavailable. Enable Windows Subsystem for Linux and Virtual Machine Platform? Windows will request administrator permission. Save your work; restart Windows afterwards and rerun this command.' $nonInteractive)) { return 1 }
        $featureCode = Install-ODSPortalWslFeatures -MissingExecutable
        if ($featureCode -notin @(0, 3010)) { throw "Windows feature preparation failed (exit $featureCode). Check Windows Update and virtualization support. See https://learn.microsoft.com/windows/wsl/install-manual ." }
        Write-Host 'Windows features prepared. Restart Windows, then rerun this command to finish WSL/Ubuntu preparation. ODS has not been installed yet.'
        return 3010
    }
    $status = Invoke-ODSPortalWsl -Arguments @('--status')
    if ($status.Code -ne 0) {
        if (-not (Confirm-ODSPortalPreparation 'WSL is not ready. Prepare WSL with wsl --install --no-distribution? Windows will request administrator permission. Save your work; a restart may be required.' $nonInteractive)) { return 1 }
        $featureCode = Install-ODSPortalWslFeatures
        if ($featureCode -notin @(0, 3010)) { throw "WSL feature preparation failed (exit $featureCode). Run wsl --status to inspect the Windows error." }
        Write-Host 'Restart Windows if requested, then rerun the same install.ps1 command from a normal PowerShell window. ODS has not been installed yet.'
        return 3010
    }
    Assert-ODSPortalWslVersion
    Write-ODSPortalStage 2 'YOUR UBUNTU WORKSPACE' 'Finding an existing Ubuntu before offering a download.'
    $list = Invoke-ODSPortalWsl -Arguments @('--list', '--quiet')
    if ($list.Code -ne 0) { throw ('Cannot list WSL distributions: ' + $list.Output + ' ' + $list.Error) }
    $names = @($list.Output -split '\r?\n' | ForEach-Object { $_.Trim() })
    $distro = Resolve-ODSPortalDistro ([string]$Options['Distro']) $names
    Write-Host "Selected Ubuntu distribution: $distro"
    if ($distro -notin $names) {
        if (-not (Confirm-ODSPortalPreparation "Install $distro using wsl --install? This downloads Ubuntu under your Windows account; existing distributions will not be removed." $nonInteractive)) { return 1 }
        $installed = Invoke-ODSPortalWsl -Arguments @('--install', '--distribution', $distro, '--no-launch')
        if ($installed.Code -eq 3010) {
            Write-Host 'Windows requires a restart. Restart, then rerun the same command to finish Ubuntu setup.'
            return 3010
        }
        if ($installed.Code -ne 0) { throw ('Ubuntu installation did not complete: ' + $installed.Output + ' ' + $installed.Error) }
        if ((Initialize-ODSPortalUbuntuUser $distro) -ne 0) { throw "Finish $distro first-run setup, then rerun this command." }
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
    $init = Invoke-ODSPortalWsl -Arguments @('--distribution', $distro, '--exec', 'cat', '/proc/1/comm')
    if ($init.Code -ne 0 -or $init.Output.Trim() -ne 'systemd') {
        throw "Enable systemd=true under [boot] in /etc/wsl.conf inside Ubuntu (preserve other settings). Then run wsl --terminate $distro from PowerShell, reopen Ubuntu and rerun this command."
    }
    Write-ODSPortalStage 3 'CONTAINER CONNECTION' "Checking Docker and Compose inside $distro."
    foreach ($arguments in @(@('docker','info'), @('docker','compose','version'))) {
        $probe = Invoke-ODSPortalWsl -Arguments (@('--distribution', $distro, '--exec') + $arguments)
        if ($probe.Code -ne 0) {
            throw "Docker is not ready inside $distro. Install/start Docker Desktop, enable the WSL2 engine and Settings > Resources > WSL Integration for $distro. In Ubuntu, docker info AND docker compose version must succeed as your normal user. See https://docs.docker.com/desktop/features/wsl/ ."
        }
    }
    if (-not $Options['Cloud']) { Assert-ODSPortalNvidiaReady $distro (Get-ODSPortalWindowsNvidiaDriver) }
    Write-ODSPortalStage 4 'INSTALL PIXEL / PORTAL' "Prerequisites passed for $distro. Starting the Linux installer."
    Write-Host '         Enter your Ubuntu sudo password there if requested.'
    return Invoke-ODSPortalLinuxInstaller $InstallerRoot $distro $linuxArgs ([string]$Options['InstallDir'])
}
