# Isolated regressions for 0/1/N Docker resources on Windows PowerShell 5.1/7.
# Load selected function definitions only. Docker, host services, scheduled
# tasks and install removal are mocks; the CLI entry point is never invoked.
[CmdletBinding()]
param([string]$CliPath)

$ErrorActionPreference = 'Stop'
if (-not $CliPath) { $CliPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'installers/windows/ods.ps1' }
$tokens = $null; $parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($CliPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw $parseErrors[0] }
foreach ($name in @('Test-ODSArgumentPresent', 'Test-ODSDockerRunningQuiet',
    'Get-ODSDockerProjectResourceNames', 'Remove-ODSDockerProjectByLabel', 'Invoke-Uninstall')) {
    $definition = $ast.Find({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    }, $true)
    if ($null -eq $definition) { throw "Missing function: $name" }
    . ([scriptblock]::Create($definition.Extent.Text))
}

$script:Cases = 0
$fixtureRoot = Join-Path ([IO.Path]::GetTempPath()) ('ods-uninstall-mock-' + [guid]::NewGuid().ToString('N'))
$fixtureInstall = Join-Path $fixtureRoot 'install'
$null = New-Item -ItemType Directory -Path $fixtureInstall
$sentinel = Join-Path $fixtureInstall 'sentinel.txt'
[IO.File]::WriteAllText($sentinel, 'This fixture must never be removed by an uninstall function.')
$script:ODS_AGENT_TASK_NAME = 'fixture-agent'
$script:ODS_MODEL_UPGRADE_TASK_NAME = 'fixture-upgrade'
$script:LEMONADE_TASK_NAME = 'fixture-lemonade'
$script:OPENCODE_TASK_NAME = 'fixture-opencode'

function Assert-True { param($Condition, [string]$Message) if (-not $Condition) { throw $Message } }
function Reset-Fixture {
    param([int]$Count = 0)
    $script:InstallDir = $fixtureInstall
    $script:Resources = @{
        container = @(@('c11111111111', 'c22222222222', 'c33333333333') | Select-Object -First $Count)
        network = @(@('af8748b74f7b', 'b22222222222', 'd33333333333') | Select-Object -First $Count)
        volume = @(@('ods_volume_one', 'ods_volume_two', 'ods_volume_three') | Select-Object -First $Count)
    }
    $script:Unrelated = @{ container = @('eeeeeeeeeeee'); network = @('7017a1db866f'); volume = @('other_volume') }
    $script:DockerCalls = New-Object 'System.Collections.Generic.List[object]'
    $script:Successes = New-Object 'System.Collections.Generic.List[string]'
    $script:HostCalls = New-Object 'System.Collections.Generic.List[string]'
    $script:FilesRemoved = $false
    $script:PreservedData = $false
    $script:PreservedModels = $false
    $script:DockerAvailable = $true
    $script:ComposeAvailable = $false
    $script:ComposeExit = 0
    $script:RetainKind = ''
    $script:RemovalExit = 0
    $script:FailQueryKind = ''
    $script:FailAfterRemoval = $false
    $script:ThrowOnQuery = $false
    $script:HadRemoval = $false
    $global:LASTEXITCODE = 0
}

function docker {
    $argv = @($args | ForEach-Object { [string]$_ })
    $script:DockerCalls.Add([pscustomobject]@{ Arguments = $argv })
    $global:LASTEXITCODE = 0
    if (($argv -join ' ') -eq 'info') {
        if (-not $script:DockerAvailable) { $global:LASTEXITCODE = 1 }
        return
    }
    if ($argv[0] -eq 'compose') { $global:LASTEXITCODE = $script:ComposeExit; return }
    $kind = $null
    if ($argv[0] -eq 'ps') { $kind = 'container'; $expected = 'ps -aq --filter label=com.docker.compose.project=ods' }
    elseif ($argv.Count -ge 2 -and $argv[1] -eq 'ls') { $kind = $argv[0]; $expected = "$kind ls -q --filter label=com.docker.compose.project=ods" }
    if ($kind) {
        if (($argv -join ' ') -ne $expected -or -not $script:Resources.ContainsKey($kind)) { throw 'Unexpected unscoped Docker enumeration' }
        if ($kind -eq $script:FailQueryKind -and (-not $script:FailAfterRemoval -or $script:HadRemoval)) {
            if ($script:ThrowOnQuery) { throw 'Mock Docker query transport failure' }
            # A nonzero command may have printed partial output; it is not an
            # authoritative inventory and must never authorize file removal.
            $global:LASTEXITCODE = 1
            foreach ($item in $script:Resources[$kind]) { Write-Output $item }
            return
        }
        foreach ($item in $script:Resources[$kind]) { Write-Output $item }
        Write-Output '  '
        return
    }
    if ($argv.Count -ge 2 -and $argv[0] -eq 'rm' -and $argv[1] -eq '-f') { $kind = 'container'; $firstId = 2 }
    elseif ($argv.Count -ge 2 -and $argv[1] -eq 'rm' -and $argv[0] -in @('network', 'volume')) { $kind = $argv[0]; $firstId = 2 }
    else { throw 'Unexpected Docker operation in isolated uninstall test' }
    if ($argv.Count -le $firstId) { throw 'Docker removal was called without resources' }
    $script:HadRemoval = $true
    foreach ($id in $argv[$firstId..($argv.Count - 1)]) {
        # Docker accepts ID prefixes. A character-splatted singleton could
        # therefore remove an unrelated network, as in the real regression.
        $matchingResources = @(@($script:Resources[$kind]) + @($script:Unrelated[$kind]) | Where-Object {
            if ($kind -eq 'volume') { $_ -eq $id } else { $_.StartsWith($id, [StringComparison]::Ordinal) }
        })
        if ($matchingResources.Count -ne 1) { $global:LASTEXITCODE = 1; continue }
        if ($kind -eq $script:RetainKind) { $global:LASTEXITCODE = $script:RemovalExit; continue }
        $matched = $matchingResources[0]
        $script:Resources[$kind] = @($script:Resources[$kind] | Where-Object { $_ -ne $matched })
        $script:Unrelated[$kind] = @($script:Unrelated[$kind] | Where-Object { $_ -ne $matched })
    }
}
Assert-True ((Get-Command docker).CommandType -eq 'Function') 'Docker mock is not the resolved command; refusing to run'

function Write-AI { param($Message) $null = $Message }
function Write-AIWarn { param($Message) $null = $Message }
function Write-AIError { param($Message) $null = $Message }
function Write-AISuccess { param($Message) $script:Successes.Add([string]$Message) }
function Invoke-Agent { param($Action) $script:HostCalls.Add('agent:' + $Action) }
function Stop-ODSOpenCodeRuntime { $script:HostCalls.Add('opencode') }
function Get-NativeInferenceBackend { return 'none' }
function Stop-NativeInferenceServer { throw 'Unexpected native inference call' }
function Stop-ScheduledTask { [CmdletBinding()] param($TaskName) $script:HostCalls.Add('stop-task:' + $TaskName) }
function Unregister-ScheduledTask {
    [CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact='None')]
    param($TaskName)
    if ($PSCmdlet.ShouldProcess($TaskName, 'Record mocked task removal')) { $script:HostCalls.Add('unregister-task:' + $TaskName) }
}
function Get-ComposeFlags { return @('-f', 'fixture-compose.yml') }
function Test-ODSComposeFlagsFilesAvailable { param($ComposeFlags) $null = $ComposeFlags; return $script:ComposeAvailable }
function Assert-ODSInstallDirSafeForRemoval {
    Assert-True ($InstallDir -eq $fixtureInstall) 'A test attempted to target a real install directory'
}
function Remove-ODSInstallDirectory {
    param([switch]$KeepData, [switch]$KeepModels)
    $script:FilesRemoved = $true
    $script:PreservedData = [bool]$KeepData
    $script:PreservedModels = [bool]$KeepModels
}
function Assert-UnrelatedIntact {
    Assert-True (($script:Unrelated.container -join ',') -eq 'eeeeeeeeeeee') 'Unrelated container was removed by an ID prefix'
    Assert-True (($script:Unrelated.network -join ',') -eq '7017a1db866f') 'Unrelated network was removed by a character ID prefix'
    Assert-True (($script:Unrelated.volume -join ',') -eq 'other_volume') 'Unrelated volume was removed'
    Assert-True (Test-Path -LiteralPath $sentinel) 'The uninstall executed real fixture deletion instead of its mock'
}
function Assert-UninstallFailure {
    param([string]$Expected)
    $caught = $null
    try { Invoke-Uninstall -UninstallArgs @('--force') } catch { $caught = $_.Exception.Message }
    Assert-True ($caught -and $caught.Contains($Expected)) "Expected $Expected; got $caught"
    Assert-True (-not $script:FilesRemoved) 'Failed cleanup authorized runtime file removal'
    Assert-True (@($script:Successes | Where-Object { $_ -like 'ODS uninstalled*' }).Count -eq 0) 'Failed cleanup reported uninstall success'
    Assert-UnrelatedIntact
    $script:Cases++
}

try {
    foreach ($count in @(0, 1, 3)) {
        foreach ($kind in @('container', 'network', 'volume')) {
            Reset-Fixture -Count $count
            $actual = @(Get-ODSDockerProjectResourceNames -Kind $kind)
            Assert-True ($actual.Count -eq $count) "Wrong $kind count for $count resources"
            Assert-True (($actual -join ',') -eq ($script:Resources[$kind] -join ',')) "Enumeration changed $kind IDs"
            $script:Cases++
        }
        foreach ($removeVolumes in @($false, $true)) {
            Reset-Fixture -Count $count
            $expected = @{}
            foreach ($kind in @('container', 'network', 'volume')) { $expected[$kind] = @($script:Resources[$kind]) }
            Remove-ODSDockerProjectByLabel -RemoveVolumes:$removeVolumes
            Assert-UnrelatedIntact
            foreach ($kind in @('container', 'network', 'volume')) {
                $prefix = if ($kind -eq 'container') { 'rm -f' } else { "$kind rm" }
                $calls = @($script:DockerCalls | Where-Object { ($_.Arguments[0..1] -join ' ') -eq $prefix })
                $shouldRemove = $count -gt 0 -and ($kind -ne 'volume' -or $removeVolumes)
                Assert-True ($calls.Count -eq [int]$shouldRemove) "Wrong removal count for $kind/$count"
                if ($shouldRemove) {
                    $actualIds = @($calls[0].Arguments | Select-Object -Skip 2)
                    Assert-True (($actualIds -join ',') -eq ($expected[$kind] -join ',')) "$kind IDs were split or altered during splatting"
                }
            }
            $script:Cases++
        }
    }
    foreach ($count in @(0, 1, 3)) {
        Reset-Fixture -Count $count
        Invoke-Uninstall -UninstallArgs @('--force')
        Assert-True $script:FilesRemoved "Successful fallback did not reach file cleanup for $count resources"
        Assert-True (@($script:Successes | Where-Object { $_ -like 'ODS uninstalled*' }).Count -eq 1) 'Missing final success'
        Assert-UnrelatedIntact
        $script:Cases++
    }
    foreach ($keep in @('--keep-data', '--keep-models')) {
        Reset-Fixture -Count 1
        Invoke-Uninstall -UninstallArgs @('--force', $keep)
        Assert-True ($script:Resources.volume.Count -eq 1) 'Keep option removed a Docker volume'
        Assert-True ($script:PreservedData -eq ($keep -eq '--keep-data') -and $script:PreservedModels -eq ($keep -eq '--keep-models')) 'Keep option was not forwarded'
        Assert-UnrelatedIntact
        $script:Cases++
    }
    Reset-Fixture -Count 1
    $script:Resources.container = @(); $script:Resources.volume = @()
    $script:InstallDir = Join-Path $fixtureRoot 'absent-install'
    Invoke-Uninstall -UninstallArgs @('--force')
    Assert-True ($script:Resources.network.Count -eq 0) 'Orphan singleton network was ignored because the install directory was absent'
    Assert-UnrelatedIntact
    $script:Cases++

    Reset-Fixture -Count 1
    $script:ComposeAvailable = $true
    $script:Resources.container = @()
    Invoke-Uninstall -UninstallArgs @('--force')
    Assert-True ($script:Resources.network.Count -eq 0 -and $script:Resources.volume.Count -eq 0) 'Compose success skipped remaining network/volume cleanup'
    Assert-UnrelatedIntact
    $script:Cases++

    foreach ($exitCode in @(0, 1)) {
        Reset-Fixture -Count 1
        $script:RetainKind = 'network'; $script:RemovalExit = $exitCode
        Assert-UninstallFailure 'ODS_UNINSTALL_DOCKER_CLEANUP_INCOMPLETE'
        Assert-True ($script:Resources.network.Count -eq 1) 'Expected the singleton network to remain in the negative fixture'
    }
    foreach ($kind in @('container', 'network', 'volume')) {
        foreach ($throwQuery in @($false, $true)) {
            Reset-Fixture -Count 1
            $script:FailQueryKind = $kind; $script:ThrowOnQuery = $throwQuery
            Assert-UninstallFailure 'ODS_UNINSTALL_DOCKER_QUERY_FAILED'
            Assert-True ($script:HostCalls.Count -eq 0 -and -not $script:HadRemoval) 'Inventory failure changed host/Docker state'
        }
    }
    Reset-Fixture -Count 1
    $script:FailQueryKind = 'network'; $script:FailAfterRemoval = $true
    Assert-UninstallFailure 'ODS_UNINSTALL_DOCKER_QUERY_FAILED'

    Reset-Fixture
    $script:DockerAvailable = $false
    Assert-UninstallFailure 'ODS_UNINSTALL_DOCKER_UNAVAILABLE'
    Write-Host "[PASS] $script:Cases isolated Windows uninstall cases; complete IDs, unrelated resources and failure-before-file-removal verified"
} finally {
    # Only the directory created above is deleted; never an installation path.
    $resolved = [IO.Path]::GetFullPath($fixtureRoot)
    $temporary = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($temporary, [StringComparison]::OrdinalIgnoreCase) -or
        (Split-Path -Leaf $resolved) -notlike 'ods-uninstall-mock-*') { throw 'Unsafe fixture cleanup path' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
