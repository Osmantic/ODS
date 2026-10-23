$ErrorActionPreference = "Stop"

# Windows PowerShell 5.1 decodes a BOM-less file as the ANSI code page unless
# Get-Content is given -Encoding. ODS writes .env as UTF-8 without a BOM, so
# a value with non-ASCII characters (a user profile path such as C:\Users\José)
# was read back garbled.

$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
. (Join-Path $root "installers\windows\lib\llm-endpoint.ps1")

$dir = Join-Path ([IO.Path]::GetTempPath()) ("ods-env-utf8-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $dir | Out-Null
try {
    $envPath = Join-Path $dir ".env"
    $expected = "C:\Users\Jos" + [char]0x00E9 + "\models"
    $quoted = "caf" + [char]0x00E9 + " " + [char]0x2713
    $content = "MODEL_DIR=$expected`nLABEL=`"$quoted`"`n"
    [IO.File]::WriteAllText($envPath, $content, [Text.UTF8Encoding]::new($false))

    $map = Get-WindowsODSEnvMap -Path $envPath
    if (-not ($map["MODEL_DIR"] -ceq $expected)) {
        throw "Get-WindowsODSEnvMap garbled a UTF-8 value: got '$($map["MODEL_DIR"])'"
    }
    if (-not ($map["LABEL"] -ceq $quoted)) {
        throw "Get-WindowsODSEnvMap garbled a quoted UTF-8 value: got '$($map["LABEL"])'"
    }
} finally {
    Remove-Item -LiteralPath $dir -Recurse -Force
}

$contract = Get-Content -LiteralPath (Join-Path $root "installers\windows\lib\backend-contract.ps1") -Raw
if ($contract -match "function\s+Get-ODSEnvFileValue") {
    throw "backend-contract.ps1 still defines the unused, non-UTF-8 Get-ODSEnvFileValue reader"
}

# Writers that read .env and write the whole file back must decode it as UTF-8
# too, or every rewrite double-encodes the owner's non-ASCII values (#7915).
function Get-ODSScriptAst([string]$RelativePath) {
    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $root $RelativePath), [ref]$tokens, [ref]$errors)
    if ($errors.Count -gt 0) { throw "$RelativePath does not parse: $($errors[0].Message)" }
    return $ast
}

$odsAst = Get-ODSScriptAst "installers\windows\ods.ps1"
$setter = $odsAst.Find({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq "Set-ODSEnvValue"
}, $true)
if (-not $setter) { throw "ods.ps1 no longer defines Set-ODSEnvValue" }
. ([scriptblock]::Create($setter.Extent.Text))

$InstallDir = Join-Path ([IO.Path]::GetTempPath()) ("ods-env-utf8-writer-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $InstallDir | Out-Null
try {
    $envPath = Join-Path $InstallDir ".env"
    $owner = "AUDIO_TTS_VOICE=Jos" + [char]0x00E9 + " " + [char]0x2713
    [IO.File]::WriteAllText($envPath, "LLAMA_CPU_LIMIT=4.0`n$owner`n", [Text.UTF8Encoding]::new($false))
    foreach ($limit in @("1.0", "2.0")) {
        Set-ODSEnvValue -Key "LLAMA_CPU_LIMIT" -Value $limit
        $lines = [IO.File]::ReadAllLines($envPath, [Text.UTF8Encoding]::new($false))
        if (-not ($lines -ccontains $owner)) {
            throw "Set-ODSEnvValue garbled a UTF-8 value: got '$(@($lines | Where-Object { $_ -like 'AUDIO_TTS_VOICE=*' }) -join '|')'"
        }
        if (-not ($lines -ccontains "LLAMA_CPU_LIMIT=$limit")) {
            throw "Set-ODSEnvValue did not update LLAMA_CPU_LIMIT to $limit"
        }
    }
} finally {
    Remove-Item -LiteralPath $InstallDir -Recurse -Force
}

# install-windows.ps1 patches .env inline (bootstrap model, image fallbacks),
# so check every .env read in both lifecycle scripts passes -Encoding UTF8.
foreach ($script in @("installers\windows\ods.ps1", "installers\windows\install-windows.ps1")) {
    $reads = (Get-ODSScriptAst $script).FindAll({ param($node)
        $node -is [System.Management.Automation.Language.CommandAst] -and
        $node.GetCommandName() -eq "Get-Content" -and
        $node.Extent.Text -match '\$(envPath|envFile|_envCheck)\b'
    }, $true)
    if (@($reads).Count -eq 0) { throw "$script has no .env reads to check; update this contract" }
    foreach ($read in $reads) {
        if ($read.Extent.Text -notmatch '-Encoding\s+UTF8\b') {
            throw "$script line $($read.Extent.StartLineNumber) reads .env without -Encoding UTF8: $($read.Extent.Text)"
        }
    }
}

Write-Host "[PASS] Windows .env reader keeps UTF-8 values"
