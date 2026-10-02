function Invoke-ODSRemoteDockerCommand {
    param([Parameter(Mandatory = $true)][string[]]$Arguments,
          [ValidateRange(1, 600)][int]$TimeoutSeconds = 60)
    # ProcessStartInfo.ArgumentList is unavailable in Windows PowerShell 5.1.
    # Quote Windows argv directly; never pass these arguments through a shell.
    $quoted = foreach ($argument in $Arguments) {
        $value = [regex]::Replace($argument, '(\\*)"', '$1$1\"')
        '"' + [regex]::Replace($value, '(\\+)$', '$1$1') + '"'
    }
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = (Get-Command docker.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    $info.Arguments = $quoted -join ' '
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = [Diagnostics.Process]::Start($info)
    try {
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            # Terminate only our CLI client. A Docker writer may still be live;
            # its stable name must be reconciled before a later invocation.
            try { $process.Kill(); $null = $process.WaitForExit(5000) } catch { }
            throw 'Docker CLI timed out; transaction writer state remains unverified'
        }
        $process.WaitForExit()
        return [pscustomobject]@{ Code = $process.ExitCode; Output = $stdout.Result; Error = $stderr.Result }
    } finally { $process.Dispose() }
}

function Get-ODSRemoteWriter {
    param([string]$Name, [string]$RootHash, [string]$ImageId)
    $listed = Invoke-ODSRemoteDockerCommand @('ps', '-a', '--no-trunc', '--filter', "name=^${Name}$", '--format', '{{.ID}}')
    if ($listed.Code -ne 0) { throw "Cannot establish state of transaction writer $Name" }
    $ids = @($listed.Output -split '\s+' | Where-Object { $_ })
    if ($ids.Count -eq 0) { return $null }
    if ($ids.Count -ne 1 -or $ids[0] -notmatch '^[a-f0-9]{64}$') { throw 'Ambiguous transaction writer identity' }
    $observed = Invoke-ODSRemoteDockerCommand @('container', 'inspect', $ids[0])
    if ($observed.Code -ne 0) { throw "Cannot inspect transaction writer $Name" }
    $items = @($observed.Output | ConvertFrom-Json -ErrorAction Stop)
    if ($items.Count -ne 1) { throw 'Invalid transaction writer inspection' }
    $writer = $items[0]
    if ($writer.Id -cne $ids[0] -or $writer.Name -cne "/$Name" -or
        $writer.Image -cne $ImageId -or
        $writer.Config.Labels.'com.osmantic.ods.remote-transaction-root' -cne $RootHash -or
        (@($writer.Config.Entrypoint) -join '|') -cne 'python3' -or
        (@($writer.Config.Cmd) -join '|') -cne '/candidate/installers/windows/lib/remote-provider-transaction.py|--request-file') {
        throw "Transaction writer ownership mismatch: $Name"
    }
    return $writer
}

function Save-ODSRemoteWriterLog {
    param([string]$Id, [string]$Directory)
    $log = Invoke-ODSRemoteDockerCommand @('logs', $Id)
    if ($log.Code -ne 0) { throw 'Cannot preserve transaction writer logs' }
    $encoding = [Text.UTF8Encoding]::new($false)
    [IO.File]::WriteAllText((Join-Path $Directory "$Id.stdout.json"), $log.Output, $encoding)
    [IO.File]::WriteAllText((Join-Path $Directory "$Id.stderr.txt"), $log.Error, $encoding)
    return $log
}

function Invoke-ODSWindowsRemoteTransaction {
    param([Parameter(Mandatory = $true)][string]$InstallDir,
          [Parameter(Mandatory = $true)][string]$SourceRoot,
          [Parameter(Mandatory = $true)][hashtable]$Request)
    $install = Get-ODSRemoteRecipeDirectory $InstallDir
    $source = Get-ODSRemoteRecipeDirectory $SourceRoot
    $null = Get-ODSRemoteRecipeBytes (Join-Path $source 'installers\windows\lib\remote-provider-transaction.py')
    if ($Request.operation -cnotin @('sync', 'flags')) { throw 'Invalid remote-provider transaction operation' }
    $image = 'python:3.11-slim@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e'
    $info = Invoke-ODSRemoteDockerCommand @('info', '--format', '{{.OSType}}')
    if ($info.Code -ne 0 -or $info.Output.Trim() -cne 'linux') { throw 'Remote-provider transactions require the Linux Docker engine' }
    $imageInfo = Invoke-ODSRemoteDockerCommand @('image', 'inspect', $image, '--format', '{{.Id}}')
    if ($imageInfo.Code -ne 0) {
        $pull = Invoke-ODSRemoteDockerCommand -Arguments @('pull', $image) -TimeoutSeconds 600
        if ($pull.Code -ne 0) { throw 'Could not pull the pinned ODS Python base image' }
        $imageInfo = Invoke-ODSRemoteDockerCommand @('image', 'inspect', $image, '--format', '{{.Id}}')
    }
    $imageId = $imageInfo.Output.Trim()
    if ($imageInfo.Code -ne 0 -or $imageId -notmatch '^sha256:[a-f0-9]{64}$') { throw 'Cannot verify the transaction image' }
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $rootHash = -join ($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($install.TrimEnd('\', '/').ToUpperInvariant())) | ForEach-Object { $_.ToString('x2') }) }
    finally { $hash.Dispose() }
    $name = 'ods-windows-remote-' + $rootHash.Substring(0, 24)
    $stateParent = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ODS\install-transactions'
    $null = New-Item -ItemType Directory -Path $stateParent -Force
    $null = Get-ODSRemoteRecipeDirectory $stateParent
    $journal = Join-Path $stateParent ([guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $journal
    $null = Get-ODSRemoteRecipeDirectory $journal
    $encoding = [Text.UTF8Encoding]::new($false)
    [IO.File]::WriteAllText((Join-Path $journal 'request.json'), ($Request | ConvertTo-Json -Depth 8 -Compress), $encoding)
    [IO.File]::WriteAllText((Join-Path $journal 'writer-name.txt'), $name, $encoding)

    # A lost Docker CLI is not a completed writer. Reconcile the exact named
    # container before any retry; never steal its lock or kill a live writer.
    $writer = Get-ODSRemoteWriter -Name $name -RootHash $rootHash -ImageId $imageId
    if ($null -ne $writer) {
        $id = $writer.Id
        $deadline = [DateTime]::UtcNow.AddSeconds(30)
        while ($writer.State.Status -ceq 'running' -and -not $writer.State.Paused) {
            if ([DateTime]::UtcNow -ge $deadline) { throw "Transaction writer is still running: $name" }
            Start-Sleep -Milliseconds 100
            $writer = Get-ODSRemoteWriter -Name $name -RootHash $rootHash -ImageId $imageId
            if ($null -eq $writer -or $writer.Id -cne $id) { throw 'Transaction writer changed during reconciliation' }
        }
        if ($writer.State.Running -or $writer.State.Paused -or
            $writer.State.Status -cnotin @('exited', 'created')) {
            throw "Transaction writer state is unresolved: $name"
        }
        $null = Save-ODSRemoteWriterLog -Id $id -Directory $journal
        $removed = Invoke-ODSRemoteDockerCommand @('rm', $id)
        if ($removed.Code -ne 0) { throw "Cannot remove terminal transaction writer: $name" }
    }
    # All mutation occurs under Linux flock inside this bounded process. The
    # image has no network, Docker socket, capabilities, or writable rootfs.
    $arguments = @('run', '--name', $name, '--label', "com.osmantic.ods.remote-transaction-root=$rootHash",
        '--network', 'none', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '--pids-limit', '64', '--memory', '256m', '--env', 'PYTHONDONTWRITEBYTECODE=1',
        '--mount', "type=bind,source=$install,target=/ods",
        '--mount', "type=bind,source=$source,target=/candidate,readonly",
        '--mount', "type=bind,source=$journal,target=/transaction,readonly",
        '--entrypoint', 'python3', $image,
        '/candidate/installers/windows/lib/remote-provider-transaction.py', '--request-file')
    $run = Invoke-ODSRemoteDockerCommand $arguments
    $writer = Get-ODSRemoteWriter -Name $name -RootHash $rootHash -ImageId $imageId
    if ($null -eq $writer -or $writer.State.Running -or $writer.State.Paused -or $writer.State.Status -cne 'exited') {
        throw "Transaction completion is unverified; retain and inspect $name"
    }
    $log = Save-ODSRemoteWriterLog -Id $writer.Id -Directory $journal
    if ($run.Code -ne 0 -or $writer.State.ExitCode -ne 0) {
        throw "Remote-provider transaction failed; inspect $journal and $name"
    }
    $result = $log.Output | ConvertFrom-Json -ErrorAction Stop
    if ($result.schema -cne 'ods.windows-remote-transaction.v1' -or $result.operation -cne $Request.operation) {
        throw "Transaction receipt is invalid; retain and inspect $name"
    }
    $removed = Invoke-ODSRemoteDockerCommand @('rm', $writer.Id)
    if ($removed.Code -ne 0) { throw "Cannot remove completed transaction writer: $name" }
    return $result
}
