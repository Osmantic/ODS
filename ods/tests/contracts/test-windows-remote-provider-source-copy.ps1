param([switch]$AllowMissingDocker)
$ErrorActionPreference = 'Stop'
$odsRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
. (Join-Path $odsRoot 'installers\windows\lib\service-plan.ps1')
. (Join-Path $odsRoot 'installers\windows\lib\remote-provider-source-copy.ps1')
. (Join-Path $odsRoot 'installers\windows\lib\remote-provider-docker-transaction.ps1')
try {
    $docker = Invoke-ODSRemoteDockerCommand @('info', '--format', '{{.OSType}}')
    if ($docker.Code -ne 0 -or $docker.Output.Trim() -cne 'linux') { throw 'Linux Docker unavailable' }
} catch {
    if (-not $AllowMissingDocker) { throw }
    Write-Output '[SKIP] Actual native Windows copy contract requires a Linux Docker engine; no install acceptance recorded'
    return
}
$tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
$testRoot = Join-Path $tempBase ('ods-remote-copy-' + [guid]::NewGuid().ToString('N'))
if (-not [IO.Path]::GetFullPath($testRoot).StartsWith($tempBase, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Unsafe test root'
}
$ids = @('remote-provider-egress', 'remote-provider-ssh-tunnel')
$fixtureInstalls = [Collections.Generic.List[string]]::new()
$phase = [IO.File]::ReadAllText((Join-Path $odsRoot 'installers\windows\phases\06-directories.ps1'))
$start = $phase.IndexOf('# Copy source tree (skip if running in-place).')
$end = $phase.IndexOf('# Copy extensions library', $start)
if ($start -lt 0 -or $end -le $start) { throw 'Cannot isolate actual phase06 source copy' }
$copyBlock = [scriptblock]::Create($phase.Substring($start, $end - $start))

function Assert-Copy([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
function New-CopyFixture([string]$Name, [string]$Transport = '') {
    $caseRoot = Join-Path $testRoot $Name
    $source = Join-Path $caseRoot 'source'
    $install = Join-Path $caseRoot 'install'
    $fixtureInstalls.Add($install)
    foreach ($id in $ids) {
        $sourceDir = Join-Path $source "extensions\services\$id"
        New-Item -ItemType Directory -Path $sourceDir -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $install "extensions\services\$id") -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $odsRoot "extensions\services\$id\compose.yaml.disabled") -Destination $sourceDir
    }
    $lib = Join-Path $source 'installers\windows\lib'
    New-Item -ItemType Directory -Path $lib -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $odsRoot 'installers\windows\lib\remote-provider-source-copy.ps1') -Destination $lib
    foreach ($helper in @('remote-provider-docker-transaction.ps1', 'remote-provider-transaction.py')) {
        Copy-Item -LiteralPath (Join-Path $odsRoot "installers\windows\lib\$helper") -Destination $lib
    }
    $scripts = Join-Path $source 'scripts'
    New-Item -ItemType Directory -Path $scripts -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $odsRoot 'scripts\remote-provider-compose-selection.py') -Destination $scripts
    $control = Join-Path $source 'extensions\services\unrelated'
    New-Item -ItemType Directory -Path $control -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $control 'compose.yaml.disabled'), 'unrelated-copy-control')
    $routeDir = Join-Path $install 'data\remote-provider'
    New-Item -ItemType Directory -Path $routeDir -Force | Out-Null
    if ($Transport) {
        $route = @{ schema = 'ods.remote-routing-state.v1'; enabled = $true; provider = @{ transport = $Transport } }
        [IO.File]::WriteAllText((Join-Path $routeDir 'routing-state.json'), ($route | ConvertTo-Json))
    }
    return @{ Source = $source; Install = $install }
}
function Invoke-ActualCopy($Case) {
    & {
        param($sourceRoot, $installDir, $block)
        function Write-AI { param($Message) }
        function Write-AISuccess { param($Message) }
        function Write-AIWarn { param($Message) }
        function Write-AIError { param($Message) }
        & $block
    } $Case.Source $Case.Install $copyBlock
}
function Assert-Recipes($Case, [bool]$Egress, [bool]$Tunnel) {
    $expected = @($Egress, $Tunnel)
    for ($i = 0; $i -lt $ids.Count; $i++) {
        $dir = Join-Path $Case.Install "extensions\services\$($ids[$i])"
        $name = if ($expected[$i]) { 'compose.yaml' } else { 'compose.yaml.disabled' }
        $other = if ($expected[$i]) { 'compose.yaml.disabled' } else { 'compose.yaml' }
        Assert-Copy (-not (Test-Path -LiteralPath (Join-Path $dir $other))) 'Opposite marker must remain absent'
        $got = [IO.File]::ReadAllBytes((Join-Path $dir $name))
        $want = [IO.File]::ReadAllBytes((Join-Path $Case.Source "extensions\services\$($ids[$i])\compose.yaml.disabled"))
        Assert-Copy ([Convert]::ToBase64String($got) -ceq [Convert]::ToBase64String($want)) 'Recipe must match source exactly'
    }
}

try {
    foreach ($transport in @('direct', 'ssh', '')) {
        $label = if ($transport) { $transport } else { 'fresh-core' }
        $case = New-CopyFixture $label $transport
        $routePath = Join-Path $case.Install 'data\remote-provider\routing-state.json'
        $beforeRoute = if ($transport) { [IO.File]::ReadAllText($routePath) } else { '' }
        Invoke-ActualCopy $case
        Assert-Recipes $case ([bool]$transport) ($transport -eq 'ssh')
        Assert-Copy (([IO.File]::ReadAllText((Join-Path $case.Install 'extensions\services\unrelated\compose.yaml.disabled'))) -ceq 'unrelated-copy-control') 'Exclusions must not suppress other services'
        if ($transport) { Assert-Copy ([IO.File]::ReadAllText($routePath) -ceq $beforeRoute) 'Route bytes changed' }
        Invoke-ActualCopy $case
        Assert-Recipes $case ([bool]$transport) ($transport -eq 'ssh')
    }

    $case = New-CopyFixture 'genuinely-disabled' 'direct'
    $disabled = Join-Path $case.Install 'extensions\services\remote-provider-egress\compose.yaml.disabled'
    Copy-Item -LiteralPath (Join-Path $case.Source 'extensions\services\remote-provider-egress\compose.yaml.disabled') -Destination $disabled
    $failed = $false
    try { Invoke-ActualCopy $case } catch { $failed = $true }
    Assert-Copy $failed 'A real disabled marker plus active route must fail even if canonical'
    Assert-Copy (-not (Test-Path -LiteralPath (Join-Path $case.Install 'extensions\services\remote-provider-ssh-tunnel\compose.yaml.disabled'))) 'No marker may publish after invalid preflight'

    $case = New-CopyFixture 'old-selected'
    [IO.File]::WriteAllText((Join-Path $case.Install 'extensions\services\remote-provider-egress\compose.yaml'), 'old active recipe')
    Invoke-ActualCopy $case
    Assert-Recipes $case $true $false
    # Owner changes after reconciliation must win on the next copy.
    $active = Join-Path $case.Install 'extensions\services\remote-provider-egress\compose.yaml'
    Move-Item -LiteralPath $active -Destination "$active.disabled"
    Invoke-ActualCopy $case
    Assert-Recipes $case $false $false

    $case = New-CopyFixture 'second-source-invalid' 'ssh'
    Remove-Item -LiteralPath (Join-Path $case.Source 'extensions\services\remote-provider-ssh-tunnel\compose.yaml.disabled')
    $failed = $false
    try { Sync-ODSWindowsRemoteProviderRecipes -InstallDir $case.Install -SourceRoot $case.Source } catch { $failed = $true }
    Assert-Copy $failed 'Both source recipes must validate before any publication'
    Assert-Copy (-not (Test-Path -LiteralPath (Join-Path $case.Install 'extensions\services\remote-provider-egress\compose.yaml'))) 'First SSH marker must not publish on invalid second source'

    $case = New-CopyFixture 'source-active-marker'
    [IO.File]::WriteAllText((Join-Path $case.Source 'extensions\services\remote-provider-egress\compose.yaml'), 'unexpected active source recipe')
    $failed = $false
    try { Invoke-ActualCopy $case } catch { $failed = $true }
    Assert-Copy $failed 'Noncanonical source selection must fail closed'
    Assert-Copy (-not (Test-Path -LiteralPath (Join-Path $case.Install 'extensions\services\remote-provider-egress\compose.yaml'))) 'Broad copy must exclude active markers too'
    Assert-Copy (-not (Test-Path -LiteralPath (Join-Path $case.Install 'extensions\services\remote-provider-egress\compose.yaml.disabled'))) 'Broad copy must exclude disabled markers too'

    foreach ($linkCase in @('lock', 'source', 'target')) {
        $case = New-CopyFixture ('hardlink-' + $linkCase)
        $custody = Join-Path (Split-Path -Parent $case.Install) 'unrelated-custody'
        [IO.File]::WriteAllText($custody, '')
        $linked = switch ($linkCase) {
            'lock' { Join-Path $case.Install 'data\.extensions-lock' }
            'source' { Join-Path $case.Source 'extensions\services\remote-provider-egress\compose.yaml.disabled' }
            'target' { Join-Path $case.Install 'extensions\services\remote-provider-egress\compose.yaml' }
        }
        if (Test-Path -LiteralPath $linked) { Remove-Item -LiteralPath $linked }
        New-Item -ItemType HardLink -Path $linked -Target $custody | Out-Null
        $failed = $false
        try { Sync-ODSWindowsRemoteProviderRecipes -InstallDir $case.Install -SourceRoot $case.Source } catch { $failed = $true }
        Assert-Copy $failed "Hardlinked $linkCase must be rejected"
        Assert-Copy ([IO.File]::ReadAllBytes($custody).Length -eq 0) 'Hardlink custody bytes must remain untouched'
    }

    $case = New-CopyFixture 'in-place'
    $case.Install = $case.Source
    New-Item -ItemType Directory -Path (Join-Path $case.Install 'data\remote-provider') -Force | Out-Null
    Invoke-ActualCopy $case
    Assert-Recipes $case $false $false
    $case.Source += '\' # Same directory with a trailing separator is in-place too.
    Invoke-ActualCopy $case
    Assert-Recipes $case $false $false
    $inPlaceRoute = Join-Path $case.Install 'data\remote-provider\routing-state.json'
    [IO.File]::WriteAllText($inPlaceRoute, '{"schema":"ods.remote-routing-state.v1","enabled":true,"provider":{"transport":"ssh"}}')
    $message = ''
    try { Invoke-ActualCopy $case } catch { $message = $_.Exception.Message }
    Assert-Copy ($message -like '*in-place source update*trusted backup*fresh install directory*') 'Ambiguous in-place migration must give an explicit recovery instruction'
    Assert-Recipes $case $false $false

    $case = New-CopyFixture 'late-library-choice'
    Sync-ODSWindowsRemoteProviderRecipes -InstallDir $case.Install -SourceRoot $case.Source
    foreach ($name in @('docker-compose.base.yml', 'docker-compose.override.yml')) {
        [IO.File]::WriteAllText((Join-Path $case.Install $name), 'services: {}')
    }
    $stale = @('-f', 'docker-compose.base.yml', '-f', 'docker-compose.override.yml')
    # Library Add after the installer scanned the service plan.
    Invoke-ODSWindowsExtensionGraphLock -InstallDir $case.Install -Action {
        param($install)
        $active = Join-Path $install 'extensions\services\remote-provider-egress\compose.yaml'
        Move-Item -LiteralPath "$active.disabled" -Destination $active
    }
    $fresh = @(Write-ODSWindowsRemoteProviderComposeFlags -InstallDir $case.Install -SourceRoot $case.Source -ComposeFlags $stale)
    Assert-Copy (($fresh -join '|') -ceq '-f|docker-compose.base.yml|-f|extensions/services/remote-provider-egress/compose.yaml|-f|docker-compose.override.yml') 'Late Add must appear before the user override'
    $stale = $fresh
    # Library Disable after that snapshot invalidates the previous cache.
    Invoke-ODSWindowsExtensionGraphLock -InstallDir $case.Install -Action {
        param($install)
        $active = Join-Path $install 'extensions\services\remote-provider-egress\compose.yaml'
        Move-Item -LiteralPath $active -Destination "$active.disabled"
        Remove-Item -LiteralPath (Join-Path $install '.compose-flags')
    }
    $fresh = @(Write-ODSWindowsRemoteProviderComposeFlags -InstallDir $case.Install -SourceRoot $case.Source -ComposeFlags $stale)
    Assert-Copy (($fresh -join '|') -ceq '-f|docker-compose.base.yml|-f|docker-compose.override.yml') 'Late Disable must remove the stale remote path'
    Assert-Copy ([IO.File]::ReadAllText((Join-Path $case.Install '.compose-flags')) -ceq ($fresh -join ' ')) 'Published cache must match the fresh selection'
    Write-Output '[PASS] actual Windows robocopy plus Docker-serialized recipes and selection-aware flags'
} finally {
    # Expected rejection cases deliberately retain an exited transaction
    # container. Remove only our exact verified terminal fixture writers.
    $image = Invoke-ODSRemoteDockerCommand @('image', 'inspect', 'python:3.11-slim@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e', '--format', '{{.Id}}')
    if ($image.Code -ne 0) { throw 'Cannot verify fixture writer image for cleanup' }
    foreach ($fixture in $fixtureInstalls) {
        $hasher = [Security.Cryptography.SHA256]::Create()
        try { $identity = -join ($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($fixture.ToUpperInvariant())) | ForEach-Object { $_.ToString('x2') }) }
        finally { $hasher.Dispose() }
        $writer = Get-ODSRemoteWriter -Name ('ods-windows-remote-' + $identity.Substring(0, 24)) -RootHash $identity -ImageId $image.Output.Trim()
        if ($null -ne $writer) {
            if ($writer.State.Running -or $writer.State.Status -cne 'exited') { throw 'Fixture writer is not terminal; preserving fixture files' }
            $removed = Invoke-ODSRemoteDockerCommand @('rm', $writer.Id)
            if ($removed.Code -ne 0) { throw 'Could not remove terminal fixture writer' }
        }
    }
    if (-not [IO.Path]::GetFullPath($testRoot).StartsWith($tempBase, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe cleanup root' }
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
