$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'installers/windows/ods.ps1'), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw $errors[0] }
foreach ($name in @('Get-ComposeFlags', 'Resolve-ODSModelStoreComposeFlags', 'Get-ODSNativeModelSelection')) {
    $definition = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    . ([scriptblock]::Create($definition.Extent.Text))
}
function Ensure-HermesDashboardSessionToken { }
function Read-ODSEnv { return @{ODS_ACTIVE_MODEL_STORE='default'; GGUF_FILE='fixture.gguf'} }
function Resolve-ODSHostAgentPython { return [pscustomobject]@{ FilePath = (Get-Command python -CommandType Application).Source; PrefixArgs = @() } }
function Assert-True { param($Value, $Message) if (-not $Value) { throw $Message } }
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ods-compose-models-'+[Guid]::NewGuid().ToString('N'))
$InstallDir = Join-Path $fixture 'install'
try {
    foreach ($directory in @('data/models', 'scripts', 'extensions/services/dashboard-api', 'SSD space')) {
        New-Item -ItemType Directory -Path (Join-Path $InstallDir $directory) -Force | Out-Null
    }
    Copy-Item -LiteralPath (Join-Path $root 'scripts/model-store-compose-flags.py') -Destination (Join-Path $InstallDir 'scripts')
    foreach ($module in @('model_stores.py','env_values.py')) {
        Copy-Item -LiteralPath (Join-Path $root "extensions/services/dashboard-api/$module") -Destination (Join-Path $InstallDir 'extensions/services/dashboard-api')
    }
    $cache = Join-Path $InstallDir '.compose-flags'
    $original = '--env-file .env -p owner -f base.yml -f custom.yml'
    [IO.File]::WriteAllText($cache, $original)
    Assert-True (((Get-ComposeFlags) -join ' ') -eq $original) 'An unregistered stack was changed'
    # Legacy default models need existence checks too, despite having no
    # qualification registry or artifact hashes to validate.
    $model = Join-Path $InstallDir 'data/models/fixture.gguf'
    foreach ($state in @('missing', 'empty')) {
        if ($state -eq 'empty') { [IO.File]::WriteAllText($model, '') }
        $rejected = $false
        try { $null = Get-ODSNativeModelSelection -VerifyArtifacts }
        catch { $rejected = $_.Exception.Message -match 'missing or empty' }
        Assert-True $rejected 'Default model preflight accepted a missing or empty checkpoint'
    }
    [IO.File]::WriteAllText($model, 'fixture checkpoint')
    Assert-True ((Get-ODSNativeModelSelection -VerifyArtifacts).modelPath -eq $model) 'Default model compatibility changed'
    $ssd = Join-Path $InstallDir 'SSD space'
    $registry = @{schemaVersion=1; stores=@(@{id='ssd'; hostPath=$ssd; containerPath='/model-stores/ssd'})}
    $mounts = @{services=@{'dashboard-api'=@{volumes=@(@{type='bind';source=$ssd;target='/model-stores/ssd';read_only=$true;bind=@{create_host_path=$false}})}}}
    [IO.File]::WriteAllText((Join-Path $InstallDir 'data/model-stores.json'), ($registry | ConvertTo-Json -Depth 8))
    [IO.File]::WriteAllText((Join-Path $InstallDir '.model-stores.compose.json'), ($mounts | ConvertTo-Json -Depth 8))
    [IO.File]::WriteAllText((Join-Path $InstallDir '.env'), "ODS_ACTIVE_MODEL_STORE=ssd`n")
    $resolved = (Get-ComposeFlags) -join ' '
    Assert-True ($resolved -eq ($original+' -f .model-stores.compose.json -f data/.active-model-store.compose.json')) 'Saved flags omitted newly registered mounts'
    Assert-True ([IO.File]::ReadAllText($cache) -eq $original) 'The owner saved stack was rewritten'
    [IO.File]::WriteAllText($cache, $resolved)
    [IO.File]::WriteAllText((Join-Path $InstallDir '.env'), "ODS_ACTIVE_MODEL_STORE=default`n")
    Assert-True (((Get-ComposeFlags) -join ' ') -eq ($original+' -f .model-stores.compose.json')) 'Switching back to default retained a stale SSD active mount'
    Write-Host '[PASS] Windows saved Compose flags include validated new mounts and preserve custom flags'
} finally {
    $resolved = [IO.Path]::GetFullPath($fixture)
    $prefix = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar
    if ($resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -and [IO.Path]::GetFileName($resolved).StartsWith('ods-compose-models-')) {
        Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue
    }
}
