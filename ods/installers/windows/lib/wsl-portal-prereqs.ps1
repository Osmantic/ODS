# Windows-side prerequisites for the Portal setup: host capacity, Docker
# Desktop, Docker's WSL integration, the first Ubuntu account and resuming
# after a restart. Each step asks first, changes only its own prerequisite and
# stops with instructions when it cannot finish. ODS itself is still installed
# only by install-core.sh inside Ubuntu.

$script:ODSPortalMinimumFreeGB = 40
$script:ODSPortalDockerWaitSeconds = 240
$script:ODSPortalResumeValue = 'ODSPortalSetup'

# ---------------------------------------------------------------- pure helpers

function Test-ODSPortalLinuxUsername([string]$Name) {
    # Debian adduser's default NAME_REGEX, minus names that would shadow
    # system accounts the installer relies on.
    return ($Name -cmatch '^[a-z][a-z0-9_-]{0,31}$') -and ($Name -notin @('root', 'docker', 'ods', 'ods-pixel', 'nobody', 'daemon', 'sys', 'bin', 'admin', 'sudo', 'users'))
}

function Get-ODSPortalDistroLauncherName([string]$Distro) {
    # Store-packaged Ubuntu launchers: Ubuntu -> ubuntu.exe, Ubuntu-24.04 -> ubuntu2404.exe.
    if ($Distro -notmatch '^Ubuntu(?:-(\d{2})\.(\d{2}))?$') { return $null }
    if ($Matches[1]) { return ('ubuntu' + $Matches[1] + $Matches[2] + '.exe') }
    return 'ubuntu.exe'
}

function ConvertTo-ODSPortalPowerShellLiteral([string]$Value) {
    return "'" + $Value.Replace("'", "''") + "'"
}

function New-ODSPortalResumeScript([string]$EntryScript, [System.Collections.IDictionary]$Options) {
    # Re-invokes the same entry point with the same validated options.
    $arguments = @()
    foreach ($key in @($Options.Keys | Sort-Object)) {
        if ($key -notmatch '^[A-Za-z]+$' -or $key -eq 'DryRun') { continue }
        $value = $Options[$key]
        if ($value -is [System.Management.Automation.SwitchParameter] -or $value -is [bool]) {
            if ([bool]$value) { $arguments += "-$key" }
        } elseif ($null -ne $value -and [string]$value -ne '') {
            $arguments += "-$key " + (ConvertTo-ODSPortalPowerShellLiteral ([string]$value))
        }
    }
    $entry = ConvertTo-ODSPortalPowerShellLiteral $EntryScript
    $folder = ConvertTo-ODSPortalPowerShellLiteral (Split-Path -Parent $EntryScript)
    return @(
        '# Written by the ODS Windows setup to continue after a restart. Safe to delete.',
        "`$host.UI.RawUI.WindowTitle = 'ODS setup (continuing after restart)'",
        "if (-not (Test-Path -LiteralPath $entry)) {",
        "    Write-Host 'The downloaded ODS setup files are no longer available. Paste the install command from https://github.com/Osmantic/ODS again.' -ForegroundColor Yellow",
        '    return',
        '}',
        "Set-Location -LiteralPath $folder",
        "Write-Host 'Continuing ODS setup after the restart...' -ForegroundColor Cyan",
        ("& $entry " + ($arguments -join ' ')).TrimEnd()
    ) -join "`r`n"
}

function Update-ODSPortalDockerSettings([string]$Json, [string]$FileName, [string]$Distro) {
    # Adds $Distro to Docker Desktop's per-distro WSL integration list.
    # settings-store.json (current Docker Desktop) uses PascalCase keys and the
    # older settings.json uses camelCase; an existing key's spelling wins.
    # Returns $null when the WSL engine is off, which integration cannot fix.
    $settings = $Json | ConvertFrom-Json
    if ($null -eq $settings -or $settings -isnot [pscustomobject]) { throw "Docker Desktop settings in $FileName are not a JSON object." }
    $names = @($settings.PSObject.Properties.Name)
    $pascal = $FileName -eq 'settings-store.json'
    foreach ($engineKey in @('WslEngineEnabled', 'wslEngineEnabled')) {
        if ($names -ccontains $engineKey -and $settings.$engineKey -eq $false) { return $null }
    }
    $listKey = if ($names -ccontains 'IntegratedWslDistros') { 'IntegratedWslDistros' } elseif ($names -ccontains 'integratedWslDistros') { 'integratedWslDistros' } elseif ($pascal) { 'IntegratedWslDistros' } else { 'integratedWslDistros' }
    $current = @()
    if ($names -ccontains $listKey -and $null -ne $settings.$listKey) { $current = @($settings.$listKey | ForEach-Object { [string]$_ }) }
    if ($current -contains $Distro) { return $Json }
    $updated = [string[]]@($current + $Distro)
    if ($names -ccontains $listKey) { $settings.$listKey = $updated } else { $settings | Add-Member -NotePropertyName $listKey -NotePropertyValue $updated }
    return ($settings | ConvertTo-Json -Depth 64)
}

# ----------------------------------------------------------- host inspection

function Test-ODSPortalVirtualization {
    # A running hypervisor (Hyper-V/VMP already on) hides the firmware flag,
    # so either signal is enough.
    $system = Get-CimInstance -ClassName Win32_ComputerSystem
    if ($system.HypervisorPresent) { return $true }
    $processors = @(Get-CimInstance -ClassName Win32_Processor)
    return [bool](@($processors | Where-Object { $_.VirtualizationFirmwareEnabled }).Count -gt 0)
}

function Get-ODSPortalFreeSystemGB {
    # WSL disks and Docker Desktop data live under the user's profile drive.
    $root = [IO.Path]::GetPathRoot($env:LOCALAPPDATA)
    return [math]::Floor(([IO.DriveInfo]::new($root)).AvailableFreeSpace / 1GB)
}

function Assert-ODSPortalHostCapacity([bool]$WslReady) {
    $free = Get-ODSPortalFreeSystemGB
    if ($free -lt $script:ODSPortalMinimumFreeGB) {
        throw "Only $free GB is free on the Windows drive that stores Ubuntu and Docker data. Free at least $($script:ODSPortalMinimumFreeGB) GB (Ubuntu, Docker images and the AI model), then rerun this command. Nothing was installed."
    }
    if (-not $WslReady -and -not (Test-ODSPortalVirtualization)) {
        throw 'Hardware virtualization is turned off, and WSL2 cannot run without it. Restart into your BIOS/UEFI settings, enable Intel VT-x (Intel Virtualization Technology) or AMD SVM, save, start Windows and rerun this command. Your PC maker''s support site names the exact menu.'
    }
}

# --------------------------------------------------------------- Docker Desktop

function Get-ODSPortalDockerDesktop {
    $root = Join-Path $env:ProgramFiles 'Docker\Docker'
    $exe = Join-Path $root 'Docker Desktop.exe'
    $cli = Join-Path $root 'resources\bin\docker.exe'
    return [pscustomobject]@{ Installed = (Test-Path -LiteralPath $exe); Exe = $exe; Cli = $cli }
}

function Invoke-ODSPortalDockerCli([string]$Cli, [string[]]$Arguments) {
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & $Cli @Arguments 2>&1
        return [pscustomobject]@{ Code = $LASTEXITCODE; Output = (($output | Out-String).Trim()) }
    } finally { $ErrorActionPreference = $previousPreference }
}

function Test-ODSPortalDockerEngine($Desktop) {
    if (-not (Test-Path -LiteralPath $Desktop.Cli)) { return $false }
    return (Invoke-ODSPortalDockerCli $Desktop.Cli @('version', '--format', '{{.Server.Version}}')).Code -eq 0
}

function Install-ODSPortalDockerDesktop {
    $winget = Get-Command winget.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $winget) {
        throw 'Docker Desktop is required, and this Windows has no winget to install it automatically. Install Docker Desktop from https://docs.docker.com/desktop/setup/install/windows-install/ (keep "Use WSL 2" selected), restart Windows, then rerun this command.'
    }
    # Docker's documented installer flags: quiet, WSL 2 backend, license accepted
    # (the user agreed in the prompt). winget asks Windows for elevation.
    $arguments = @('install', '--id', 'Docker.DockerDesktop', '--exact', '--source', 'winget',
        '--accept-package-agreements', '--accept-source-agreements',
        '--override', 'install --quiet --accept-license --backend=wsl-2')
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $winget.Source @arguments | Out-Host
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousPreference }
    if ($code -ne 0 -or -not (Get-ODSPortalDockerDesktop).Installed) {
        throw "Docker Desktop installation did not finish (winget exit $code). Install it from https://docs.docker.com/desktop/setup/install/windows-install/ , restart Windows, then rerun this command."
    }
}

function Wait-ODSPortalDockerEngine($Desktop) {
    # Bounded readiness wait for a service that was just started; not a retry.
    $deadline = [DateTime]::UtcNow.AddSeconds($script:ODSPortalDockerWaitSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-ODSPortalDockerEngine $Desktop) { return }
        Start-Sleep -Seconds 5
    }
    throw "Docker Desktop did not finish starting within $($script:ODSPortalDockerWaitSeconds / 60) minutes. Open Docker Desktop, accept any prompt it shows, wait until it says Engine running, then rerun this command."
}

function Start-ODSPortalDockerDesktop($Desktop) {
    Write-Host '         Starting Docker Desktop (the first start can take a few minutes)...'
    Start-Process -FilePath $Desktop.Exe | Out-Null
    Wait-ODSPortalDockerEngine $Desktop
}

function Enable-ODSPortalDockerWslIntegration($Desktop, [string]$Distro) {
    $folder = Join-Path $env:APPDATA 'Docker'
    $file = @('settings-store.json', 'settings.json') | ForEach-Object { Join-Path $folder $_ } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    $manual = "Open Docker Desktop > Settings > Resources > WSL Integration, turn on $Distro, click Apply & restart, then rerun this command."
    $hyperV = "Docker Desktop is using Hyper-V instead of WSL 2. In Docker Desktop > Settings > General, enable 'Use the WSL 2 based engine', apply, then rerun this command."
    if (-not $file) { throw "Docker Desktop has not saved its settings yet. $manual" }
    # Checked before stopping Docker so a Hyper-V setup is left running.
    if ($null -eq (Update-ODSPortalDockerSettings ([IO.File]::ReadAllText($file)) (Split-Path -Leaf $file) $Distro)) { throw $hyperV }
    # Docker Desktop writes its in-memory settings back when it stops, so the
    # file is edited only while it is stopped.
    Write-Host '         Restarting Docker Desktop with WSL integration for your Ubuntu...'
    $stop = Invoke-ODSPortalDockerCli $Desktop.Cli @('desktop', 'stop')
    if ($stop.Code -ne 0) { throw "Docker Desktop could not be stopped automatically. $manual" }
    $current = [IO.File]::ReadAllText($file)
    $updated = Update-ODSPortalDockerSettings $current (Split-Path -Leaf $file) $Distro
    if ($null -eq $updated) { throw $hyperV }
    if ($updated -ne $current) { [IO.File]::WriteAllText($file, $updated, [Text.UTF8Encoding]::new($false)) }
    Start-ODSPortalDockerDesktop $Desktop
}

# ------------------------------------------------------ Ubuntu first account

function Read-ODSPortalLinuxAccount {
    Write-Host ''
    Write-Host '         Create the Linux account for your Ubuntu workspace.'
    Write-Host '         Use lowercase letters, numbers, - or _ (for example: maria). This is separate from your Windows login.'
    do {
        $name = (Read-Host '         Ubuntu username').Trim()
        $valid = Test-ODSPortalLinuxUsername $name
        if (-not $valid) { Write-Host '         Start with a lowercase letter; use only a-z, 0-9, - or _ (max 32 characters).' -ForegroundColor Yellow }
    } until ($valid)
    Write-Host '         Choose a password. Nothing appears on screen while you type; that is normal.'
    do {
        $first = Read-Host '         Ubuntu password' -AsSecureString
        $second = Read-Host '         Type the password again' -AsSecureString
        $a = ConvertFrom-ODSPortalSecureString $first
        $b = ConvertFrom-ODSPortalSecureString $second
        $valid = $a.Length -ge 1 -and $a -ceq $b
        if (-not $valid) { Write-Host '         The passwords were empty or did not match. Try again.' -ForegroundColor Yellow }
    } until ($valid)
    Write-Host '         Remember this password: Ubuntu asks for it once during installation.'
    return [pscustomobject]@{ Name = $name; Password = $a }
}

function ConvertFrom-ODSPortalSecureString([Security.SecureString]$Value) {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

function Register-ODSPortalDistro([string]$Distro) {
    # Store-packaged releases installed with --no-launch stay unregistered
    # until their launcher runs; "install --root" registers without the
    # interactive account wizard, which this setup replaces.
    $name = Get-ODSPortalDistroLauncherName $Distro
    $launcher = if ($name -and $env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA ('Microsoft\WindowsApps\' + $name) } else { $null }
    if (-not $launcher -or -not (Test-Path -LiteralPath $launcher)) {
        throw "$Distro was downloaded but Windows has not registered it yet. Open $Distro once from the Start menu, finish its setup, close it, then rerun this command."
    }
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $launcher install --root | Out-Host
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousPreference }
    if ($code -ne 0) { throw "$Distro could not finish its first start (exit $code). Open $Distro once from the Start menu, then rerun this command." }
}

function Invoke-ODSPortalWslInput([string]$Distro, [string[]]$Command, [string]$Text) {
    # Runs a fixed root command in the distro with $Text on stdin. Bytes are
    # written directly: Windows PowerShell pipes add CRLF and use the console
    # code page, which would change passwords.
    $info = [Diagnostics.ProcessStartInfo]::new((Get-Command wsl.exe -CommandType Application | Select-Object -First 1).Source)
    $info.Arguments = '--distribution ' + $Distro + ' --user root --exec ' + ($Command -join ' ')
    $info.UseShellExecute = $false
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.CreateNoWindow = $true
    $process = [Diagnostics.Process]::Start($info)
    try {
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($Text -replace "`r`n", "`n") + "`n")
        $process.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
        $process.StandardInput.Close()
        $process.WaitForExit()
        $output = (($stdout.Result + $stderr.Result) -replace "`0", '').Trim()
        return [pscustomobject]@{ Code = $process.ExitCode; Output = $output }
    } finally { $process.Dispose() }
}

function New-ODSPortalLinuxAccount([string]$Distro, $Account) {
    $exists = Invoke-ODSPortalWsl -Arguments @('--distribution', $Distro, '--user', 'root', '--exec', 'id', '-u', $Account.Name)
    if ($exists.Code -ne 0) {
        $created = Invoke-ODSPortalWsl -Arguments @('--distribution', $Distro, '--user', 'root', '--exec', 'useradd', '--create-home', '--shell', '/bin/bash', '--groups', 'sudo', '--', $Account.Name)
        if ($created.Code -ne 0) { throw "Could not create the Ubuntu user $($Account.Name): $($created.Output)" }
    }
    # The password travels only on chpasswd's stdin, never in arguments or logs.
    $password = Invoke-ODSPortalWslInput $Distro @('chpasswd') ($Account.Name + ':' + $Account.Password)
    if ($password.Code -ne 0) { throw "Could not set the Ubuntu password for $($Account.Name). Open $Distro, run: sudo passwd $($Account.Name), then rerun this command." }
    $config = @'
import configparser, sys
path = '/etc/wsl.conf'
parser = configparser.ConfigParser(interpolation=None)
parser.optionxform = str
parser.read(path)
for section, key, value in (('user', 'default', sys.argv[1]), ('boot', 'systemd', 'true')):
    if not parser.has_section(section):
        parser.add_section(section)
    parser.set(section, key, value)
with open(path, 'w') as handle:
    parser.write(handle)
'@
    $written = Invoke-ODSPortalWslInput $Distro @('python3', '-', $Account.Name) $config
    if ($written.Code -ne 0) { throw "Could not make $($Account.Name) the default Ubuntu user: $($written.Output)" }
    $null = Invoke-ODSPortalWsl -Arguments @('--terminate', $Distro)
}

# ------------------------------------------------------ continue after restart

function Get-ODSPortalEntryScript([string]$InstallerRoot) {
    $root = Join-Path (Split-Path -Parent (Split-Path -Parent $InstallerRoot)) 'install.ps1'
    if (Test-Path -LiteralPath $root) { return $root }
    return (Join-Path $InstallerRoot 'windows-portal.ps1')
}

function Register-ODSPortalResume([string]$InstallerRoot, [System.Collections.IDictionary]$Options) {
    # RunOnce is per user, needs no elevation and Windows deletes the entry
    # before running it, so it can never loop.
    $folder = Join-Path $env:LOCALAPPDATA 'ODS'
    $null = New-Item -ItemType Directory -Path $folder -Force
    $resume = Join-Path $folder 'portal-setup-resume.ps1'
    [IO.File]::WriteAllText($resume, (New-ODSPortalResumeScript (Get-ODSPortalEntryScript $InstallerRoot) $Options), [Text.UTF8Encoding]::new($true))
    $shell = Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
    $command = '"' + $shell + '" -NoExit -NoProfile -ExecutionPolicy Bypass -File "' + $resume + '"'
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce'
    if (-not (Test-Path -LiteralPath $key)) { $null = New-Item -Path $key -Force }
    Set-ItemProperty -LiteralPath $key -Name $script:ODSPortalResumeValue -Value $command
}

function Request-ODSPortalRestart([string]$InstallerRoot, [System.Collections.IDictionary]$Options, [string]$Reason) {
    Register-ODSPortalResume $InstallerRoot $Options
    Write-Host ''
    Write-Host "  $Reason" -ForegroundColor Yellow
    Write-Host '  Save your work and restart Windows. After you sign in again, ODS setup continues automatically in a new window.'
    Write-Host '  ODS has not been installed yet.'
    return 3010
}
