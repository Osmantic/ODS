$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\installers\windows\lib\compose-project-ownership.ps1')

$script:root = Join-Path $env:TEMP 'ods-compose-ownership-fixture'
$script:ids = @(('a' * 64), ('b' * 64), ('c' * 64))
$script:containers = @()
$script:inspectFails = $false
$script:projectScanFails = $false
$script:nameScanFails = $false
$script:messages = @()
$script:removed = @()
$script:removeFails = $false
$script:projectScans = 0
$script:flipOnSecondScan = $false
$script:lastConfig = ''

function Write-AIError { param([string]$Message) $script:messages += $Message }
function Check { param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}
function New-FixtureContainer {
    param([string]$Id, [string]$Name, [string]$Project = 'ods',
          [string]$WorkingDir = $script:root, [string]$ConfigFiles = '',
          [string]$State = 'running')
    if (-not $ConfigFiles) { $ConfigFiles = Join-Path $WorkingDir 'docker-compose.base.yml' }
    return [pscustomobject]@{
        Id = $Id; Name = $Name; Project = $Project
        WorkingDir = $WorkingDir; ConfigFiles = $ConfigFiles; State = $State
    }
}
function docker {
    $argv = @($args)
    if ($argv[0] -eq '--config') {
        $script:lastConfig = $argv[1]
        $argv = @($argv[2..($argv.Count - 1)])
    }
    $global:LASTEXITCODE = 0
    if ($argv[0] -eq 'ps') {
        if ($argv -contains 'label=com.docker.compose.project=ods') {
            $script:projectScans++
            if ($script:flipOnSecondScan -and $script:projectScans -eq 2) {
                $script:containers += New-FixtureContainer -Id $script:ids[1] -Name 'ods-litellm'
            }
            if ($script:projectScanFails) { $global:LASTEXITCODE = 1; return }
            $script:containers | Where-Object { $_.Project -eq 'ods' } |
                ForEach-Object { $_.Id }
            return
        }
        if ($argv -contains 'name=^ods-') {
            if ($script:nameScanFails) { $global:LASTEXITCODE = 1; return }
            $script:containers | Where-Object { $_.Name -like 'ods-*' } |
                ForEach-Object { $_.Id }
            return
        }
    }
    if ($argv[0] -eq 'container' -and $argv[1] -eq 'inspect') {
        if ($script:inspectFails) { $global:LASTEXITCODE = 1; return }
        $item = @($script:containers | Where-Object { $_.Id -eq $argv[2] })
        if ($item.Count -ne 1) { $global:LASTEXITCODE = 1; return }
        $c = $item[0]
        [pscustomobject]@{
            Id = $c.Id; Name = '/' + $c.Name
            State = @{ Status = $c.State }
            Config = @{ Labels = @{
                'com.docker.compose.project' = $c.Project
                'com.docker.compose.project.working_dir' = $c.WorkingDir
                'com.docker.compose.project.config_files' = $c.ConfigFiles
            } }
        } | ConvertTo-Json -Depth 6 -Compress
        return
    }
    if ($argv[0] -eq 'rm' -and $argv[1] -eq '-f') {
        $script:removed += @($argv[2..($argv.Count - 1)])
        if ($script:removeFails) { $global:LASTEXITCODE = 1 }
        return
    }
    throw "Unexpected Docker call: $($argv -join ' ')"
}
function Expect-Blocked { param([string]$Message, [string]$Reason)
    $blocked = $false
    $script:messages = @()
    try { $null = @(Assert-ODSWindowsComposeContainerOwnership -InstallDir $script:root) }
    catch { $blocked = ($_.Exception.Message -eq 'ODS_INSTALL_ABORTED') }
    Check ($blocked -and (@($script:messages | Where-Object { $_ -like "*$Reason*" }).Count -gt 0)) $Message
}

Check (@(Assert-ODSWindowsComposeContainerOwnership -InstallDir $script:root).Count -eq 0) `
    'an empty Docker engine is allowed'
$script:containers = @(
    (New-FixtureContainer -Id $script:ids[0] -Name 'ods-dashboard'),
    (New-FixtureContainer -Id $script:ids[1] -Name 'ods-litellm' -WorkingDir ($script:root.ToUpperInvariant()))
)
$owned = @(Assert-ODSWindowsComposeContainerOwnership -InstallDir $script:root)
Check ($owned.Count -eq 2 -and $owned -contains $script:ids[0] -and
       $owned -contains $script:ids[1]) 'same-install containers return exact owned IDs'
$script:containers = @(
    (New-FixtureContainer -Id $script:ids[0] -Name 'ods-dashboard' `
        -ConfigFiles ((Join-Path $script:root 'docker-compose.base.yml') + ',' +
                      (Join-Path $script:root 'docker-compose.windows.yml')))
)
Check (@(Assert-ODSWindowsComposeContainerOwnership -InstallDir $script:root).Count -eq 1) `
    'multiple same-root Compose config files are allowed'
$null = @(Assert-ODSWindowsComposeContainerOwnership -InstallDir $script:root `
    -DockerClientArgs @('--config', 'C:\fixture-docker-config'))
Check ($script:lastConfig -eq 'C:\fixture-docker-config') `
    'the selected Docker client config is used for ownership inspection'

$script:containers = @(
    (New-FixtureContainer -Id $script:ids[0] -Name 'ods-dashboard' `
        -WorkingDir '/home/michael/ods' -ConfigFiles '/home/michael/ods/docker-compose.base.yml')
)
Expect-Blocked 'a running foreign WSL project is blocked even with free ports' 'belongs to'
$script:removed = @()
$blockedCleanup = $false
try { $null = Clear-ODSWindowsOwnedStaleContainers -InstallDir $script:root }
catch { $blockedCleanup = ($_.Exception.Message -eq 'ODS_INSTALL_ABORTED') }
Check ($blockedCleanup -and $script:removed.Count -eq 0) `
    'a foreign WSL project is never passed to docker rm'
$script:containers[0].State = 'exited'
Expect-Blocked 'a stopped foreign WSL project is also blocked' 'belongs to'

$script:containers = @(
    (New-FixtureContainer -Id $script:ids[0] -Name 'ods-dashboard' -Project 'other')
)
Expect-Blocked 'an ods-* name without the expected project label is not removed' 'ownership labels'

$script:containers = @(
    (New-FixtureContainer -Id $script:ids[0] -Name 'custom-dashboard' `
        -WorkingDir '/home/michael/ods' -ConfigFiles '/home/michael/ods/docker-compose.base.yml')
)
Expect-Blocked 'a custom-named container in the foreign ods project is blocked' 'belongs to'

$script:containers = @(
    (New-FixtureContainer -Id $script:ids[0] -Name 'ods-dashboard' `
        -ConfigFiles 'C:\outside\docker-compose.base.yml')
)
Expect-Blocked 'a same-root container with a foreign Compose config is blocked' 'outside'

$script:containers = @((New-FixtureContainer -Id $script:ids[0] -Name 'ods-dashboard'))
$script:inspectFails = $true
Expect-Blocked 'inspect failure does not authorize removal' 'could not inspect'
$script:inspectFails = $false
$script:projectScanFails = $true
Expect-Blocked 'project enumeration failure does not authorize removal' 'project enumeration failed'
$script:projectScanFails = $false
$script:nameScanFails = $true
Expect-Blocked 'name enumeration failure does not authorize removal' 'container-name enumeration failed'
$script:nameScanFails = $false

$script:containers = @((New-FixtureContainer -Id $script:ids[0] -Name 'ods-dashboard'))
$script:removed = @()
$removedCount = Clear-ODSWindowsOwnedStaleContainers -InstallDir $script:root
Check ($removedCount -eq 1 -and $script:removed.Count -eq 1 -and
       $script:removed[0] -eq $script:ids[0]) `
    'same-install cleanup removes only the exact inspected container ID'

$script:removed = @()
$script:removeFails = $true
$failedRemoval = $false
try { $null = Clear-ODSWindowsOwnedStaleContainers -InstallDir $script:root }
catch { $failedRemoval = ($_.Exception.Message -eq 'ODS_INSTALL_ABORTED') }
Check $failedRemoval 'failed owned-container cleanup aborts the install'
$script:removeFails = $false

$script:removed = @()
$script:projectScans = 0
$script:flipOnSecondScan = $true
$changedOwnership = $false
try { $null = Clear-ODSWindowsOwnedStaleContainers -InstallDir $script:root }
catch { $changedOwnership = ($_.Exception.Message -eq 'ODS_INSTALL_ABORTED') }
Check ($changedOwnership -and $script:removed.Count -eq 0) `
    'a changed owned-container set is never passed to docker rm'

'Windows Compose project ownership contract passed'
