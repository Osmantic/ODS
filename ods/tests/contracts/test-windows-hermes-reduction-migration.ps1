$ErrorActionPreference = "Stop"

$phasePath = Join-Path $PSScriptRoot "../../installers/windows/phases/06-directories.ps1"
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    (Resolve-Path $phasePath),
    [ref]$tokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -gt 0) {
    throw $parseErrors[0]
}

$migrationAst = $ast.Find(
    {
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq "Remove-ODSManagedHermesReductions"
    },
    $true
)
if (-not $migrationAst) {
    throw "Remove-ODSManagedHermesReductions was not found"
}
. ([scriptblock]::Create($migrationAst.Extent.Text))

foreach ($functionName in @(
    "Get-HermesConfigRegularFile",
    "Get-HermesConfigFingerprint",
    "Update-HermesConfigFile",
    "Update-HermesConfigPair"
)) {
    $functionAst = $ast.Find(
        {
            param($node)
            $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                $node.Name -eq $functionName
        },
        $true
    )
    if (-not $functionAst) {
        throw "$functionName was not found"
    }
    . ([scriptblock]::Create($functionAst.Extent.Text))
}

$legacy = @"
model: # operator model settings
  default: "operator-model"
  max_tokens: 1024 # ODS legacy default
  temperature: 0.4
agent:
  disabled_toolsets:
    - terminal # managed item
    - browser
  # preserve this operator note
  mode: autonomous
terminal:
  backend: remote
  timeout: 30 # ODS legacy default
other:
  max_tokens: 1024
"@ -replace "`n", "`r`n"

$migrated = Remove-ODSManagedHermesReductions -Content $legacy
foreach ($removed in @(
    "  max_tokens: 1024 # ODS legacy default",
    "  disabled_toolsets:",
    "    - terminal # managed item",
    "    - browser",
    "  timeout: 30 # ODS legacy default"
)) {
    if ($migrated.Contains($removed)) {
        throw "Legacy reduction was not removed: $removed"
    }
}

$legacyLf = $legacy.Replace("`r`n", "`n")
$migratedLf = Remove-ODSManagedHermesReductions -Content $legacyLf
if ($migratedLf.Contains("  max_tokens: 1024 # ODS legacy default") -or
    $migratedLf.Contains("  timeout: 30 # ODS legacy default")) {
    throw "Legacy scalar reductions were not removed from LF input"
}
if (-not $migratedLf.Contains("other:`n  max_tokens: 1024")) {
    throw "Operator content was not preserved in LF input"
}
foreach ($preserved in @(
    "  default: `"operator-model`"",
    "  temperature: 0.4",
    "  # preserve this operator note",
    "  mode: autonomous",
    "  backend: remote",
    "other:`r`n  max_tokens: 1024"
)) {
    if (-not $migrated.Contains($preserved)) {
        throw "Operator content was not preserved: $preserved"
    }
}

$divergent = @"
model:
  max_tokens: 2048
agent:
  disabled_toolsets:
    - terminal
    - browser
    - skills
terminal:
  timeout: 45
"@
if ((Remove-ODSManagedHermesReductions -Content $divergent) -cne $divergent) {
    throw "Divergent operator reductions must remain byte-for-byte unchanged"
}

$prefixDivergent = "model:`n  max_tokens: 10240`nagent:`nmodel_override: true`nterminal:`n  timeout: 300"
if ((Remove-ODSManagedHermesReductions -Content $prefixDivergent) -cne $prefixDivergent) {
    throw "Numeric prefixes and an unrelated empty agent mapping must remain unchanged"
}

$hashDivergent = "model:`n  max_tokens: 1024#operator`nagent:`n  disabled_toolsets:`n    - terminal`n    - browser#operator`nterminal:`n  timeout: 30#operator"
if ((Remove-ODSManagedHermesReductions -Content $hashDivergent) -cne $hashDivergent) {
    throw "Hash suffixes without YAML comment whitespace must remain operator values"
}

$foldedScalar = "agent:`n  disabled_toolsets:`n    -terminal`n    -browser`n"
if ((Remove-ODSManagedHermesReductions -Content $foldedScalar) -cne $foldedScalar) {
    throw "A YAML scalar beginning with dash-prefixed words must remain operator state"
}

$windowsLegacyList = @(
    "terminal", "browser", "vision", "video", "image_gen", "video_gen",
    "x_search", "moa", "tts", "skills", "todo", "memory",
    "session_search", "clarify", "delegation", "cronjob", "messaging",
    "homeassistant", "spotify", "yuanbao", "computer_use"
)
$largeLegacy = ("agent:`n  disabled_toolsets:`n" + (($windowsLegacyList | ForEach-Object { "    - $_`n" }) -join "")).TrimEnd("`n")
if ((Remove-ODSManagedHermesReductions -Content $largeLegacy) -match "disabled_toolsets|    - ") {
    throw "The exact historical Windows toolset list was not removed"
}

$interleavedLegacy = "agent:`n  disabled_toolsets:`n    -  terminal`n    # retain this note`n`n    -   browser`n  mode: autonomous`n"
$interleavedMigrated = Remove-ODSManagedHermesReductions -Content $interleavedLegacy
if ($interleavedMigrated -match "disabled_toolsets|    -  terminal|    -   browser") {
    throw "An exact list with inter-item comments was not removed"
}
if (-not $interleavedMigrated.Contains("    # retain this note")) {
    throw "An inter-item operator comment was not preserved"
}
if (-not $interleavedMigrated.Contains("  mode: autonomous")) {
    throw "An operator sibling following the managed list was not preserved"
}

$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("ods-hermes-pair-" + [Guid]::NewGuid().ToString("N"))
$templatePath = Join-Path $testRoot "cli-config.yaml.template"
$livePath = Join-Path $testRoot "config.yaml"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$originalTemplate = @"
model:
  default: "template-before"
  base_url: "http://template-before/v1"
  context_length: 32768
providers:
  custom:
    request_timeout_seconds: 180
auxiliary:
  compression:
    context_length: 32768
compression:
  enabled: true
  threshold: 0.25
  target_ratio: 0.10
  protect_last_n: 8
"@ -replace "`n", "`r`n"
$originalLive = $originalTemplate.Replace("template-before", "live-before")

function Get-ExactFileBytes {
    param([string]$Path)
    return [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($Path))
}

try {
    [System.IO.Directory]::CreateDirectory($testRoot) | Out-Null
    [System.IO.File]::WriteAllText($templatePath, $originalTemplate, $utf8NoBom)
    [System.IO.File]::WriteAllText($livePath, $originalLive, $utf8NoBom)

    $updated = Update-HermesConfigPair `
        -TemplatePath $templatePath `
        -LivePath $livePath `
        -Model 'ods/model$&literal' `
        -BaseUrl 'http://127.0.0.1:4000/$&/v1' `
        -ContextLength 131072 `
        -RequestTimeoutSeconds 900
    if (-not $updated) {
        throw "A valid Hermes template/live pair did not update"
    }
    foreach ($path in @($templatePath, $livePath)) {
        $patched = [System.IO.File]::ReadAllText($path, $utf8NoBom)
        foreach ($expected in @(
            '  default: "ods/model$&literal"',
            '  base_url: "http://127.0.0.1:4000/$&/v1"',
            '  context_length: 131072',
            '    context_length: 131072',
            '    request_timeout_seconds: 900',
            '  threshold: 0.75',
            '  target_ratio: 0.50',
            '  protect_last_n: 40'
        )) {
            if (-not $patched.Contains($expected)) {
                throw "Patched pair member $path is missing: $expected"
            }
        }
    }

    [System.IO.File]::WriteAllText($templatePath, $originalTemplate, $utf8NoBom)
    $malformedLive = "providers:`n  custom:`n    request_timeout_seconds: 180`n"
    [System.IO.File]::WriteAllText($livePath, $malformedLive, $utf8NoBom)
    $templateBeforeFailure = Get-ExactFileBytes -Path $templatePath
    $liveBeforeFailure = Get-ExactFileBytes -Path $livePath

    $failedUpdate = Update-HermesConfigPair `
        -TemplatePath $templatePath `
        -LivePath $livePath `
        -Model "must-not-land" `
        -BaseUrl "http://must-not-land/v1" `
        -ContextLength 65536 `
        -RequestTimeoutSeconds 900
    if ($failedUpdate) {
        throw "A malformed live config unexpectedly committed the pair"
    }
    if ((Get-ExactFileBytes -Path $templatePath) -cne $templateBeforeFailure) {
        throw "Template bytes changed after the live member failed verification"
    }
    if ((Get-ExactFileBytes -Path $livePath) -cne $liveBeforeFailure) {
        throw "Live bytes changed after pair rollback"
    }

    $outsidePath = Join-Path $testRoot "outside-operator-config.yaml"
    [System.IO.File]::WriteAllText($outsidePath, $originalLive, $utf8NoBom)
    [System.IO.File]::WriteAllText($templatePath, $originalTemplate, $utf8NoBom)
    Remove-Item -LiteralPath $livePath -Force
    $linkCreated = $false
    try {
        New-Item -ItemType SymbolicLink -Path $livePath -Target $outsidePath -ErrorAction Stop | Out-Null
        $linkCreated = $true
    } catch {
        Write-Host "[SKIP] File symlink creation is unavailable; reparse rejection remains covered on capable CI hosts"
    }
    if ($linkCreated) {
        $templateBeforeLink = Get-ExactFileBytes -Path $templatePath
        $outsideBeforeLink = Get-ExactFileBytes -Path $outsidePath
        if (Update-HermesConfigPair `
            -TemplatePath $templatePath `
            -LivePath $livePath `
            -Model "must-not-follow-link" `
            -BaseUrl "http://must-not-follow-link/v1" `
            -ContextLength 65536) {
            throw "A reparse-point pair member was accepted"
        }
        if ((Get-ExactFileBytes -Path $templatePath) -cne $templateBeforeLink) {
            throw "Pair mate changed when the live member was a reparse point"
        }
        if ((Get-ExactFileBytes -Path $outsidePath) -cne $outsideBeforeLink) {
            throw "Hermes updater followed and changed a reparse-point target"
        }
        Remove-Item -LiteralPath $livePath -Force
    }
    if (Get-ChildItem -LiteralPath $testRoot -Filter "*.ods-*" -Force) {
        throw "Hermes pair update left staging or backup artifacts behind"
    }
} finally {
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "[PASS] Windows Hermes migration and transactional pair update contracts"
