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

Write-Host "[PASS] Windows .env reader keeps UTF-8 values"
