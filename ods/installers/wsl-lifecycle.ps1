# Owned lifetime of one ODS Linux installation inside WSL. Dot-sourcing defines
# functions only. The separate native Windows/Docker Desktop path does not use it.
[CmdletBinding()]
param(
    [ValidateSet('start','status','stop','restart','release','hold')][string]$Action = 'status',
    [string]$Distro,
    [string]$InstallRoot,
    [string]$InstanceDirectory
)
$ErrorActionPreference = 'Stop'
$script:ODSWslLifecycleSource = $PSCommandPath

function Get-ODSWslIdentity([string]$Distro, [string]$InstallRoot) {
    if ([string]::IsNullOrWhiteSpace($Distro) -or $Distro -match '[\x00-\x1f"\\]') { throw 'Invalid WSL distribution name' }
    if ($InstallRoot -notmatch '^/[^\x00-\x1f]+$' -or $InstallRoot -match '(^|/)\.\.?(/|$)' -or $InstallRoot.Contains('//')) { throw 'An absolute, normalized Linux install root is required' }
    $InstallRoot = $InstallRoot.TrimEnd('/')
    if ([string]::IsNullOrWhiteSpace($InstallRoot) -or $InstallRoot -eq '/') { throw 'The Linux installation root cannot be empty or the filesystem root' }
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $id = -join ($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes("$sid`n$Distro`n$InstallRoot")) | ForEach-Object { $_.ToString('x2') }) } finally { $hash.Dispose() }
    [pscustomobject]@{ schemaVersion=1; ownerSid=$sid; distro=$Distro; installRoot=$InstallRoot; id=$id; taskName="ODS-WSL-$($id.Substring(0,24))"; directory=(Join-Path $env:LOCALAPPDATA "ODS\wsl\$id") }
}

function Assert-ODSPrivatePath([string]$Path, [switch]$Directory) {
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or ($Directory -and -not $item.PSIsContainer)) { throw "Unsafe lifecycle path: $Path" }
    $ancestor = if ($item.PSIsContainer) { $item } else { $item.Directory }
    while ($ancestor) { if ($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Lifecycle path has a junction ancestor' }; $ancestor=$ancestor.Parent }
    $acl = Get-Acl -LiteralPath $Path
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $sid) { throw 'Lifecycle path has a different owner' }
    foreach ($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin @($sid,'S-1-5-18')) { throw 'Lifecycle path is accessible to another identity' }
    }
}

function Initialize-ODSPrivateDirectory([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        $parent = Split-Path -Parent $Path
        if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        # Reject junction ancestors before creating private executable state.
        $ancestor = Get-Item -LiteralPath $parent
        while ($ancestor) { if ($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Lifecycle directory has a junction ancestor' }; $ancestor=$ancestor.Parent }
        $security = New-Object Security.AccessControl.DirectorySecurity
        $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
        $security.SetOwner($sid)
        $security.SetAccessRuleProtection($true,$false)
        foreach ($principal in @($sid, [Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
            $security.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($principal,'FullControl','ContainerInherit,ObjectInherit','None','Allow'))
        }
        # The task always uses Windows PowerShell 5.1 for ACL-aware creation.
        if ($PSVersionTable.PSEdition -eq 'Desktop') {
            [IO.Directory]::CreateDirectory($Path,$security) | Out-Null
        } else {
            [IO.FileSystemAclExtensions]::Create([IO.DirectoryInfo]::new($Path),$security)
        }
    }
    Assert-ODSPrivatePath $Path -Directory
}

function Read-ODSWslJson([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    Assert-ODSPrivatePath $Path
    if ((Get-Item -LiteralPath $Path).Length -gt 65536) { throw 'Oversized lifecycle metadata' }
    Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Write-ODSPrivateBytes([string]$Path,[byte[]]$Bytes,[switch]$CreateOnly) {
    Assert-ODSPrivatePath (Split-Path -Parent $Path) -Directory
    # Never reclaim existing wrong-owner state. Only our fresh CreateNew file
    # gets an explicit owner; an elevated SSH token may otherwise default its
    # owner to BUILTIN\Administrators despite the inherited private DACL.
    if (Test-Path -LiteralPath $Path) {
        Assert-ODSPrivatePath $Path
        if ($CreateOnly) { throw 'Private lifecycle file already exists' }
    }
    $temporary="$Path.$([guid]::NewGuid().ToString('N')).tmp"
    $stream=[IO.File]::Open($temporary,'CreateNew','Write','None')
    try { $stream.Write($Bytes,0,$Bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
    $acl=Get-Acl -LiteralPath $temporary
    $acl.SetOwner([Security.Principal.WindowsIdentity]::GetCurrent().User)
    Set-Acl -LiteralPath $temporary -AclObject $acl
    Assert-ODSPrivatePath $temporary
    if (-not $CreateOnly -and (Test-Path -LiteralPath $Path)) { [IO.File]::Replace($temporary,$Path,[NullString]::Value) } else { [IO.File]::Move($temporary,$Path) }
}

function Write-ODSWslJson([string]$Path, $Value) {
    Write-ODSPrivateBytes $Path ([Text.UTF8Encoding]::new($false).GetBytes(($Value | ConvertTo-Json -Depth 8)))
}

function Open-ODSPrivateLock([string]$Path) {
    # Racing initializers must never replace an already-open lock file.
    if (-not (Test-Path -LiteralPath $Path)) { Write-ODSPrivateBytes $Path ([byte[]]@()) -CreateOnly }
    Assert-ODSPrivatePath $Path
    [IO.File]::Open($Path,'Open','ReadWrite','None')
}

function Get-ODSWslRunningDistributions {
    $names = & (Join-Path $env:WINDIR 'System32\wsl.exe') --list --running --quiet 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect running WSL distributions' }
    @($names | ForEach-Object { ($_ -replace "`0",'').Trim() } | Where-Object { $_ })
}

function Get-ODSProcessIdentity([int]$ProcessId) {
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) { return $null }
    $native = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId"
    if (-not $native) { return $null }
    [pscustomobject]@{ pid=$ProcessId; startTicks=$process.StartTime.ToUniversalTime().Ticks.ToString(); executable=$native.ExecutablePath; commandLine=$native.CommandLine }
}

function Test-ODSProcessIdentity($Expected, $Actual) {
    $null -ne $Expected -and $null -ne $Actual -and $Expected.pid -eq $Actual.pid -and
        $Expected.startTicks -ceq $Actual.startTicks -and $Expected.executable -ieq $Actual.executable -and
        -not [string]::IsNullOrEmpty($Expected.commandLine) -and $Expected.commandLine -ceq $Actual.commandLine
}

function Stop-ODSOwnedProcess($Expected) {
    if (-not $Expected) { return }
    $process = Get-Process -Id $Expected.pid -ErrorAction SilentlyContinue
    if (-not $process) { return }
    # Pin the process handle BEFORE comparing identity; Kill then uses this
    # handle, never a later lookup of a potentially recycled numeric PID.
    $null = $process.Handle
    if (-not (Test-ODSProcessIdentity $Expected (Get-ODSProcessIdentity $Expected.pid))) { throw 'Owned WSL process identity changed; refusing to terminate it' }
    if ($process.StartTime.ToUniversalTime().Ticks.ToString() -cne $Expected.startTicks) { throw 'Owned process start time changed' }
    $process.Kill()
    if (-not $process.WaitForExit(10000)) { throw 'Owned WSL client did not exit' }
}

function Assert-ODSWslManifest($Identity) {
    $manifest = Read-ODSWslJson (Join-Path $Identity.directory 'instance.json')
    foreach ($name in @('schemaVersion','ownerSid','distro','installRoot','id','taskName','directory')) {
        if (-not $manifest -or $manifest.$name -cne $Identity.$name) { throw 'WSL lifetime manifest does not match this owner, distribution and installation' }
    }
    $manifest
}

function Get-ODSWslTaskArguments($Identity) {
    '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Action hold -InstanceDirectory "{1}"' -f (Join-Path $Identity.directory 'controller.ps1'),$Identity.directory
}

function Get-ODSWslHolderArguments($Identity) {
    $root64=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Identity.installRoot))
    # WSL builds can retain unnecessary quotes around a simple distro name.
    # Distribution validation excludes quotes/backslashes; quote only spaces.
    $distribution=if ($Identity.distro -match '\s') { '"'+$Identity.distro+'"' } else { $Identity.distro }
    '--distribution {0} --exec /bin/bash --noprofile --norc -c "exec /bin/sleep infinity" ods-wsl-{1}' -f $distribution,$root64
}

function Assert-ODSWslTask($Identity) {
    $task = Get-ScheduledTask -TaskName $Identity.taskName -ErrorAction SilentlyContinue
    if (-not $task) { throw 'Owned WSL lifetime task is missing' }
    $expectedExe = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $principalSid = $task.Principal.UserId
    if ($principalSid -notmatch '^S-1-') { $principalSid=([Security.Principal.NTAccount]::new($principalSid)).Translate([Security.Principal.SecurityIdentifier]).Value }
    if (@($task.Actions).Count -ne 1 -or $task.Actions[0].Execute -ine $expectedExe -or
        $task.Actions[0].Arguments -cne (Get-ODSWslTaskArguments $Identity) -or @($task.Triggers | Where-Object { $null -ne $_ }).Count -ne 0 -or
        $principalSid -ine $Identity.ownerSid -or $task.Principal.RunLevel -ne 'Limited' -or
        $task.Settings.ExecutionTimeLimit -ne 'PT0S' -or $task.Settings.RestartCount -ne 0) { throw 'WSL lifetime task identity changed' }
    $task
}

function Get-ODSWslLifetimeStatus($Identity) {
    $running = @(Get-ODSWslRunningDistributions) -contains $Identity.distro
    if (-not (Test-Path -LiteralPath $Identity.directory)) { return [pscustomobject]@{ scope='wsl-lifetime'; state='unmanaged'; distroRunning=$running; identity=$Identity; runtime=$null } }
    $null = Assert-ODSWslManifest $Identity
    $runtime = Read-ODSWslJson (Join-Path $Identity.directory 'runtime.json')
    $owned = $runtime -and $runtime.state -eq 'running' -and (Test-ODSProcessIdentity $runtime.child (Get-ODSProcessIdentity $runtime.child.pid))
    $state = if ($owned -and $running) { 'running' } elseif ($runtime -and $runtime.state -eq 'stopped') { 'stopped' } else { 'inactive' }
    [pscustomobject]@{ scope='wsl-lifetime'; state=$state; distroRunning=$running; identity=$Identity; runtime=$runtime }
}

function Start-ODSWslLifetime($Identity) {
    Initialize-ODSPrivateDirectory $Identity.directory
    $manifestPath = Join-Path $Identity.directory 'instance.json'
    if (Test-Path -LiteralPath $manifestPath) { $null=Assert-ODSWslManifest $Identity } else { Write-ODSWslJson $manifestPath $Identity }
    $status = Get-ODSWslLifetimeStatus $Identity
    if ($status.state -eq 'running') { $null=Assert-ODSWslTask $Identity; return $status }
    $task = Get-ScheduledTask -TaskName $Identity.taskName -ErrorAction SilentlyContinue
    if ($task) {
        $task=Assert-ODSWslTask $Identity
        if ($task.State -in @('Running','Queued')) { throw 'Existing lifecycle controller is active but not ready; inspect its runtime record' }
        Unregister-ScheduledTask -TaskName $Identity.taskName -Confirm:$false
    }
    # Immutable for the duration of this run; commands use the current source,
    # while an already-running controller continues using its private copy.
    Write-ODSPrivateBytes (Join-Path $Identity.directory 'controller.ps1') ([IO.File]::ReadAllBytes($script:ODSWslLifecycleSource))
    $generation=[guid]::NewGuid().ToString('N')
    Write-ODSWslJson (Join-Path $Identity.directory 'request.json') @{ generation=$generation; action='run' }
    $action=New-ScheduledTaskAction -Execute (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -Argument (Get-ODSWslTaskArguments $Identity)
    $principal=New-ScheduledTaskPrincipal -UserId $Identity.ownerSid -LogonType Interactive -RunLevel Limited
    $settings=New-ScheduledTaskSettingsSet -Hidden -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $Identity.taskName -Action $action -Principal $principal -Settings $settings -Description 'ODS owned WSL lifetime. On-demand only; explicit stop is never restarted automatically.' | Out-Null
    $null=Assert-ODSWslTask $Identity
    Start-ScheduledTask -TaskName $Identity.taskName
    for ($attempt=0; $attempt -lt 60; $attempt++) {
        $runtime=Read-ODSWslJson (Join-Path $Identity.directory 'runtime.json')
        if ($runtime -and $runtime.generation -eq $generation) {
            if ($runtime.state -eq 'running' -and (Test-ODSProcessIdentity $runtime.child (Get-ODSProcessIdentity $runtime.child.pid))) { return (Get-ODSWslLifetimeStatus $Identity) }
            if ($runtime.state -in @('failed','exited')) { throw "WSL lifetime startup failed: $($runtime.error)" }
        }
        Start-Sleep -Milliseconds 500
    }
    # Do not leave a delayed scheduler run able to start after reporting failure.
    Write-ODSWslJson (Join-Path $Identity.directory 'request.json') @{ generation=$generation; action='stop' }
    throw 'WSL lifetime startup timed out; stop was requested. Inspect runtime.json.'
}

function Stop-ODSWslLifetime($Identity) {
    $status=Get-ODSWslLifetimeStatus $Identity
    if ($status.state -eq 'unmanaged') { return $status }
    $task=Assert-ODSWslTask $Identity
    $request=Read-ODSWslJson (Join-Path $Identity.directory 'request.json')
    Write-ODSWslJson (Join-Path $Identity.directory 'request.json') @{ generation=$request.generation; action='stop' }
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        $runtime=Read-ODSWslJson (Join-Path $Identity.directory 'runtime.json')
        if (-not $runtime -and $task.State -notin @('Running','Queued')) {
            Write-ODSWslJson (Join-Path $Identity.directory 'runtime.json') @{ generation=$request.generation; state='stopped'; endedUtc=[DateTime]::UtcNow.ToString('o'); reason='controller did not start' }
            return (Get-ODSWslLifetimeStatus $Identity)
        }
        if ($runtime -and $runtime.generation -eq $request.generation -and $runtime.state -in @('stopped','exited','failed')) { return (Get-ODSWslLifetimeStatus $Identity) }
        if ($runtime -and -not (Test-ODSProcessIdentity $runtime.controller (Get-ODSProcessIdentity $runtime.controller.pid))) {
            Stop-ODSOwnedProcess $runtime.child
            Write-ODSWslJson (Join-Path $Identity.directory 'runtime.json') @{ generation=$request.generation; state='stopped'; endedUtc=[DateTime]::UtcNow.ToString('o'); reason='controller exited; exact child released' }
            return (Get-ODSWslLifetimeStatus $Identity)
        }
        Start-Sleep -Milliseconds 500
    }
    throw 'Stop requested but controller has not confirmed exit; no unrelated task or process was stopped'
}

function Invoke-ODSWslHolder([string]$Directory) {
    Assert-ODSPrivatePath $Directory -Directory
    $manifest=Read-ODSWslJson (Join-Path $Directory 'instance.json')
    $identity=Get-ODSWslIdentity $manifest.distro $manifest.installRoot
    if ($Directory -cne $identity.directory) { throw 'Controller directory does not match its identity' }
    $null=Assert-ODSWslManifest $identity
    $controllerLock=Open-ODSPrivateLock (Join-Path $Directory 'controller.lock')
    $request=Read-ODSWslJson (Join-Path $Directory 'request.json')
    if ($request.action -ne 'run') { $controllerLock.Dispose(); return }
    $runtime=@{ generation=$request.generation; state='starting'; controller=(Get-ODSProcessIdentity $PID); child=$null; startedUtc=[DateTime]::UtcNow.ToString('o'); error=$null }
    $child=$null
    try {
        # Root is an identity argument, not executable shell content. GNU sleep
        # has no six-hour timer, and Windows Task Scheduler has no time limit.
        # sleep cannot accept identity arguments. A fixed shell wrapper passes
        # them as $0/$1 while execing only the constant sleep command.
        $holderArguments=Get-ODSWslHolderArguments $identity
        $child=Start-Process -FilePath (Join-Path $env:WINDIR 'System32\wsl.exe') -ArgumentList $holderArguments -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $Directory 'holder.stdout') -RedirectStandardError (Join-Path $Directory 'holder.stderr')
        $null=$child.Handle
        $runtime.child=Get-ODSProcessIdentity $child.Id
        if (-not $runtime.child) { throw 'WSL client exited before identity capture' }
        $runtime.state='running'; Write-ODSWslJson (Join-Path $Directory 'runtime.json') $runtime
        while (-not $child.HasExited) {
            $current=Read-ODSWslJson (Join-Path $Directory 'request.json')
            if ($current.generation -cne $request.generation -or $current.action -ne 'run') {
                Stop-ODSOwnedProcess $runtime.child
                $runtime.state='stopped'
                break
            }
            Start-Sleep -Milliseconds 500
            $child.Refresh()
        }
        if ($runtime.state -ne 'stopped') { $runtime.state='exited' }
        $child.WaitForExit()
        # PowerShell 5.1 can expose a null ExitCode on a detached Process object;
        # report the nullable value honestly, never use it as success evidence.
        $runtime.exitCode=$child.ExitCode
    } catch { $runtime.state='failed'; $runtime.error=$_.Exception.Message; if ($runtime.child) { Stop-ODSOwnedProcess $runtime.child } }
    finally { $runtime.endedUtc=[DateTime]::UtcNow.ToString('o'); Write-ODSWslJson (Join-Path $Directory 'runtime.json') $runtime; if ($child) { $child.Dispose() }; $controllerLock.Dispose() }
}

function ConvertTo-ODSBashArgument([string]$Value) {
    if ($Value.Contains([char]0)) { throw 'NUL is not a shell argument' }
    "'" + $Value.Replace("'", "'\''") + "'"
}

function New-ODSWslRootCommand([string]$RepoRoot) {
    "cd -- " + (ConvertTo-ODSBashArgument $RepoRoot) + " && source " +
        (ConvertTo-ODSBashArgument "$RepoRoot/installers/lib/path-utils.sh") + " && resolve_install_dir"
}

function New-ODSWslInstallerCommand([string]$RepoRoot,[string[]]$Arguments,[string]$ResolvedRoot) {
    $command="cd -- " + (ConvertTo-ODSBashArgument $RepoRoot) + " && "
    if ($ResolvedRoot) { $command += "env INSTALL_DIR=" + (ConvertTo-ODSBashArgument $ResolvedRoot) + " " }
    $command += 'bash install-core.sh'
    foreach ($argument in $Arguments) { $command += ' ' + (ConvertTo-ODSBashArgument $argument) }
    $command
}

function Assert-ODSWslStackPlan($Identity,[string]$Action,$Plan) {
    $required=@('schemaVersion','action','installRoot','ownerUid','nativeUnits')
    $names=@($Plan.PSObject.Properties.Name)
    if ($names.Count -ne $required.Count -or @($names | Where-Object { $_ -notin $required }).Count -gt 0 -or
        $Plan.schemaVersion -ne 1 -or ($Plan.schemaVersion -isnot [int] -and $Plan.schemaVersion -isnot [long]) -or
        $Plan.action -isnot [string] -or $Plan.action -cne $Action -or $Plan.installRoot -isnot [string] -or $Plan.installRoot -cne $Identity.installRoot -or
        ($Plan.ownerUid -isnot [int] -and $Plan.ownerUid -isnot [long]) -or $Plan.ownerUid -le 0 -or $Plan.ownerUid -gt 4294967294 -or
        $Plan.nativeUnits -isnot [Array]) { throw 'Invalid owner-verified WSL lifecycle plan' }
    $allowed=@('pixel-ingress.service','openclaw-gateway.service','pixel-extension-manager.service','pixel-artifact-promoter.service','pixel-workspace-preview.service')
    if ($Plan.nativeUnits.Count -ne 0) {
        if ($Plan.nativeUnits.Count -ne $allowed.Count) { throw 'Unexpected native service plan' }
        for ($i=0;$i -lt $allowed.Count;$i++) {
            if ($Plan.nativeUnits[$i] -isnot [string] -or $Plan.nativeUnits[$i] -cne $allowed[$i]) { throw 'Unexpected native service in lifecycle plan' }
        }
    }
}

function Invoke-ODSWslCommand($Identity,[string[]]$Arguments,[switch]$AsRoot) {
    $target=@('--distribution',$Identity.distro)
    if ($AsRoot) {
        $allowed=@('pixel-ingress.service','openclaw-gateway.service','pixel-extension-manager.service','pixel-artifact-promoter.service','pixel-workspace-preview.service')
        if ($Arguments.Count -ne 3 -or $Arguments[0] -cne '/usr/bin/systemctl' -or
            $Arguments[1] -cnotin @('start','stop') -or $Arguments[2] -cnotin $allowed) { throw 'Only exact native systemctl lifecycle commands may run as WSL root' }
        $target+=@('--user','root')
    }
    $target+=@('--exec')+$Arguments
    & (Join-Path $env:WINDIR 'System32\wsl.exe') @target
    if ($LASTEXITCODE -ne 0) { throw 'WSL lifecycle command failed; lifetime client remains available for diagnosis' }
}

function Invoke-ODSWslNativeUnit($Identity,[string]$Action,[string]$Unit) {
    $allowed=@('pixel-ingress.service','openclaw-gateway.service','pixel-extension-manager.service','pixel-artifact-promoter.service','pixel-workspace-preview.service')
    if ($Action -notin @('start','stop') -or $Unit -cnotin $allowed) { throw 'Invalid fixed native lifecycle command' }
    # The signed-in Windows distro owner already has WSL --user root authority.
    # Execute only this fixed system executable/argv; never owner Python/bash.
    Invoke-ODSWslCommand $Identity @('/usr/bin/systemctl',$Action,$Unit) -AsRoot
    $state=(Invoke-ODSWslCommand $Identity @('/usr/bin/systemctl','show',$Unit,'--property=ActiveState','--value') | Out-String).Trim()
    if (($Action -eq 'start' -and $state -ne 'active') -or
        ($Action -eq 'stop' -and $state -notin @('inactive','failed'))) { throw "Native ODS unit did not reach the requested state: $Unit" }
}

function Invoke-ODSWslStack($Identity,[string]$Action) {
    if ($Action -notin @('start','stop')) { throw 'Invalid stack lifecycle action' }
    $program="$($Identity.installRoot)/installers/lib/wsl_stack.py"
    $raw=(Invoke-ODSWslCommand $Identity @('python3',$program,"plan-$Action",$Identity.installRoot) | Out-String)
    if ($raw.Length -gt 65536) { throw 'Could not obtain the ordinary-owner lifecycle plan; no services were changed' }
    $plan=$raw | ConvertFrom-Json
    Assert-ODSWslStackPlan $Identity $Action $plan
    $units=@($plan.nativeUnits)
    if ($Action -eq 'stop') {
        foreach ($unit in $units) { Invoke-ODSWslNativeUnit $Identity 'stop' $unit }
    }
    # Compose always executes as the ordinary Linux owner, never as root.
    Invoke-ODSWslCommand $Identity @('python3',$program,"compose-$Action",$Identity.installRoot)
    if ($Action -eq 'start') {
        [Array]::Reverse($units)
        foreach ($unit in $units) { Invoke-ODSWslNativeUnit $Identity 'start' $unit }
    }
}

function Invoke-ODSWslLifecycle([string]$Action,[string]$Distro,[string]$InstallRoot) {
    $identity=Get-ODSWslIdentity $Distro $InstallRoot
    if ($Action -eq 'status') { return (Get-ODSWslLifetimeStatus $identity) }
    Initialize-ODSPrivateDirectory $identity.directory
    $lock=Open-ODSPrivateLock (Join-Path $identity.directory 'command.lock')
    try {
        if ($Action -eq 'release') { return (Stop-ODSWslLifetime $identity) }
        if ($Action -in @('stop','restart')) {
            $status=Get-ODSWslLifetimeStatus $identity
            # A stopped distribution is never entered by stop.
            if ($status.distroRunning) { Invoke-ODSWslStack $identity 'stop' }
            $status=Stop-ODSWslLifetime $identity
            if ($Action -eq 'stop') { return $status }
        }
        $status=Start-ODSWslLifetime $identity
        Invoke-ODSWslStack $identity 'start'
        Get-ODSWslLifetimeStatus $identity
    } finally { $lock.Dispose() }
}

if ($MyInvocation.InvocationName -ne '.') {
    if ($Action -eq 'hold') { Invoke-ODSWslHolder $InstanceDirectory }
    else { Invoke-ODSWslLifecycle $Action $Distro $InstallRoot | ConvertTo-Json -Depth 8 }
}
