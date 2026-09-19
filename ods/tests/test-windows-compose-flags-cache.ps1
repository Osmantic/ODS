$ErrorActionPreference = 'Stop'
# Get-ComposeFlags returned a cached .compose-flags verbatim even when a
# referenced compose file had been renamed or removed by an upgrade, so every
# caller (~13 sites: status, start, stop, restart, enable, disable, ...) handed
# docker compose a path that no longer resolved. The bash CLI's
# get_compose_flags validates every -f target and rebuilds when the cache is
# stale; this test pins the same contract on Windows, including the
# logs\compose-launch.txt recovery path.
$root = Split-Path -Parent $PSScriptRoot
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'installers/windows/ods.ps1'), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw $errors[0] }
foreach ($name in @('Get-ComposeFlags','Test-ODSComposeFlagsFilesAvailable')) {
    $function = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    if (-not $function) { throw "Missing function $name" }
    . ([scriptblock]::Create($function.Extent.Text))
}
function Assert-True { param($Condition, [string]$Message) if (-not $Condition) { throw $Message }; $script:Assertions++ }
function Ensure-HermesDashboardSessionToken { }
function Resolve-ODSModelStoreComposeFlags { param($Flags) return $Flags }
function Write-AI { param($Message) }
function Write-AIWarn { param($Message) }
function Write-AISuccess { param($Message) }
function Write-AIError { param($Message) throw $Message }

$script:Assertions = 0
$fixtureRoot = Join-Path ([IO.Path]::GetTempPath()) ('ods-compose-flags-' + [Guid]::NewGuid().ToString('N'))
$InstallDir = Join-Path $fixtureRoot 'install with spaces'

function Write-ComposeFlagsCache { param([string]$Content)
    Set-Content -LiteralPath (Join-Path $InstallDir '.compose-flags') -Value $Content -Encoding utf8 -NoNewline
}
function Write-LaunchRecord { param([string]$Flags)
    $logDir = Join-Path $InstallDir 'logs'
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $logDir 'compose-launch.txt') -Value "compose_flags=$Flags" -Encoding utf8
}

try {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $InstallDir 'docker-compose.base.yml'), 'services: {}')
    [IO.File]::WriteAllText((Join-Path $InstallDir 'docker-compose.nvidia.yml'), 'services: {}')

    # ── 1. Fresh cache is used verbatim ──────────────────────────────────────
    Write-ComposeFlagsCache '-f docker-compose.base.yml -f docker-compose.nvidia.yml'
    $flags = Get-ComposeFlags
    Assert-True (($flags -join ' ') -eq '-f docker-compose.base.yml -f docker-compose.nvidia.yml') `
        "fresh cache should be returned verbatim, got: $($flags -join ' ')"

    # ── 2. Stale cache is dropped and rebuilt ────────────────────────────────
    Write-ComposeFlagsCache '-f docker-compose.base.yml -f docker-compose.retired.yml'
    $flags = Get-ComposeFlags
    Assert-True (($flags -join ' ') -notmatch 'retired') `
        "stale cache must not hand docker a missing file, got: $($flags -join ' ')"
    Assert-True (($flags -join ' ') -match 'docker-compose.base.yml') `
        "rebuilt flags should include the files that exist, got: $($flags -join ' ')"
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $InstallDir '.compose-flags'))) `
        "stale .compose-flags should be deleted so the rebuild persists"

    # ── 3. Stale cache + stale launch record both fall through ───────────────
    Write-ComposeFlagsCache '-f docker-compose.retired.yml'
    Write-LaunchRecord '-f docker-compose.gone.yml'
    $flags = Get-ComposeFlags
    Assert-True (($flags -join ' ') -notmatch 'retired|gone') `
        "stale launch record must not be used either, got: $($flags -join ' ')"
    Assert-True (($flags -join ' ') -match 'docker-compose.base.yml') `
        "dynamic fallback should detect existing files, got: $($flags -join ' ')"

    # ── 4. Fresh launch record still recovers flags when cache is absent ─────
    Write-LaunchRecord '-f docker-compose.base.yml -f docker-compose.nvidia.yml'
    $flags = Get-ComposeFlags
    Assert-True (($flags -join ' ') -eq '-f docker-compose.base.yml -f docker-compose.nvidia.yml') `
        "a fresh launch record should still be honoured, got: $($flags -join ' ')"

    # ── 5. Empty cache file rebuilds instead of returning empty flags ────────
    Remove-Item -LiteralPath (Join-Path $InstallDir 'logs') -Recurse -Force
    Write-ComposeFlagsCache ''
    $flags = Get-ComposeFlags
    Assert-True (($flags -join ' ') -match 'docker-compose.base.yml') `
        "an empty .compose-flags should rebuild, got: $($flags -join ' ')"

    Write-Host "[PASS] all $script:Assertions compose-flags cache assertions"
} finally {
    Remove-Item -LiteralPath $fixtureRoot -Recurse -Force -ErrorAction SilentlyContinue
}
