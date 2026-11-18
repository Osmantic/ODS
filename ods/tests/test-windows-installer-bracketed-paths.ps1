$ErrorActionPreference = "Stop"

# Contract: install-windows.ps1 must handle bracketed paths without wildcard expansion.
# In PowerShell (5.1 and 7+), Test-Path, Get-ChildItem, Get-Content, Remove-Item, and Set-Content
# treat square brackets (e.g. C:\Dev\ODS[Test]) as character-class wildcards unless -LiteralPath is used.

function Assert($condition, $message) {
    if (-not $condition) { throw $message }
}

$testRoot = Join-Path ([IO.Path]::GetTempPath()) "ods-bracket[install]-$([Guid]::NewGuid().ToString('N'))"

try {
    # ── 1. Model Detection in Bracketed Path ──────────────────────────────────
    $modelsDir = Join-Path (Join-Path $testRoot "data") "models"
    [System.IO.Directory]::CreateDirectory($modelsDir) | Out-Null
    $modelFile = "Qwen3.5-2B-Q4_K_M.gguf"
    $modelPath = Join-Path $modelsDir $modelFile
    Set-Content -LiteralPath $modelPath -Value "mock-gguf-payload"

    # Demonstrate that standard Test-Path fails on bracketed path
    $nonLiteralResult = Test-Path $modelPath
    Assert (-not $nonLiteralResult) "Unquoted Test-Path must evaluate brackets as wildcards (returning false)"

    # Verify -LiteralPath detects the model correctly
    $literalResult = Test-Path -LiteralPath $modelPath
    Assert $literalResult "Test-Path -LiteralPath must detect model file inside bracketed directory"

    # ── 2. Bootstrap .env Patching in Bracketed Path ─────────────────────────
    $envPath = Join-Path $testRoot ".env"
    $initialEnv = @"
GGUF_FILE=initial.gguf
LLM_MODEL=initial-model
MAX_CONTEXT=2048
CTX_SIZE=2048
AUDIO_STT_MODEL=Systran/faster-whisper-base
WHISPER_PORT=9005
"@
    Set-Content -LiteralPath $envPath -Value $initialEnv -Encoding UTF8

    Assert (-not (Test-Path $envPath)) "Unquoted Test-Path must fail on bracketed .env path"
    Assert (Test-Path -LiteralPath $envPath) "Test-Path -LiteralPath must detect bracketed .env path"

    # Run the bootstrap patching logic from install-windows.ps1:411-424
    if (Test-Path -LiteralPath $envPath) {
        $envContent = Get-Content -LiteralPath $envPath -Raw -Encoding UTF8
        $ggufReplacement = "Qwen3.5-2B-Q4_K_M.gguf"
        $llmModelReplacement = "qwen3.5:2b"
        $maxContext = 4096
        $envContent = $envContent -replace "(?m)^GGUF_FILE=.*$", "GGUF_FILE=$ggufReplacement"
        $envContent = $envContent -replace "(?m)^LLM_MODEL=.*$", "LLM_MODEL=$llmModelReplacement"
        $envContent = $envContent -replace "(?m)^MAX_CONTEXT=.*$", "MAX_CONTEXT=$maxContext"
        $envContent = $envContent -replace "(?m)^CTX_SIZE=.*$", "CTX_SIZE=$maxContext"
        [System.IO.File]::WriteAllText($envPath, $envContent, (New-Object System.Text.UTF8Encoding($false)))
    }

    $patchedEnv = Get-Content -LiteralPath $envPath -Raw -Encoding UTF8
    Assert ($patchedEnv -match "GGUF_FILE=Qwen3.5-2B-Q4_K_M.gguf") "Patched .env must contain updated GGUF_FILE"
    Assert ($patchedEnv -match "LLM_MODEL=qwen3.5:2b") "Patched .env must contain updated LLM_MODEL"
    Assert ($patchedEnv -match "MAX_CONTEXT=4096") "Patched .env must contain updated MAX_CONTEXT"

    # ── 3. Extension Service Discovery in Bracketed Path ─────────────────────
    $extDir = Join-Path (Join-Path $testRoot "extensions") "services"
    $svcDir = Join-Path $extDir "mock-service"
    [System.IO.Directory]::CreateDirectory($svcDir) | Out-Null

    $manifestPath = Join-Path $svcDir "manifest.yaml"
    $manifestContent = @"
schema_version: ods.services.v1
name: mock-service
category: tool
compose_file: compose.yaml
"@
    Set-Content -LiteralPath $manifestPath -Value $manifestContent -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $svcDir "compose.yaml") -Value "services: {}" -Encoding UTF8

    Assert (-not (Test-Path $extDir)) "Unquoted Test-Path must fail on bracketed extensions directory"
    Assert (Test-Path -LiteralPath $extDir) "Test-Path -LiteralPath must detect bracketed extensions directory"

    $extServices = Get-ChildItem -LiteralPath $extDir -Directory | Sort-Object Name
    Assert ($extServices.Count -eq 1) "Get-ChildItem -LiteralPath must find extensions inside bracketed path"
    Assert ($extServices[0].Name -eq "mock-service") "Discovered extension service name must match"

    $foundManifest = $false
    if (Test-Path -LiteralPath $manifestPath) {
        $lines = Get-Content -LiteralPath $manifestPath -ErrorAction SilentlyContinue
        if ($lines -and ($lines | Where-Object { $_ -match "schema_version:\s*ods\.services\.v1" })) {
            $foundManifest = $true
        }
    }
    Assert $foundManifest "Extension manifest must be found and parsed inside bracketed directory"

    # ── 4. Whisper STT Configuration in Bracketed Path ───────────────────────
    $sttFound = $false
    if (Test-Path -LiteralPath $envPath) {
        $envText = [System.IO.File]::ReadAllText($envPath, (New-Object System.Text.UTF8Encoding($false)))
        if ($envText -match "WHISPER_PORT=(\d+)") {
            $sttFound = ($Matches[1] -eq "9005")
        }
    }
    Assert $sttFound "Whisper STT port override must be read from .env inside bracketed directory"

    # ── 5. Static AST Assertion on install-windows.ps1 ───────────────────────
    $root = Split-Path -Parent $PSScriptRoot
    $installerPath = Join-Path $root "installers/windows/install-windows.ps1"
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        $installerPath,
        [ref]$null,
        [ref]$null
    )

    $commandElements = $ast.FindAll(
        { $args[0] -is [System.Management.Automation.Language.CommandAst] },
        $true
    )

    $targetCommands = @("Test-Path", "Get-ChildItem", "Get-Content", "Remove-Item", "Set-Content")
    $prohibitedWildcardVars = @(
        "`$modelPath", "`$envPath", "`$extDir", "`$manifestPath", "`$cfPath",
        "`$logDir", "`$_envCheck", "`$_composeLogDir", "`$_buildLog", "`$_composeLog",
        "`$upgradeScript", "`$setupCompleteFile", "`$Path"
    )

    foreach ($cmd in $commandElements) {
        $cmdName = $cmd.GetCommandName()
        if ($cmdName -and ($targetCommands -contains $cmdName)) {
            $cmdText = $cmd.Extent.Text
            foreach ($var in $prohibitedWildcardVars) {
                # If command references one of our critical paths without -LiteralPath, fail
                if ($cmdText -match [regex]::Escape($var) -and $cmdText -notmatch "-LiteralPath") {
                    throw "Regression: $cmdName in install-windows.ps1 uses wildcard path expansion for $var without -LiteralPath: '$cmdText'"
                }
            }
        }
    }

    Write-Host "[PASS] Windows installer bracketed paths literal path contract"
} finally {
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
