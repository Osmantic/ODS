$ErrorActionPreference = "Stop"

$rootDir = Resolve-Path (Join-Path $PSScriptRoot "../..")
$envGeneratorLibrary = Join-Path $rootDir "installers/windows/lib/env-generator.ps1"

. $envGeneratorLibrary

$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) `
    "ods-windows-env-gen-$([Guid]::NewGuid().ToString('N'))"

Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $testRoot -Force | Out-Null

try {
    # Write-Utf8NoBom atomic replacement and literal-path contract under bracketed temp directory
    $bracketDir = Join-Path $testRoot "bracket [test] path"
    $bracketFile = Join-Path $bracketDir "config.env"
    $expectedUtf8Content = "KEY=value_with_unicode_✓"
    Write-Utf8NoBom -Path $bracketFile -Content $expectedUtf8Content

    if (-not (Test-Path -LiteralPath $bracketFile -PathType Leaf)) {
        throw "Write-Utf8NoBom failed to write file under bracketed directory"
    }
    $writtenContent = [System.IO.File]::ReadAllText($bracketFile)
    if ($writtenContent -ne $expectedUtf8Content) {
        throw "Write-Utf8NoBom content mismatch under bracketed directory"
    }
    $fileBytes = [System.IO.File]::ReadAllBytes($bracketFile)
    if ($fileBytes.Length -ge 3 -and $fileBytes[0] -eq 0xEF -and $fileBytes[1] -eq 0xBB -and $fileBytes[2] -eq 0xBF) {
        throw "Write-Utf8NoBom wrote UTF-8 BOM"
    }

    # Assert atomic replacement when overwriting an existing file under a bracketed path
    $updatedUtf8Content = "KEY=updated_value_✓"
    Write-Utf8NoBom -Path $bracketFile -Content $updatedUtf8Content
    $updatedContent = [System.IO.File]::ReadAllText($bracketFile)
    if ($updatedContent -ne $updatedUtf8Content) {
        throw "Write-Utf8NoBom failed to replace existing file under bracketed directory"
    }

    Write-Host "[PASS] Windows env-generator Write-Utf8NoBom atomic literal-path contracts"
} finally {
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
