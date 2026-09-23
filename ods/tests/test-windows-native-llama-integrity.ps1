# Offline fixtures: inert ZIP bytes only; no installer/task/runtime is executed.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$repo = Split-Path -Parent $root
. (Join-Path $root 'installers/windows/lib/native-llama-artifact.ps1')
$script:checks = 0
function Assert-True {
    param([bool]$Value, [string]$Label)
    if (-not $Value) { throw $Label }
    $script:checks++
}
function Assert-Rejected {
    param([scriptblock]$Action, [string]$Pattern)
    $caught = $null
    try { $null = & $Action } catch { $caught = $_ }
    Assert-True ($null -ne $caught) "Expected rejection matching $Pattern"
    Assert-True (($caught.Exception.Message + ' ' + $caught.FullyQualifiedErrorId) -match $Pattern) "Wrong rejection: $caught"
}
$output = [System.IO.Path]::GetFullPath((Join-Path $repo 'output/native-llama-fixtures'))
$null = New-Item -ItemType Directory -Path $output -Force
$testRoot = Join-Path $output ('windows-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $testRoot
$oldTemp = $env:TEMP
$oldTmp = $env:TMP
try {
    $source = Join-Path $testRoot 'source'
    $sourceInstallers = Join-Path $source 'installers'
    $null = New-Item -ItemType Directory -Path $sourceInstallers -Force
    $manifest = Get-Content -LiteralPath (Join-Path $root 'installers/native-llama-artifacts.json') -Raw | ConvertFrom-Json
    foreach ($selection in @(@('windows-vulkan-x64', 'b8248'), @('windows-vulkan-x64', 'b9014'), @('macos-arm64', 'b8210'), @('macos-arm64', 'b9014'))) {
        $resolved = Resolve-ODSNativeLlamaArtifact -ManifestPath (Join-Path $root 'installers/native-llama-artifacts.json') -Platform $selection[0] -Tag $selection[1]
        Assert-True ($resolved.tag -ceq $selection[1]) 'Reviewed selection failed'
    }
    $fixtureDirectory = Join-Path $testRoot 'fixture'
    $null = New-Item -ItemType Directory -Path $fixtureDirectory
    [System.IO.File]::WriteAllText((Join-Path $fixtureDirectory 'llama-server.exe'), 'Inert bytes; never executed')
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $script:fixture = Join-Path $testRoot 'inert.zip'
    [System.IO.Compression.ZipFile]::CreateFromDirectory($fixtureDirectory, $script:fixture)
    $hash = (Get-FileHash -LiteralPath $script:fixture -Algorithm SHA256).Hash.ToLowerInvariant()
    foreach ($entry in $manifest.artifacts) {
        if ($entry.platform -ceq 'windows-vulkan-x64') { $entry.sha256 = $hash }
    }
    $manifestPath = Join-Path $sourceInstallers 'native-llama-artifacts.json'
    [System.IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 8))
    Assert-True (Test-ODSNativeLlamaArchive -Path $script:fixture -ExpectedSha256 $hash) 'Reviewed local bytes rejected'
    foreach ($badHash in @('', '1234', ('g' * 64), ($hash + "`n"))) {
        Assert-Rejected { Test-ODSNativeLlamaArchive -Path $script:fixture -ExpectedSha256 $badHash } 'valid reviewed'
    }
    Assert-Rejected { Test-ODSNativeLlamaArchive -Path $script:fixture -ExpectedSha256 ('0' * 64) } 'does not match'
    $empty = Join-Path $testRoot 'empty.zip'
    [System.IO.File]::WriteAllBytes($empty, [byte[]]@())
    Assert-Rejected { Test-ODSNativeLlamaArchive -Path $empty -ExpectedSha256 $hash } 'regular staged'
    Assert-Rejected { Test-ODSNativeLlamaArchive -Path $testRoot -ExpectedSha256 $hash } 'regular staged'
    Assert-Rejected { Test-ODSNativeLlamaArchive -Path (Join-Path $testRoot 'missing.zip') -ExpectedSha256 $hash } 'Cannot find path|PathNotFound|ItemNotFound'
    # Simulate reparse metadata without requiring the symbolic-link privilege.
    function Get-Item {
        [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidOverwritingBuiltInCmdlets', '', Justification='An isolated inert-file fixture simulates reparse metadata without requiring symlink privileges.')]
        param($LiteralPath)
        $null = $LiteralPath
        [pscustomobject]@{ PSIsContainer=$false; Length=50; Attributes=[System.IO.FileAttributes]::ReparsePoint }
    }
    Assert-Rejected { Test-ODSNativeLlamaArchive -Path $script:fixture -ExpectedSha256 $hash } 'regular staged'
    Remove-Item Function:Get-Item

    $temporary = Join-Path $testRoot 'temporary'
    $null = New-Item -ItemType Directory -Path $temporary
    $env:TEMP = $temporary
    $env:TMP = $temporary
    $oldCache = Join-Path $temporary 'llama-b8248-bin-win-vulkan-x64.zip'
    [System.IO.File]::WriteAllText($oldCache, 'untrusted old cache')
    $script:events = New-Object 'System.Collections.Generic.List[string]'
    $script:stages = New-Object 'System.Collections.Generic.List[string]'
    $script:mode = 'valid'
    # Load only the two real extraction definitions; never source an installer.
    foreach ($definition in @(@('ui.ps1', 'Invoke-ExtractionWithRetry'), @('detection.ps1', 'Test-ZipIntegrity'))) {
        $parseTokens = $null; $parseErrors = $null
        $helperAst = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $root ('installers/windows/lib/' + $definition[0])), [ref]$parseTokens, [ref]$parseErrors)
        Assert-True ($parseErrors.Count -eq 0) 'Extraction helper parse failed'
        $functionName = $definition[1]
        $functionAst = $helperAst.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $functionName }, $true)
        $body = $functionAst.Extent.Text.Replace('function Invoke-ExtractionWithRetry', 'function Invoke-RealFixtureExtraction')
        . ([scriptblock]::Create($body))
    }
    function Write-AI { param($Message) $null = $Message }
    function Write-AIWarn { param($Message) $null = $Message }
    function Write-AIError { param($Message) $null = $Message }
    function Invoke-DownloadWithRetry {
        param($Url, $Destination, $Label)
        if ($Url -notmatch '^https://github\.com/ggml-org/llama\.cpp/releases/download/b[0-9]+/' -or [string]::IsNullOrWhiteSpace($Label)) {
            throw 'Unexpected download fixture arguments'
        }
        $script:events.Add('download')
        $script:stages.Add((Split-Path -Parent $Destination))
        if ($script:mode -eq 'network') { return $false }
        if ($script:mode -eq 'changed') { [System.IO.File]::WriteAllText($Destination, 'substituted') }
        elseif ($script:mode -eq 'empty') { [System.IO.File]::WriteAllBytes($Destination, [byte[]]@()) }
        else { [System.IO.File]::Copy($script:fixture, $Destination) }
        return $true
    }
    function Invoke-ExtractionWithRetry {
        param($ZipPath, $DestinationPath)
        $script:events.Add('extract')
        if ($script:mode -eq 'extraction') { return $false }
        return (Invoke-RealFixtureExtraction -ZipPath $ZipPath -DestinationPath $DestinationPath -MaxRetries 1)
    }
    foreach ($tag in @('b8248', 'b9014')) {
        $script:events.Clear()
        Install-ODSVerifiedNativeLlama -SourceRoot $source -Tag $tag -InstallDir (Join-Path $testRoot $tag)
        Assert-True (($script:events -join ',') -eq 'download,extract') 'Download verification/extraction flow failed'
        Assert-True (Test-Path -LiteralPath (Join-Path $testRoot "$tag/llama-server/llama-server.exe")) 'Fixture did not extract'
    }
    Assert-True ($script:stages[0] -ne $script:stages[1]) 'Staging path was reused'
    Assert-True ((Get-Content -LiteralPath $oldCache -Raw) -eq 'untrusted old cache') 'Predictable old cache was consumed or removed'
    # A literal owner-selected directory with [] must never glob adjacent dirs.
    $bracketRoot = Join-Path $testRoot 'ODS[12]'
    $bracketDestination = Join-Path $bracketRoot 'llama-server'
    foreach ($name in @('ODS1', 'ODS2', 'ODS[12]')) {
        $directory = Join-Path $testRoot ($name + '/llama-server')
        $null = New-Item -ItemType Directory -Path $directory -Force
        [System.IO.File]::WriteAllText((Join-Path $directory 'sentinel.txt'), $name)
    }
    Install-ODSVerifiedNativeLlama -SourceRoot $source -Tag 'b8248' -InstallDir $bracketRoot
    Assert-True (Test-Path -LiteralPath (Join-Path $bracketDestination 'llama-server.exe')) 'Literal bracket destination was not populated'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $bracketDestination 'sentinel.txt'))) 'Literal stale destination was not cleaned'
    foreach ($neighbor in @('ODS1', 'ODS2')) {
        Assert-True ((Get-Content -LiteralPath (Join-Path $testRoot ($neighbor + '/llama-server/sentinel.txt')) -Raw) -eq $neighbor) 'Extraction modified a wildcard neighbor'
    }
    foreach ($mode in @('changed', 'empty', 'network', 'extraction')) {
        $script:mode = $mode
        $script:events.Clear()
        Assert-Rejected { Install-ODSVerifiedNativeLlama -SourceRoot $source -Tag 'b8248' -InstallDir (Join-Path $testRoot $mode) } 'does not match|regular staged|download failed|extraction failed'
        if ($mode -ne 'extraction') { Assert-True (-not $script:events.Contains('extract')) 'Rejected bytes reached extraction' }
    }
    $script:events.Clear()
    Assert-Rejected { Install-ODSVerifiedNativeLlama -SourceRoot $source -Tag 'b999999' -InstallDir (Join-Path $testRoot 'unknown') } 'No reviewed'
    Assert-True ($script:events.Count -eq 0) 'Unknown tag downloaded an artifact'
    $manifest.artifacts[0].sha256 = ''
    [System.IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 8))
    Assert-Rejected { Install-ODSVerifiedNativeLlama -SourceRoot $source -Tag 'b8248' -InstallDir (Join-Path $testRoot 'no-hash') } 'valid reviewed'
    Assert-True ($script:events.Count -eq 0) 'Missing hash reached download'
    foreach ($stage in $script:stages) { Assert-True (-not (Test-Path -LiteralPath $stage)) 'Staging was not cleaned' }
    Assert-True (@(Get-ChildItem -LiteralPath $temporary).Count -eq 1) 'Unexpected staging residue'

    # Bind the helper to the real orchestrator without dot-sourcing it or
    # executing task/service branches. Existing owner binaries skip the helper.
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'installers/windows/install-windows.ps1'), [ref]$tokens, [ref]$errors)
    Assert-True ($errors.Count -eq 0) 'Installer does not parse'
    $calls = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.CommandAst] -and $node.GetCommandName() -eq 'Install-ODSVerifiedNativeLlama' }, $true))
    Assert-True ($calls.Count -eq 1) 'Expected one verified archive installation boundary'
    $ownerGuard = $calls[0].Parent
    while ($ownerGuard -and -not ($ownerGuard -is [System.Management.Automation.Language.IfStatementAst] -and $ownerGuard.Clauses[0].Item1.Extent.Text -eq '-not (Test-Path -LiteralPath $script:LLAMA_SERVER_EXE)')) { $ownerGuard = $ownerGuard.Parent }
    Assert-True ($null -ne $ownerGuard) 'Existing owner binary reuse is not preserved'
    $failureGuard = $calls[0].Parent
    while ($failureGuard -and $failureGuard -isnot [System.Management.Automation.Language.TryStatementAst]) { $failureGuard = $failureGuard.Parent }
    Assert-True ($failureGuard.CatchClauses[0].Extent.Text -match 'exit 1') 'Verification failure does not stop runtime activation'
    Write-Host "[PASS] $script:checks native llama integrity checks; no artifact/installer/task execution"
} finally {
    $env:TEMP = $oldTemp
    $env:TMP = $oldTmp
    $cleanup = [System.IO.Path]::GetFullPath($testRoot)
    if ([System.IO.Path]::GetDirectoryName($cleanup) -ine $output -or
        [System.IO.Path]::GetFileName($cleanup) -cnotmatch '\Awindows-[0-9a-f]{32}\z') {
        throw 'Refusing fixture cleanup outside the workspace output directory.'
    }
    Remove-Item -LiteralPath $cleanup -Recurse -Force
}
