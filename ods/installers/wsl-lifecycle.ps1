# Owned lifetime of one ODS Linux installation inside WSL. Dot-sourcing defines
# functions only. The separate native Windows/Docker Desktop path does not use it.
[CmdletBinding()]
param(
    [ValidateSet('start','status','stop','restart','release','hold','relay-hold','autostart','disable-startup','enable-startup')][string]$Action = 'status',
    [string]$Distro,
    [string]$InstallRoot,
    [string]$InstanceDirectory,
    [switch]$ValidateOnly,
    [string]$StateRoot,
    [string]$DockerDesktopPath,
    [switch]$RetireRelay
)
$ErrorActionPreference = 'Stop'
if ($RetireRelay -and $Action -ne 'disable-startup') { throw 'RetireRelay is supported only for disable-startup' }
$script:ODSWslLifecycleSource = $PSCommandPath
$script:ODSWslStartupDeadline = $null
$script:ODSWslStartupIdentity = $null
$script:ODSWslStartupGeneration = $null
$script:ODSWslStateRoot = ''
if ($StateRoot) {
    if ($StateRoot -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$))' -or $StateRoot -match '[\x00-\x1f"]') { throw 'An absolute Windows state directory is required' }
    $script:ODSWslStateRoot = [IO.Path]::GetFullPath($StateRoot).TrimEnd('\')
    if ($script:ODSWslStateRoot -eq [IO.Path]::GetPathRoot($StateRoot).TrimEnd('\')) { throw 'State directory cannot be a filesystem root' }
}

function Get-ODSWslUtcNow { [DateTime]::UtcNow }

function Resolve-ODSWslRegisteredDistro([string]$Name) {
    $names = & (Join-Path ([Environment]::SystemDirectory) 'wsl.exe') --list --quiet 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect registered WSL distributions' }
    $registeredNames = @($names | ForEach-Object { ($_ -replace "`0", '').Trim() } | Where-Object { $_ -ieq $Name })
    if ($registeredNames.Count -ne 1) { throw 'Select one registered WSL distribution' }
    $registeredNames[0]
}

function Get-ODSWslIdentity([string]$Distro, [string]$InstallRoot) {
    if ([string]::IsNullOrWhiteSpace($Distro) -or $Distro -match '[\x00-\x1f"\\]') { throw 'Invalid WSL distribution name' }
    if ($InstallRoot -notmatch '^/[^\x00-\x1f]+$' -or $InstallRoot -match '(^|/)\.\.?(/|$)' -or $InstallRoot.Contains('//')) { throw 'An absolute, normalized Linux install root is required' }
    $InstallRoot = $InstallRoot.TrimEnd('/')
    if ([string]::IsNullOrWhiteSpace($InstallRoot) -or $InstallRoot -eq '/') { throw 'The Linux installation root cannot be empty or the filesystem root' }
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $id = -join ($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes("$sid`n$Distro`n$InstallRoot")) | ForEach-Object { $_.ToString('x2') }) } finally { $hash.Dispose() }
    $stateBase = if ($script:ODSWslStateRoot) { $script:ODSWslStateRoot } else { Join-Path $env:LOCALAPPDATA 'ODS\wsl' }
    [pscustomobject]@{ schemaVersion=1; ownerSid=$sid; distro=$Distro; installRoot=$InstallRoot; id=$id; taskName="ODS-WSL-$($id.Substring(0,24))"; directory=(Join-Path $stateBase $id) }
}

function Assert-ODSPrivatePath([string]$Path, [switch]$Directory) {
    $item = Get-Item -LiteralPath $Path -Force
    # FileInfo.Attributes becomes -1 if a publisher replaces this name before
    # its lazy refresh. GetAttributes throws FileNotFound instead of making
    # that missing-file sentinel look like every attribute (including reparse).
    $attributes=[IO.File]::GetAttributes($Path)
    if (($attributes -band [IO.FileAttributes]::ReparsePoint) -or ($Directory -and -not ($attributes -band [IO.FileAttributes]::Directory))) { throw "Unsafe lifecycle path: $Path" }
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
    $watch=[Diagnostics.Stopwatch]::StartNew()
    $observed=$false
    for($attempt=0; $attempt -lt 40; $attempt++) {
        $stream=$null
        try {
            if (Test-Path -LiteralPath $Path) { $observed=$true }
            Assert-ODSPrivatePath $Path
            # Publishers replace the file with a complete new copy. Allow that replacement
            # while retaining a complete old snapshot, but not in-place writes.
            $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,
                ([IO.FileShare]::Read -bor [IO.FileShare]::Delete))
            if ($stream.Length -gt 65536) { throw 'Oversized lifecycle metadata' }
            $reader=[IO.StreamReader]::new($stream,[Text.UTF8Encoding]::new($false),$true)
            try { $text=$reader.ReadToEnd() } finally { $reader.Dispose() }
            $stream=$null
            # PowerShell otherwise maps empty content and JSON null to the
            # same null result as an absent file. Lifecycle records are objects.
            if ($text -notmatch '^\s*\{') { throw 'Lifecycle metadata must be a JSON object' }
            $value=$text | ConvertFrom-Json -ErrorAction Stop
            if ($null -eq $value -or $value -isnot [pscustomobject]) { throw 'Lifecycle metadata must be a JSON object' }
            return $value
        } catch {
            $cause=$_.Exception.GetBaseException()
            $code=$cause.HResult -band 0xFFFF
            # Windows PowerShell also wraps this observed Get-Item replacement
            # miss as generic COR_E_IO. Do not classify other IO errors this way.
            $providerMissing=$cause -is [IO.IOException] -and $code -eq 5664 -and
                $_.FullyQualifiedErrorId -ceq 'ItemNotFound,Microsoft.PowerShell.Commands.GetItemCommand'
            $missing=$cause -is [Management.Automation.ItemNotFoundException] -or
                $cause -is [IO.FileNotFoundException] -or $cause -is [IO.DirectoryNotFoundException] -or $providerMissing
            $locked=$cause -is [IO.IOException] -and $code -in @(32,33)
            if (-not ($missing -or $locked)) { throw }
            if ($attempt -ge 39 -or $watch.ElapsedMilliseconds -ge 2000) {
                if ($missing -and -not $observed) { return $null }
                throw
            }
            # Windows can briefly hide the destination during replacement.
            # Confirm absence within the same bounded budget as sharing locks;
            # never turn a lock, invalid ACL, or malformed content into absence.
            # Revalidate the private path and handle size on every attempt.
        } finally {
            if ($stream) { $stream.Dispose() }
        }
        Start-Sleep -Milliseconds 50
    }
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
    if ($script:ODSWslStartupIdentity) {
        $output=Invoke-ODSWslBoundedCommand $script:ODSWslStartupIdentity @() 10 -ListRunning
        return @($output -split '\r?\n' | ForEach-Object { ($_ -replace "`0",'').Trim() } | Where-Object { $_ })
    }
    $names = & (Join-Path $env:WINDIR 'System32\wsl.exe') --list --running --quiet 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect running WSL distributions' }
    @($names | ForEach-Object { ($_ -replace "`0",'').Trim() } | Where-Object { $_ })
}

function Get-ODSProcessIdentity([int]$ProcessId) {
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) { return $null }
    try {
        # Keep this process instance pinned across the slower CIM query. A
        # holder can exit during shutdown, and its PID may then be reused.
        $null = $process.Handle
        $started = $process.StartTime
        if (-not $started -or $process.HasExited) { return $null }
        $native = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId"
        if (-not $native -or $process.HasExited) { return $null }
        [pscustomobject]@{ pid=$ProcessId; startTicks=$started.ToUniversalTime().Ticks.ToString(); executable=$native.ExecutablePath; commandLine=$native.CommandLine }
    } catch [System.InvalidOperationException] {
        # Process properties can become unavailable after an ordinary exit.
        return $null
    } finally { $process.Dispose() }
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

function Get-ODSWslRelayTaskArguments($Identity) {
    $arguments='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Action relay-hold -InstanceDirectory "{1}"' -f (Join-Path $Identity.directory 'relay-controller.ps1'),$Identity.directory
    if ($script:ODSWslStateRoot) { $arguments += ' -StateRoot "{0}"' -f $script:ODSWslStateRoot }
    $arguments
}

function Assert-ODSWslRelayTask($Identity) {
    $task=Get-ScheduledTask -TaskName ($Identity.taskName+'-Relay') -ErrorAction SilentlyContinue
    if (-not $task) { throw 'Owned WSL relay task is missing' }
    $expectedExe=Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
    $sid=$task.Principal.UserId
    if ($sid -notmatch '^S-1-') { $sid=([Security.Principal.NTAccount]::new($sid)).Translate([Security.Principal.SecurityIdentifier]).Value }
    if (@($task.Actions).Count -ne 1 -or $task.Actions[0].Execute -ine $expectedExe -or
        $task.Actions[0].Arguments -cne (Get-ODSWslRelayTaskArguments $Identity) -or
        @($task.Triggers | Where-Object { $null -ne $_ }).Count -ne 0 -or
        $sid -ine $Identity.ownerSid -or $task.Principal.LogonType -ne 'Interactive' -or
        $task.Principal.RunLevel -ne 'Limited' -or $task.Settings.ExecutionTimeLimit -ne 'PT0S' -or
        $task.Settings.RestartCount -ne 0 -or $task.Settings.MultipleInstances -ne 'IgnoreNew') {
        throw 'WSL relay task identity changed; no task or process was modified'
    }
    $task
}

function Start-ODSWslAgentRelay($Identity) {
    $null=Assert-ODSWslManifest $Identity
    $source=Join-Path (Split-Path -Parent $script:ODSWslLifecycleSource) 'wsl-agent-relay.ps1'
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw 'ODS WSL agent relay source is missing' }
    $destination=Join-Path $Identity.directory 'agent-relay.ps1'
    $controller=Join-Path $Identity.directory 'relay-controller.ps1'
    $recordPath=Join-Path $Identity.directory 'agent-relay-process.json'
    $taskName=$Identity.taskName+'-Relay'
    $task=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) { $task=Assert-ODSWslRelayTask $Identity }
    $record=Read-ODSWslJson $recordPath
    $runtime=Read-ODSWslJson (Join-Path $Identity.directory 'relay-runtime.json')
    $request=Read-ODSWslJson (Join-Path $Identity.directory 'relay-request.json')
    # A legacy caller-owned process is not durable, even when its source matches.
    if ($task -and $task.State -eq 'Running' -and $request -and $request.action -eq 'run' -and
        $runtime -and $runtime.generation -ceq $request.generation -and $runtime.state -eq 'running' -and
        (Test-ODSProcessIdentity $runtime.controller (Get-ODSProcessIdentity $runtime.controller.pid)) -and
        (Test-ODSProcessIdentity $record $runtime.child) -and
        (Test-ODSProcessIdentity $record (Get-ODSProcessIdentity $record.pid)) -and
        (Test-Path -LiteralPath $destination -PathType Leaf) -and (Test-Path -LiteralPath $controller -PathType Leaf)) {
        Assert-ODSPrivatePath $destination
        Assert-ODSPrivatePath $controller
        if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ceq (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -and
            (Get-FileHash -LiteralPath $script:ODSWslLifecycleSource -Algorithm SHA256).Hash -ceq (Get-FileHash -LiteralPath $controller -Algorithm SHA256).Hash) { return }
    }
    Stop-ODSWslAgentRelay $Identity
    Write-ODSPrivateBytes $destination ([IO.File]::ReadAllBytes($source))
    # Keep this copy independent of the already-running WSL holder controller.
    Write-ODSPrivateBytes $controller ([IO.File]::ReadAllBytes($script:ODSWslLifecycleSource))
    if (-not $task) {
        $action=New-ScheduledTaskAction -Execute (Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe') -Argument (Get-ODSWslRelayTaskArguments $Identity)
        $principal=New-ScheduledTaskPrincipal -UserId $Identity.ownerSid -LogonType Interactive -RunLevel Limited
        $settings=New-ScheduledTaskSettingsSet -Hidden -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings -Description 'ODS owned WSL agent relay. On-demand only; independent of the launching terminal.' | Out-Null
    }
    $task=Assert-ODSWslRelayTask $Identity
    if ($task.State -ne 'Ready') { throw 'Owned WSL relay task is not ready' }
    $generation=[guid]::NewGuid().ToString('N')
    $requestPath=Join-Path $Identity.directory 'relay-request.json'
    Write-ODSWslJson $requestPath @{generation=$generation;action='run'}
    $started=$false
    try {
        Assert-ODSWslStartupStillWanted
        Start-ScheduledTask -TaskName $taskName
        for ($attempt=0; $attempt -lt 60; $attempt++) {
            Assert-ODSWslStartupStillWanted
            $runtime=Read-ODSWslJson (Join-Path $Identity.directory 'relay-runtime.json')
            if ($runtime -and $runtime.generation -ceq $generation) {
                if ($runtime.state -eq 'running' -and
                    (Test-ODSProcessIdentity $runtime.controller (Get-ODSProcessIdentity $runtime.controller.pid)) -and
                    (Test-ODSProcessIdentity $runtime.child (Get-ODSProcessIdentity $runtime.child.pid))) { $started=$true; return }
                if ($runtime.state -in @('failed','exited','stopped')) { throw "WSL relay startup failed: $($runtime.error)" }
            }
            Start-Sleep -Milliseconds 500
        }
        throw 'WSL relay startup timed out; inspect relay-runtime.json'
    } finally {
        # A queued task must not start after the caller reports failure.
        if (-not $started) { Write-ODSWslJson $requestPath @{generation=$generation;action='stop'} }
    }
}

function Stop-ODSWslAgentRelay($Identity) {
    $taskName=$Identity.taskName+'-Relay'
    $task=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) { $null=Assert-ODSWslRelayTask $Identity }
    $recordPath=Join-Path $Identity.directory 'agent-relay-process.json'
    $record=Read-ODSWslJson $recordPath
    if ($task) {
        $request=Read-ODSWslJson (Join-Path $Identity.directory 'relay-request.json')
        Write-ODSWslJson (Join-Path $Identity.directory 'relay-request.json') @{generation=$request.generation;action='stop'}
        for ($attempt=0; $attempt -lt 30; $attempt++) {
            $task=Assert-ODSWslRelayTask $Identity
            if ($task.State -eq 'Ready') { break }
            Start-Sleep -Milliseconds 500
        }
        if ($task.State -ne 'Ready') {
            # Only this exact validated, on-demand owner task may be cancelled.
            $null=Assert-ODSWslRelayTask $Identity
            Stop-ScheduledTask -TaskName $taskName
            for ($attempt=0; $attempt -lt 20; $attempt++) {
                $task=Assert-ODSWslRelayTask $Identity
                if ($task.State -eq 'Ready') { break }
                Start-Sleep -Milliseconds 250
            }
            if ($task.State -ne 'Ready') { throw 'Owned WSL relay task did not stop' }
        }
        # A controller may have published its child after the initial read.
        $record=Read-ODSWslJson $recordPath
    }
    if ($record) {
        Stop-ODSOwnedProcess $record
        Remove-Item -LiteralPath $recordPath -Force
    }
}

function Invoke-ODSWslRelayHolder([string]$Directory) {
    if ($script:ODSWslStateRoot) { Assert-ODSPrivatePath $script:ODSWslStateRoot -Directory }
    Assert-ODSPrivatePath $Directory -Directory
    $manifest=Read-ODSWslJson (Join-Path $Directory 'instance.json')
    $identity=Get-ODSWslIdentity $manifest.distro $manifest.installRoot
    if ($Directory -cne $identity.directory) { throw 'Relay controller directory does not match its identity' }
    $null=Assert-ODSWslManifest $identity
    $null=Assert-ODSWslRelayTask $identity
    $lock=Open-ODSPrivateLock (Join-Path $Directory 'relay-controller.lock')
    $runtime=$null
    $child=$null
    try {
        $request=Read-ODSWslJson (Join-Path $Directory 'relay-request.json')
        if (-not $request -or $request.action -ne 'run') { return }
        $runtime=@{generation=$request.generation;state='starting';controller=(Get-ODSProcessIdentity $PID);child=$null;startedUtc=[DateTime]::UtcNow.ToString('o');error=$null}
        $source=Join-Path $Directory 'agent-relay.ps1'
        Assert-ODSPrivatePath $source
        $powershell=Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
        $arguments='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Distro "{1}"' -f $source,$identity.distro
        $current=Read-ODSWslJson (Join-Path $Directory 'relay-request.json')
        if (-not $current -or $current.generation -cne $request.generation -or $current.action -ne 'run') { $runtime.state='stopped'; return }
        # Task Scheduler owns this parent, so terminal/SSH exit cannot reap it.
        $child=Start-Process -FilePath $powershell -ArgumentList $arguments -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $Directory 'agent-relay.stdout') -RedirectStandardError (Join-Path $Directory 'agent-relay.stderr')
        $null=$child.Handle
        $runtime.child=Get-ODSProcessIdentity $child.Id
        if (-not $runtime.child) { throw 'WSL relay exited before identity capture' }
        Write-ODSWslJson (Join-Path $Directory 'agent-relay-process.json') $runtime.child
        Start-Sleep -Seconds 2
        $child.Refresh()
        if ($child.HasExited) { throw 'WSL relay exited before startup; inspect private agent-relay.stderr' }
        $runtime.state='running'; Write-ODSWslJson (Join-Path $Directory 'relay-runtime.json') $runtime
        while (-not $child.HasExited) {
            $current=Read-ODSWslJson (Join-Path $Directory 'relay-request.json')
            if (-not $current -or $current.generation -cne $request.generation -or $current.action -ne 'run') {
                Stop-ODSOwnedProcess $runtime.child
                $runtime.state='stopped'
                break
            }
            Start-Sleep -Milliseconds 500
            $child.Refresh()
        }
        if ($runtime.state -ne 'stopped') { $runtime.state='exited' }
        $child.WaitForExit()
        $runtime.exitCode=$child.ExitCode
    } catch {
        if ($runtime) {
            $runtime.state='failed'; $runtime.error=$_.Exception.Message
            if ($runtime.child) { Stop-ODSOwnedProcess $runtime.child }
        }
        throw
    } finally {
        try {
            if ($runtime) { $runtime.endedUtc=[DateTime]::UtcNow.ToString('o'); Write-ODSWslJson (Join-Path $Directory 'relay-runtime.json') $runtime }
        } finally {
            if ($child) { $child.Dispose() }
            $lock.Dispose()
        }
    }
}

function Get-ODSWslAgentAddressFailureReason([string]$Output) {
    if ($Output.Length -le 2048 -and $Output.TrimStart().StartsWith('{')) {
        try {
            $result=$Output | ConvertFrom-Json -ErrorAction Stop
            if ($result -is [pscustomobject] -and $result.error -is [string] -and
                $result.error -cmatch '\A[a-z][a-z-]{0,31}\z') { return $result.error }
        } catch { }
    }
    'unknown'
}

function Update-ODSWslAgentAddress($Identity) {
    $program="$($Identity.installRoot)/lib/wsl-agent-address.py"
    $raw=(Invoke-ODSWslBoundedCommand $Identity @('/usr/bin/python3',$program,$Identity.installRoot) 60 -Mutation -JsonErrorReason | Out-String)
    if ($raw.Length -gt 2048) { throw 'Oversized WSL agent address result' }
    $result=$raw | ConvertFrom-Json
    if ($result.mode -notin @('unmanaged','explicit','wsl-nat-bridge') -or $result.changed -isnot [bool]) {
        throw 'Invalid WSL agent address result'
    }
    $result
}

function Assert-ODSWslManifest($Identity) {
    $manifest = Read-ODSWslJson (Join-Path $Identity.directory 'instance.json')
    foreach ($name in @('schemaVersion','ownerSid','distro','installRoot','id','taskName','directory')) {
        if (-not $manifest -or $manifest.$name -cne $Identity.$name) { throw 'WSL lifetime manifest does not match this owner, distribution and installation' }
    }
    $manifest
}

function Get-ODSWslStartupIntent($Identity) {
    $value = Read-ODSWslJson (Join-Path $Identity.directory 'startup-intent.json')
    if (-not $value) { return $null }
    if ($value.schemaVersion -ne 1 -or $value.desiredRunning -isnot [bool] -or
        $value.generation -notmatch '^[a-f0-9]{32}$') { throw 'Invalid WSL startup preference' }
    foreach ($key in @('ownerSid','distro','installRoot','id')) {
        if ($value.$key -cne $Identity.$key) { throw 'WSL startup preference belongs to another installation' }
    }
    $value
}

function Set-ODSWslStartupIntent($Identity,[bool]$Running) {
    $null = Assert-ODSWslManifest $Identity
    $null = Get-ODSWslStartupIntent $Identity
    Write-ODSWslJson (Join-Path $Identity.directory 'startup-intent.json') @{
        schemaVersion=1; ownerSid=$Identity.ownerSid; distro=$Identity.distro;
        installRoot=$Identity.installRoot; id=$Identity.id; desiredRunning=$Running;
        generation=[guid]::NewGuid().ToString('N')
    }
}

function Get-ODSWslStartupArguments($Identity) {
    $arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Action autostart -InstanceDirectory "{1}"' -f (Join-Path $Identity.directory 'startup.ps1'),$Identity.directory
    if ($script:ODSWslStateRoot) { $arguments += ' -StateRoot "{0}"' -f $script:ODSWslStateRoot }
    $arguments
}

function Assert-ODSWslStartupTask($Identity) {
    $task = Get-ScheduledTask -TaskName ($Identity.taskName + '-Startup') -ErrorAction SilentlyContinue
    if (-not $task) { throw 'Owned WSL startup task is missing' }
    $triggers = @($task.Triggers | Where-Object { $null -ne $_ })
    $principal = $task.Principal.UserId
    if ($principal -notmatch '^S-1-') { $principal=([Security.Principal.NTAccount]::new($principal)).Translate([Security.Principal.SecurityIdentifier]).Value }
    $triggerSid = if ($triggers.Count -eq 1) { $triggers[0].UserId } else { '' }
    if ($triggerSid -and $triggerSid -notmatch '^S-1-') { $triggerSid=([Security.Principal.NTAccount]::new($triggerSid)).Translate([Security.Principal.SecurityIdentifier]).Value }
    if (@($task.Actions).Count -ne 1 -or
        $task.Actions[0].Execute -ine (Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe') -or
        $task.Actions[0].Arguments -cne (Get-ODSWslStartupArguments $Identity) -or
        $principal -ine $Identity.ownerSid -or $task.Principal.RunLevel -ne 'Limited' -or
        $task.Principal.LogonType -ne 'Interactive' -or $triggers.Count -ne 1 -or
        $triggers[0].CimClass.CimClassName -ne 'MSFT_TaskLogonTrigger' -or $triggerSid -ine $Identity.ownerSid -or
        $triggers[0].Delay -ne 'PT30S' -or $task.Settings.ExecutionTimeLimit -ne 'PT25M' -or
        $task.Settings.RestartCount -ne 0) {
        throw 'WSL startup task identity changed; the existing registration was left untouched'
    }
    $task
}

function Get-ODSWslStartupConfig($Identity) {
    $value = Read-ODSWslJson (Join-Path $Identity.directory 'startup-config.json')
    if (-not $value -or $value.schemaVersion -ne 1 -or $value.id -cne $Identity.id -or
        $value.dockerDesktopPath -notmatch '^[A-Za-z]:\\' -or
        [IO.Path]::GetFileName($value.dockerDesktopPath) -cne 'Docker Desktop.exe' -or
        [IO.Path]::GetFullPath($value.dockerDesktopPath) -cne $value.dockerDesktopPath) {
        throw 'Verified Docker Desktop startup path is missing or invalid; rerun Windows setup'
    }
    $value
}

function Enable-ODSWslStartup($Identity,[string]$DockerDesktopPath = '',[switch]$NewInstallation) {
    $null = Assert-ODSWslManifest $Identity
    $null = Assert-ODSWslTask $Identity
    $taskName = $Identity.taskName + '-Startup'
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) { $null = Assert-ODSWslStartupTask $Identity }
    if ($DockerDesktopPath) {
        if ($DockerDesktopPath -notmatch '^[A-Za-z]:\\' -or [IO.Path]::GetFileName($DockerDesktopPath) -cne 'Docker Desktop.exe' -or
            -not (Test-Path -LiteralPath $DockerDesktopPath -PathType Leaf)) { throw 'Setup did not supply an existing absolute Docker Desktop executable' }
        $resolved=[IO.Path]::GetFullPath($DockerDesktopPath)
        Write-ODSWslJson (Join-Path $Identity.directory 'startup-config.json') @{schemaVersion=1;id=$Identity.id;dockerDesktopPath=$resolved}
    }
    $null=Get-ODSWslStartupConfig $Identity
    # A second durable file leaves the currently running holder immutable.
    Write-ODSPrivateBytes (Join-Path $Identity.directory 'startup.ps1') ([IO.File]::ReadAllBytes($script:ODSWslLifecycleSource))
    $relaySource=Join-Path (Split-Path -Parent $script:ODSWslLifecycleSource) 'wsl-agent-relay.ps1'
    if (Test-Path -LiteralPath $relaySource -PathType Leaf) {
        Write-ODSPrivateBytes (Join-Path $Identity.directory 'wsl-agent-relay.ps1') ([IO.File]::ReadAllBytes($relaySource))
    }
    # Uninstall retires sign-in startup (stop preference, task disabled) but
    # keeps this per-root directory, so a new installation at the same root
    # inherited that stop and never returned after sign-in (fleet run,
    # 2026-10-03). A verified new installation re-arms recovery; an installer
    # rerun over an existing installation keeps the owner's explicit stop.
    $intent = Get-ODSWslStartupIntent $Identity
    if (-not $intent -or ($NewInstallation -and -not $intent.desiredRunning)) { Set-ODSWslStartupIntent $Identity $true }
    if (-not $existing) {
        $action = New-ScheduledTaskAction -Execute (Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe') -Argument (Get-ODSWslStartupArguments $Identity)
        $principal = New-ScheduledTaskPrincipal -UserId $Identity.ownerSid -LogonType Interactive -RunLevel Limited
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $Identity.ownerSid
        $trigger.Delay = 'PT30S'
        $settings = New-ScheduledTaskSettingsSet -Hidden -ExecutionTimeLimit ([TimeSpan]::FromMinutes(25)) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Trigger $trigger -Settings $settings -Description 'Restore this ODS WSL installation at owner sign-in only while its saved preference is running.' | Out-Null
    }
    $task = Assert-ODSWslStartupTask $Identity
    if ($NewInstallation -and $task.State -eq 'Disabled') { Enable-ScheduledTask -TaskName $taskName | Out-Null }
}

function Assert-ODSWslStartupStillWanted {
    if (-not $script:ODSWslStartupIdentity) { return }
    $intent = Get-ODSWslStartupIntent $script:ODSWslStartupIdentity
    if (-not $intent -or -not $intent.desiredRunning -or $intent.generation -cne $script:ODSWslStartupGeneration) {
        throw 'WSL startup cancelled by a newer owner command'
    }
    if ((Get-ODSWslUtcNow) -ge $script:ODSWslStartupDeadline) { throw 'WSL startup deadline exceeded; inspect startup-status.json and retry start after Docker is ready' }
}

function Get-ODSWslLifetimeForRetirement($Identity) {
    $task=Get-ScheduledTask -TaskName $Identity.taskName -ErrorAction SilentlyContinue
    if ($task) {
        $null=Assert-ODSWslTask $Identity
        $request=Read-ODSWslJson (Join-Path $Identity.directory 'request.json')
        if (-not $request -or [string]::IsNullOrWhiteSpace($request.generation) -or $request.action -notin @('run','stop')) {
            throw 'Owned WSL lifetime request is missing or invalid; generation requires recovery before uninstall'
        }
        $runtime=Read-ODSWslJson (Join-Path $Identity.directory 'runtime.json')
        if (-not $runtime -or [string]::IsNullOrWhiteSpace($runtime.generation) -or $runtime.generation -cne $request.generation) {
            # A Ready task does not prove that a former child exited. Do not
            # fabricate a stopped record when its ownership proof was lost.
            throw 'Owned WSL lifetime runtime is missing or inconsistent; child ownership requires recovery before uninstall'
        }
    } elseif ((Test-Path -LiteralPath (Join-Path $Identity.directory 'request.json')) -or
              (Test-Path -LiteralPath (Join-Path $Identity.directory 'runtime.json'))) {
        throw 'Owned WSL lifetime task is missing; retained lifetime records require recovery before uninstall'
    }
    $task
}

function Disable-ODSWslStartup($Identity,[switch]$ValidateOnly,[switch]$RetireRelay) {
    $task=Get-ScheduledTask -TaskName ($Identity.taskName + '-Startup') -ErrorAction SilentlyContinue
    $relayTask=$null
    $lifetimeTask=$null
    if ($RetireRelay) {
        $relayTask=Get-ScheduledTask -TaskName ($Identity.taskName + '-Relay') -ErrorAction SilentlyContinue
        $lifetimeTask=Get-ScheduledTask -TaskName $Identity.taskName -ErrorAction SilentlyContinue
    }
    if (-not (Test-Path -LiteralPath $Identity.directory)) {
        if ($task -or $relayTask -or $lifetimeTask) { throw 'Windows task exists without its owner manifest; refusing to modify it' }
        return [pscustomobject]@{scope='wsl-startup';state='unmanaged';identity=$Identity;relayRetirement='unmanaged'}
    }
    $null=Assert-ODSWslManifest $Identity
    if ($task) { $null=Assert-ODSWslStartupTask $Identity }
    # Uninstall validates every task it will retire before changing startup intent.
    if ($relayTask) { $null=Assert-ODSWslRelayTask $Identity }
    if ($RetireRelay) { $lifetimeTask=Get-ODSWslLifetimeForRetirement $Identity }
    $null=Get-ODSWslStartupIntent $Identity
    $lock=$null
    try {
        if ($ValidateOnly) {
            $path=Join-Path $Identity.directory 'command.lock'
            if (Test-Path -LiteralPath $path) {
                Assert-ODSPrivatePath $path
                $lock=[IO.File]::Open($path,'Open','ReadWrite','None')
            }
            Assert-ODSWslCommandSettled $Identity -ValidateOnly
            if ($RetireRelay) { $null=Get-ODSWslLifetimeForRetirement $Identity }
            return [pscustomobject]@{scope='wsl-startup';state='validated';identity=$Identity;relayRetirement=$(if ($RetireRelay) { 'validated' } else { 'not-requested' })}
        }
        Set-ODSWslStartupIntent $Identity $false
        if ($task) { Disable-ScheduledTask -TaskName ($Identity.taskName + '-Startup') | Out-Null }
        # Uninstall must not remove Linux assets while an old start is draining.
        $lock=Open-ODSWslCommandLock $Identity
        Assert-ODSWslCommandSettled $Identity
        # Ordinary login opt-out leaves manually running services alone. Only
        # explicit uninstall retirement stops the independently owned relay
        # and releases this installation's WSL holder, never the distribution.
        if ($RetireRelay) {
            # A start holding command.lock first may have registered a holder
            # after the precheck. Only the settled, locked state decides stop.
            $lifetimeTask=Get-ODSWslLifetimeForRetirement $Identity
            Stop-ODSWslAgentRelay $Identity
            if ($lifetimeTask) { $null=Stop-ODSWslLifetime $Identity }
        }
        [pscustomobject]@{scope='wsl-startup';state='disabled';identity=$Identity;relayRetirement=$(if ($RetireRelay) { 'stopped' } else { 'not-requested' })}
    } finally { if ($lock) { $lock.Dispose() } }
}

function Open-ODSWslCommandLock($Identity) {
    # A stop preference cancels the startup coordinator before this wait.
    # Give that bounded command a short opportunity to release its lock.
    for ($attempt=0; $attempt -lt 20; $attempt++) {
        try { return Open-ODSPrivateLock (Join-Path $Identity.directory 'command.lock') }
        catch [IO.IOException] {
            if ($attempt -eq 19) { throw 'Another ODS lifecycle command is still draining; the requested startup preference was saved. Wait for it to finish, then retry stop or start.' }
            Start-Sleep -Milliseconds 250
        }
    }
}

function Get-ODSWslWindowsBootId {
    (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().Ticks.ToString()
}

function Test-ODSWslLinuxBootId($Value) {
    $Value -is [string] -and $Value -cmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z'
}

function Get-ODSWslLinuxBootId($Identity) {
    # WSL2 runs every distribution in one VM. Its kernel draws a new boot_id
    # each time that VM starts (wsl --shutdown, idle VM shutdown, Windows
    # restart). Bounded and read-only; $null when WSL gives no clear answer.
    try { $value=(Invoke-ODSWslBoundedCommand $Identity @('/bin/cat','/proc/sys/kernel/random/boot_id') 10 | Out-String).Trim() }
    catch [IO.IOException], [TimeoutException] { return $null }
    if (Test-ODSWslLinuxBootId $value) { $value } else { $null }
}

function Assert-ODSWslCommandSettled($Identity,[switch]$ValidateOnly,[switch]$MayStartDistribution) {
    $pending=Read-ODSWslJson (Join-Path $Identity.directory 'command-pending.json')
    if ($pending -and $pending.state -ne 'completed') {
        # A later full Windows boot proves the old WSL command cannot still be
        # running. Sign-out, Docker restart and closing a terminal do not.
        $boot=Get-ODSWslWindowsBootId
        if ($pending.id -ceq $Identity.id -and $pending.bootId -match '^\d{1,19}$' -and
            [long]$boot -gt [long]$pending.bootId) {
            if (-not $ValidateOnly) {
                Write-ODSWslJson (Join-Path $Identity.directory 'command-pending.json') @{schemaVersion=1;state='completed';id=$Identity.id;reason='previous Windows boot ended'}
            }
            return
        }
        # A later WSL VM boot proves the same when the record holds the boot id
        # of the VM the command ran in. Reading the current id enters the
        # distribution, and booting a stopped one starts its enabled ODS units,
        # so only a start may boot it for this. Stop, release and uninstall
        # read the id only from a distribution that is already running.
        $linuxRecorded=$pending.id -ceq $Identity.id -and (Test-ODSWslLinuxBootId $pending.linuxBootId)
        if ($linuxRecorded -and ($MayStartDistribution -or (@(Get-ODSWslRunningDistributions) -contains $Identity.distro))) {
            $linuxBoot=Get-ODSWslLinuxBootId $Identity
            if ($linuxBoot -and $linuxBoot -cne $pending.linuxBootId) {
                if (-not $ValidateOnly) {
                    Write-ODSWslJson (Join-Path $Identity.directory 'command-pending.json') @{schemaVersion=1;state='completed';id=$Identity.id;reason='WSL VM restarted'}
                }
                return
            }
        }
        # Offer only a remedy that this same action can then verify.
        $remedy=if (-not $linuxRecorded) { 'Restart Windows (not just sign out)' }
            elseif ($MayStartDistribution) { 'Run `wsl --shutdown` (or restart Windows)' }
            else { 'Run `wsl --shutdown` and open ' + $Identity.distro + ' again (or restart Windows)' }
        throw ('A previous WSL stack command has no confirmed Linux completion. No further stack operation was attempted. ' + $remedy + ', then retry the requested lifecycle action; do not delete command-pending.json.')
    }
}

function Wait-ODSWslCommandProcess($Process,[DateTime]$Deadline,[switch]$Mutation) {
    $cancelled=$null
    while (-not $Process.WaitForExit(250)) {
        if (-not $cancelled) {
            try { Assert-ODSWslStartupStillWanted } catch {
                if (-not $Mutation) { throw }
                # Keep command.lock and the client alive while Linux finishes.
                # Stopping wsl.exe does not acknowledge stopping its command.
                $cancelled=$_.Exception.Message
            }
        }
        if ((Get-ODSWslUtcNow) -ge $Deadline) { throw [TimeoutException]::new('Bounded WSL command timed out without confirmed Linux completion') }
    }
    $cancelled
}

function Complete-ODSWslCommand($Identity,[string]$Token,[string]$Output,[int]$ExitCode) {
    $suffix="`nODS_WSL_COMPLETED_${Token}:${ExitCode}`n"
    if (-not $Output.EndsWith($suffix,[StringComparison]::Ordinal)) { throw 'WSL stack command exited without its Linux completion acknowledgement' }
    # GNU timeout and the Compose adapter reserve 124/137 for a potentially
    # interrupted descendant. Ordinary errors remain immediately retryable.
    if ($ExitCode -notin @(124,137)) {
        Write-ODSWslJson (Join-Path $Identity.directory 'command-pending.json') @{schemaVersion=1;state='completed';id=$Identity.id;token=$Token}
    }
    $Output.Substring(0,$Output.Length-$suffix.Length)
}

function ConvertTo-ODSWindowsArgument([string]$Value) {
    # WSL parses its option prefix itself and can retain redundant quotes on
    # simple tokens ("--list" becomes a Linux command). Quote only when needed.
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    # CommandLineToArgvW quoting, including quotes and trailing backslashes.
    $quoted = [Text.StringBuilder]::new(); $null = $quoted.Append('"'); $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') { $slashes++; continue }
        if ($character -eq '"') { $null = $quoted.Append(('\' * (2 * $slashes + 1))) }
        else { $null = $quoted.Append(('\' * $slashes)) }
        $null = $quoted.Append($character); $slashes = 0
    }
    $null = $quoted.Append(('\' * (2 * $slashes))); $null = $quoted.Append('"')
    $quoted.ToString()
}

function Assert-ODSWslRootArguments([string[]]$Arguments) {
    $allowed=@('pixel-ingress.service','openclaw-gateway.service','pixel-extension-manager.service','pixel-artifact-promoter.service','pixel-workspace-preview.service','pixel-preview-inspection.service')
    $native=$Arguments[1] -cin @('start','stop') -and $Arguments[2] -cin $allowed
    $agentRestart=$Arguments[1] -ceq 'restart' -and $Arguments[2] -ceq 'ods-host-agent.service'
    if ($Arguments.Count -ne 3 -or $Arguments[0] -cne '/usr/bin/systemctl' -or -not ($native -or $agentRestart)) {
        throw 'Only exact native systemctl lifecycle commands may run as WSL root'
    }
}

function Invoke-ODSWslBoundedCommand($Identity,[string[]]$Arguments,[int]$Seconds=15,[switch]$AsRoot,[switch]$ListRunning,[switch]$Mutation,[switch]$JsonErrorReason) {
    Assert-ODSWslStartupStillWanted
    if ($ListRunning -and ($AsRoot -or $Arguments.Count -or $Mutation)) { throw 'Distribution listing accepts no executable arguments' }
    if ($JsonErrorReason -and -not $Mutation) { throw 'JSON error reasons require a mutation command' }
    $target = @('--distribution',$Identity.distro)
    if ($AsRoot) {
        Assert-ODSWslRootArguments $Arguments
        $target += @('--user','root')
    }
    # A Linux acknowledgement clears a completed, non-timeout operation.
    # A lost client / timeout is ambiguous, so later commands fail closed.
    $token=[guid]::NewGuid().ToString('N')
    $completion='ODS_WSL_COMPLETED_' + $token
    if ($Mutation) {
        Assert-ODSWslCommandSettled $Identity -MayStartDistribution
        $pending=@{
            schemaVersion=1;state='pending';id=$Identity.id;token=$token;bootId=(Get-ODSWslWindowsBootId);startedUtc=(Get-ODSWslUtcNow).ToString('o')
        }
        # Lets a later WSL VM boot settle a lost acknowledgement. Without a
        # readable id, only a later Windows boot does (the original rule).
        $linuxBootId=Get-ODSWslLinuxBootId $Identity
        if ($linuxBootId) { $pending.linuxBootId=$linuxBootId }
        Write-ODSWslJson (Join-Path $Identity.directory 'command-pending.json') $pending
        $wrapper='/usr/bin/timeout --signal=TERM --kill-after=5s "$1" "${@:3}"; code=$?; printf "\n%s:%s\n" "$2" "$code"; exit "$code"'
        $target += @('--exec','/bin/bash','--noprofile','--norc','-c',$wrapper,'ods-wsl-command',([string]$Seconds),$completion) + $Arguments
    } else {
        $target += @('--exec','/usr/bin/timeout','--signal=TERM','--kill-after=5s',([string]$Seconds)) + $Arguments
    }
    if ($ListRunning) { $target=@('--list','--running','--quiet') }
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = Join-Path ([Environment]::SystemDirectory) 'wsl.exe'
    $info.Arguments = ($target | ForEach-Object { ConvertTo-ODSWindowsArgument $_ }) -join ' '
    $info.UseShellExecute=$false; $info.CreateNoWindow=$true
    $info.RedirectStandardOutput=$true; $info.RedirectStandardError=$true
    $process = [Diagnostics.Process]::new(); $process.StartInfo=$info; $started=$false
    try {
        if (-not $process.Start()) { throw 'Could not launch the bound WSL command' }
        $started=$true
        $null=$process.Handle
        $stdout=$process.StandardOutput.ReadToEndAsync(); $stderr=$process.StandardError.ReadToEndAsync()
        $deadline=(Get-ODSWslUtcNow).AddSeconds($Seconds + 30)
        $cancelled=Wait-ODSWslCommandProcess $process $deadline -Mutation:$Mutation
        if (-not $stdout.Wait(5000) -or -not $stderr.Wait(5000)) { throw [TimeoutException]::new('WSL command output did not close after the client exited') }
        $output=$stdout.GetAwaiter().GetResult(); $errorOutput=$stderr.GetAwaiter().GetResult()
        if ($Mutation) {
            $output=Complete-ODSWslCommand $Identity $token $output $process.ExitCode
        }
        if ($process.ExitCode -ne 0) {
            # Report the bounded helper reason only after the Linux completion
            # acknowledgement has settled the mutation. Never expose raw output.
            if ($JsonErrorReason) {
                $reason=Get-ODSWslAgentAddressFailureReason $output
                throw [IO.IOException]::new("WSL host-agent address preparation failed (reason: $reason).")
            }
            throw [IO.IOException]::new('WSL startup command failed: ' + $errorOutput.Trim())
        }
        if ($cancelled) { throw $cancelled }
        Assert-ODSWslStartupStillWanted
        $output
    } finally {
        if ($started -and -not $process.HasExited) { $process.Kill(); $null=$process.WaitForExit(5000) }
        $process.Dispose()
    }
}

function Start-ODSWslDockerDesktop($Identity) {
    $desktop = (Get-ODSWslStartupConfig $Identity).dockerDesktopPath
    if (-not (Test-Path -LiteralPath $desktop -PathType Leaf)) { throw 'Docker Desktop is missing; open setup before retrying startup' }
    Start-Process -FilePath $desktop -WindowStyle Hidden | Out-Null
}

function Invoke-ODSWslStartup([string]$Directory) {
    Assert-ODSPrivatePath $Directory -Directory
    $manifest=Read-ODSWslJson (Join-Path $Directory 'instance.json')
    $identity=Get-ODSWslIdentity $manifest.distro $manifest.installRoot
    if ($Directory -cne $identity.directory) { throw 'Startup directory does not match this owner and installation' }
    $null=Assert-ODSWslManifest $identity
    $null=Assert-ODSWslStartupTask $identity
    $startupLock=Open-ODSPrivateLock (Join-Path $Directory 'startup.lock')
    $status=@{schemaVersion=1;state='starting';startedUtc=[DateTime]::UtcNow.ToString('o');error=$null}
    $commandLock=$null
    try {
        $intent=Get-ODSWslStartupIntent $identity
        if (-not $intent -or -not $intent.desiredRunning) { $status.state='disabled'; return }
        $script:ODSWslStartupIdentity=$identity
        $script:ODSWslStartupGeneration=$intent.generation
        $script:ODSWslStartupDeadline=(Get-ODSWslUtcNow).AddMinutes(20)
        $status.state='waiting-for-docker'; Write-ODSWslJson (Join-Path $Directory 'startup-status.json') $status
        Start-ODSWslDockerDesktop $identity
        $dockerDeadline=(Get-ODSWslUtcNow).AddMinutes(10)
        $dockerReady=$false
        while ((Get-ODSWslUtcNow) -lt $dockerDeadline) {
            Assert-ODSWslStartupStillWanted
            try {
                $null=Invoke-ODSWslBoundedCommand $identity @('/usr/bin/env','docker','info') 15
                $null=Invoke-ODSWslBoundedCommand $identity @('/usr/bin/env','docker','compose','version') 15
                $dockerReady=$true; break
            } catch [IO.IOException], [TimeoutException] {
                Assert-ODSWslStartupStillWanted
                $status.error=$_.Exception.Message
                Write-ODSWslJson (Join-Path $Directory 'startup-status.json') $status
            }
            Start-Sleep -Seconds 3
        }
        if (-not $dockerReady) { throw "Docker did not become ready in this distribution within ten minutes; open Docker Desktop and run lifecycle start. Last probe error: $($status.error)" }
        $commandLock=Open-ODSPrivateLock (Join-Path $Directory 'command.lock')
        Assert-ODSWslCommandSettled $identity -MayStartDistribution
        Assert-ODSWslStartupStillWanted
        $status.state='starting-stack'; $status.error=$null
        Write-ODSWslJson (Join-Path $Directory 'startup-status.json') $status
        $null=Start-ODSWslLifetime $identity
        Invoke-ODSWslStack $identity 'start' | ForEach-Object { [Console]::Error.WriteLine([string]$_) }
        Assert-ODSWslStartupStillWanted
        $status.state='started'
    } catch { $status.state='failed'; $status.error=$_.Exception.Message; throw }
    finally {
        $status.endedUtc=[DateTime]::UtcNow.ToString('o')
        Write-ODSWslJson (Join-Path $Directory 'startup-status.json') $status
        $script:ODSWslStartupIdentity=$null; $script:ODSWslStartupDeadline=$null; $script:ODSWslStartupGeneration=$null
        if ($commandLock) { $commandLock.Dispose() }; $startupLock.Dispose()
    }
}

function Get-ODSWslTaskArguments($Identity) {
    $arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Action hold -InstanceDirectory "{1}"' -f (Join-Path $Identity.directory 'controller.ps1'),$Identity.directory
    if ($script:ODSWslStateRoot) { $arguments += ' -StateRoot "{0}"' -f $script:ODSWslStateRoot }
    $arguments
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
        $task.Settings.ExecutionTimeLimit -ne 'PT0S' -or $task.Settings.RestartCount -ne 0) {
        # Never adopt or replace a task this code did not register: say which
        # one it is and how the owner removes it if nothing else uses it.
        throw "WSL lifetime task identity changed: scheduled task $($Identity.taskName) does not match this ODS installation ($($Identity.distro), $($Identity.installRoot)); it was created by another ODS version or modified. If no other ODS installation uses it, remove it with: Unregister-ScheduledTask -TaskName '$($Identity.taskName)' -Confirm:`$false then rerun."
    }
    $task
}

function Get-ODSWslLifetimeStatus($Identity) {
    $running = @(Get-ODSWslRunningDistributions) -contains $Identity.distro
    if (-not (Test-Path -LiteralPath $Identity.directory)) { return [pscustomobject]@{ scope='wsl-lifetime'; state='unmanaged'; distroRunning=$running; identity=$Identity; runtime=$null } }
    if ($script:ODSWslStateRoot) { Assert-ODSPrivatePath $script:ODSWslStateRoot -Directory }
    $null = Assert-ODSWslManifest $Identity
    $runtime = Read-ODSWslJson (Join-Path $Identity.directory 'runtime.json')
    $owned = $runtime -and $runtime.state -eq 'running' -and (Test-ODSProcessIdentity $runtime.child (Get-ODSProcessIdentity $runtime.child.pid))
    $state = if ($owned -and $running) { 'running' } elseif ($runtime -and $runtime.state -eq 'stopped') { 'stopped' } else { 'inactive' }
    $intent=Get-ODSWslStartupIntent $Identity
    [pscustomobject]@{ scope='wsl-lifetime'; state=$state; distroRunning=$running; identity=$Identity; runtime=$runtime;
        startupEnabled=($null -ne $intent -and $intent.desiredRunning);
        startup=(Read-ODSWslJson (Join-Path $Identity.directory 'startup-status.json')) }
}

function Start-ODSWslLifetime($Identity) {
    Assert-ODSWslStartupStillWanted
    if ($script:ODSWslStateRoot) { Initialize-ODSPrivateDirectory $script:ODSWslStateRoot }
    Initialize-ODSPrivateDirectory $Identity.directory
    $manifestPath = Join-Path $Identity.directory 'instance.json'
    if (Test-Path -LiteralPath $manifestPath) { $null=Assert-ODSWslManifest $Identity } else { Write-ODSWslJson $manifestPath $Identity }
    $status = Get-ODSWslLifetimeStatus $Identity
    if ($status.state -eq 'running') { $null=Assert-ODSWslTask $Identity; return $status }
    $task = Get-ScheduledTask -TaskName $Identity.taskName -ErrorAction SilentlyContinue
    if ($task) {
        $task=Assert-ODSWslTask $Identity
        if ($task.State -ne 'Ready') { throw 'Existing lifecycle controller is not ready; inspect its runtime record' }
    }
    # Immutable for the duration of this run; commands use the current source,
    # while an already-running controller continues using its private copy.
    Write-ODSPrivateBytes (Join-Path $Identity.directory 'controller.ps1') ([IO.File]::ReadAllBytes($script:ODSWslLifecycleSource))
    $generation=[guid]::NewGuid().ToString('N')
    Write-ODSWslJson (Join-Path $Identity.directory 'request.json') @{ generation=$generation; action='run' }
    if (-not $task) {
        $action=New-ScheduledTaskAction -Execute (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -Argument (Get-ODSWslTaskArguments $Identity)
        $principal=New-ScheduledTaskPrincipal -UserId $Identity.ownerSid -LogonType Interactive -RunLevel Limited
        $settings=New-ScheduledTaskSettingsSet -Hidden -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $Identity.taskName -Action $action -Principal $principal -Settings $settings -Description 'ODS owned WSL lifetime. On-demand only; explicit stop is never restarted automatically.' | Out-Null
    }
    $task=Assert-ODSWslTask $Identity
    if ($task.State -ne 'Ready') { throw 'Existing lifecycle controller became active before start; inspect its runtime record' }
    Assert-ODSWslStartupStillWanted
    Start-ScheduledTask -TaskName $Identity.taskName
    for ($attempt=0; $attempt -lt 60; $attempt++) {
        Assert-ODSWslStartupStillWanted
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
    # A wedged controller must not leave an exact owned WSL client behind.
    # Revalidate the task immediately before stopping it, then use the child's
    # retained process identity/handle rather than terminating the distribution.
    $null=Assert-ODSWslTask $Identity
    $runtime=Read-ODSWslJson (Join-Path $Identity.directory 'runtime.json')
    if (-not $runtime -or $runtime.generation -cne $request.generation) {
        throw 'Stop could not establish the current controller generation; no unrelated task or process was stopped'
    }
    Stop-ScheduledTask -TaskName $Identity.taskName
    Stop-ODSOwnedProcess $runtime.child
    Write-ODSWslJson (Join-Path $Identity.directory 'runtime.json') @{
        generation=$request.generation;state='stopped';endedUtc=[DateTime]::UtcNow.ToString('o');
        reason='unresponsive owned controller stopped; exact child released'
    }
    Get-ODSWslLifetimeStatus $Identity
}

function Invoke-ODSWslHolder([string]$Directory) {
    if ($script:ODSWslStateRoot) { Assert-ODSPrivatePath $script:ODSWslStateRoot -Directory }
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
    $environment=@()
    if ($ResolvedRoot) { $environment += 'INSTALL_DIR=' + (ConvertTo-ODSBashArgument $ResolvedRoot) }
    if ($script:ODSWslStateRoot) { $environment += 'ODS_WSL_STATE_ROOT=' + (ConvertTo-ODSBashArgument $script:ODSWslStateRoot) }
    if ($environment.Count) { $command += 'env ' + ($environment -join ' ') + ' ' }
    $command += 'bash install-core.sh'
    foreach ($argument in $Arguments) { $command += ' ' + (ConvertTo-ODSBashArgument $argument) }
    $command
}

function Assert-ODSWslStackPlan($Identity,[string]$Action,$Plan) {
    $required=@('schemaVersion','action','installRoot','ownerUid','nativeUnits')
    $allowedNames=$required+@('hostAgentRestart')
    $names=@($Plan.PSObject.Properties.Name)
    if ($names.Count -notin @(5,6) -or @($names | Where-Object { $_ -cnotin $allowedNames }).Count -gt 0 -or
        @($required | Where-Object { $_ -cnotin $names }).Count -gt 0 -or
        $Plan.schemaVersion -ne 1 -or ($Plan.schemaVersion -isnot [int] -and $Plan.schemaVersion -isnot [long]) -or
        $Plan.action -isnot [string] -or $Plan.action -cne $Action -or $Plan.installRoot -isnot [string] -or $Plan.installRoot -cne $Identity.installRoot -or
        ($Plan.ownerUid -isnot [int] -and $Plan.ownerUid -isnot [long]) -or $Plan.ownerUid -le 0 -or $Plan.ownerUid -gt 4294967294 -or
        $Plan.nativeUnits -isnot [Array]) { throw 'Invalid owner-verified WSL lifecycle plan' }
    if ($names -ccontains 'hostAgentRestart' -and
        ($Plan.hostAgentRestart -isnot [bool] -or ($Action -cne 'start' -and $Plan.hostAgentRestart))) {
        throw 'Invalid owner-verified host agent restart request'
    }
    $allowed=@('pixel-ingress.service','openclaw-gateway.service','pixel-extension-manager.service','pixel-artifact-promoter.service','pixel-workspace-preview.service','pixel-preview-inspection.service')
    if ($Plan.nativeUnits.Count -ne 0) {
        # The owner-side verifier accepts only a complete legacy installation
        # or the complete inspection installation; partial artifacts fail there.
        if ($Plan.nativeUnits.Count -notin @(($allowed.Count - 1), $allowed.Count)) { throw 'Unexpected native service plan' }
        for ($i=0;$i -lt $Plan.nativeUnits.Count;$i++) {
            if ($Plan.nativeUnits[$i] -isnot [string] -or $Plan.nativeUnits[$i] -cne $allowed[$i]) { throw 'Unexpected native service in lifecycle plan' }
        }
    }
}

function Invoke-ODSWslCommand($Identity,[string[]]$Arguments,[switch]$AsRoot) {
    $target=@('--distribution',$Identity.distro)
    if ($AsRoot) {
        Assert-ODSWslRootArguments $Arguments
        $target+=@('--user','root')
    }
    if ($script:ODSWslStartupDeadline) {
        Assert-ODSWslStartupStillWanted
        $seconds=[Math]::Max(1,[int]($script:ODSWslStartupDeadline - (Get-ODSWslUtcNow)).TotalSeconds)
        $mutation=$AsRoot -or ($Arguments.Count -ge 3 -and $Arguments[0] -ceq 'python3' -and $Arguments[2] -cin @('compose-start','compose-stop'))
        return Invoke-ODSWslBoundedCommand $Identity $Arguments $seconds -AsRoot:$AsRoot -Mutation:$mutation
    }
    $target+=@('--exec')+$Arguments
    & (Join-Path $env:WINDIR 'System32\wsl.exe') @target
    if ($LASTEXITCODE -ne 0) { throw 'WSL lifecycle command failed; lifetime client remains available for diagnosis' }
}

function Invoke-ODSWslNativeUnit($Identity,[string]$Action,[string]$Unit) {
    $allowed=@('pixel-ingress.service','openclaw-gateway.service','pixel-extension-manager.service','pixel-artifact-promoter.service','pixel-workspace-preview.service','pixel-preview-inspection.service')
    $agentRestart=$Action -ceq 'restart' -and $Unit -ceq 'ods-host-agent.service'
    if (-not $agentRestart -and ($Action -cnotin @('start','stop') -or $Unit -cnotin $allowed)) { throw 'Invalid fixed native lifecycle command' }
    # The signed-in Windows distro owner already has WSL --user root authority.
    # Execute only this fixed system executable/argv; never owner Python/bash.
    Invoke-ODSWslCommand $Identity @('/usr/bin/systemctl',$Action,$Unit) -AsRoot
    $state=(Invoke-ODSWslCommand $Identity @('/usr/bin/systemctl','show',$Unit,'--property=ActiveState','--value') | Out-String).Trim()
    if (($Action -in @('start','restart') -and $state -ne 'active') -or
        ($Action -eq 'stop' -and $state -notin @('inactive','failed'))) { throw "Native ODS unit did not reach the requested state: $Unit" }
}

function Invoke-ODSWslStack($Identity,[string]$Action) {
    if ($Action -notin @('start','stop')) { throw 'Invalid stack lifecycle action' }
    $program="$($Identity.installRoot)/installers/lib/wsl_stack.py"
    $raw=(Invoke-ODSWslCommand $Identity @('python3',$program,"plan-$Action",$Identity.installRoot) | Out-String)
    if ($raw.Length -gt 65536) { throw 'Could not obtain the ordinary-owner lifecycle plan; no services were changed' }
    $plan=$raw | ConvertFrom-Json
    Assert-ODSWslStackPlan $Identity $Action $plan
    $agentAddress=$null
    if ($Action -eq 'start') {
        $agentAddress=Update-ODSWslAgentAddress $Identity
        if ($agentAddress.mode -eq 'wsl-nat-bridge') { Start-ODSWslAgentRelay $Identity }
        else { Stop-ODSWslAgentRelay $Identity }
    }
    $units=@($plan.nativeUnits)
    if ($Action -eq 'stop') {
        foreach ($unit in $units) { Invoke-ODSWslNativeUnit $Identity 'stop' $unit }
    }
    # Compose always executes as the ordinary Linux owner, never as root.
    Invoke-ODSWslCommand $Identity @('python3',$program,"compose-$Action",$Identity.installRoot)
    if ($Action -eq 'start') {
        if ($plan.hostAgentRestart -or ($agentAddress -and $agentAddress.changed)) { Invoke-ODSWslNativeUnit $Identity 'restart' 'ods-host-agent.service' }
        [Array]::Reverse($units)
        foreach ($unit in $units) { Invoke-ODSWslNativeUnit $Identity 'start' $unit }
    }
}

function Invoke-ODSWslLifecycle([string]$Action,[string]$Distro,[string]$InstallRoot,[switch]$ValidateOnly,[string]$DockerDesktopPath,[switch]$RetireRelay) {
    # Uninstall already supplies the installation's canonical distro. Never
    # resolve or start WSL while withdrawing Windows sign-in permission; an
    # unconfirmed stack command is checked only against an already running VM.
    if ($RetireRelay -and $Action -ne 'disable-startup') { throw 'RetireRelay is supported only for disable-startup' }
    if ($Action -eq 'disable-startup') { return Disable-ODSWslStartup (Get-ODSWslIdentity $Distro $InstallRoot) -ValidateOnly:$ValidateOnly -RetireRelay:$RetireRelay }
    # Repair an already-managed installation's Windows sign-in task without
    # rerunning the installer or entering WSL. Uses the canonical identity
    # directly; the distro need not be registered for this Windows-only repair.
    if ($Action -eq 'enable-startup') {
        if ($ValidateOnly) { throw 'ValidateOnly is supported only for disable-startup' }
        $identity=Get-ODSWslIdentity $Distro $InstallRoot
        Enable-ODSWslStartup $identity $DockerDesktopPath
        # Registration is Windows-only, even when WSL is unavailable. Do not
        # infer runtime health from a successfully registered sign-in task.
        $intent=Get-ODSWslStartupIntent $identity
        return [pscustomobject]@{
            scope='wsl-lifetime'; state='registered'; distroRunning=$null;
            identity=$identity; runtime=$null;
            startupEnabled=($null -ne $intent -and $intent.desiredRunning);
            startup=(Read-ODSWslJson (Join-Path $identity.directory 'startup-status.json'))
        }
    }
    if ($ValidateOnly) { throw 'ValidateOnly is supported only for disable-startup' }
    $Distro=Resolve-ODSWslRegisteredDistro $Distro
    $identity=Get-ODSWslIdentity $Distro $InstallRoot
    if ($Action -eq 'status') { return (Get-ODSWslLifetimeStatus $identity) }
    if ($script:ODSWslStateRoot) { Initialize-ODSPrivateDirectory $script:ODSWslStateRoot }
    Initialize-ODSPrivateDirectory $identity.directory
    $manifestPath=Join-Path $identity.directory 'instance.json'
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        Write-ODSPrivateBytes $manifestPath ([Text.UTF8Encoding]::new($false).GetBytes(($identity | ConvertTo-Json -Depth 8))) -CreateOnly
    }
    # Publish once, before waiting for a previous command. A newer stop must
    # never be overwritten when this start finally acquires command.lock.
    Set-ODSWslStartupIntent $identity ($Action -in @('start','restart'))
    $intent=Get-ODSWslStartupIntent $identity
    $lock=$null
    try {
        if ($Action -in @('start','restart')) {
            $script:ODSWslStartupIdentity=$identity
            $script:ODSWslStartupGeneration=$intent.generation
        }
        $script:ODSWslStartupDeadline=(Get-ODSWslUtcNow).AddMinutes(20)
        $lock=Open-ODSWslCommandLock $identity
        Assert-ODSWslCommandSettled $identity -MayStartDistribution:($Action -in @('start','restart'))
        Assert-ODSWslStartupStillWanted
        if ($Action -eq 'release') { return (Stop-ODSWslLifetime $identity) }
        if ($Action -in @('stop','restart')) {
            $status=Get-ODSWslLifetimeStatus $identity
            # A stopped distribution is never entered by stop.
            if ($status.distroRunning) {
                Invoke-ODSWslStack $identity 'stop' | ForEach-Object { [Console]::Error.WriteLine([string]$_) }
            }
            Stop-ODSWslAgentRelay $identity
            $status=Stop-ODSWslLifetime $identity
            if ($Action -eq 'stop') { return $status }
        }
        $status=Start-ODSWslLifetime $identity
        Assert-ODSWslStartupStillWanted
        if (Test-Path -LiteralPath (Join-Path $identity.directory 'startup-config.json')) {
            Enable-ODSWslStartup $identity
            $null=Assert-ODSWslStartupTask $identity
            Assert-ODSWslStartupStillWanted
            Enable-ScheduledTask -TaskName ($identity.taskName + '-Startup') | Out-Null
        }
        # Stack commands emit progress and an adapter receipt. Keep them on
        # stderr so the public success pipeline contains one lifetime result.
        Invoke-ODSWslStack $identity 'start' | ForEach-Object { [Console]::Error.WriteLine([string]$_) }
        Assert-ODSWslStartupStillWanted
        Get-ODSWslLifetimeStatus $identity
    } finally {
        $script:ODSWslStartupIdentity=$null; $script:ODSWslStartupGeneration=$null; $script:ODSWslStartupDeadline=$null
        if ($lock) { $lock.Dispose() }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
    if ($Action -eq 'hold') { Invoke-ODSWslHolder $InstanceDirectory }
    elseif ($Action -eq 'relay-hold') { Invoke-ODSWslRelayHolder $InstanceDirectory }
    elseif ($Action -eq 'autostart') { Invoke-ODSWslStartup $InstanceDirectory }
    else { Invoke-ODSWslLifecycle $Action $Distro $InstallRoot -DockerDesktopPath $DockerDesktopPath -ValidateOnly:$ValidateOnly -RetireRelay:$RetireRelay | ConvertTo-Json -Depth 8 }
}
