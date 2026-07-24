$ErrorActionPreference = "Stop"

$testRoot = Join-Path ([IO.Path]::GetTempPath()) ("ods-footprint-" + [guid]::NewGuid().ToString("N"))
$sourceRoot = Join-Path $testRoot "source"
$installDir = Join-Path $testRoot "install"

try {
    New-Item -ItemType Directory -Force -Path $sourceRoot, $installDir | Out-Null

    $sourceDirectories = @(
        "tests",
        "docs",
        "examples",
        ".github",
        "extensions\services\demo\docs",
        "config",
        "data"
    )
    foreach ($directory in $sourceDirectories) {
        New-Item -ItemType Directory -Force -Path (Join-Path $sourceRoot $directory) | Out-Null
    }

    Set-Content -LiteralPath (Join-Path $sourceRoot "README.md") -Value "root development file"
    Set-Content -LiteralPath (Join-Path $sourceRoot "docs\guide.txt") -Value "root development file"
    Set-Content -LiteralPath (Join-Path $sourceRoot "tests\test.txt") -Value "root development file"
    Set-Content -LiteralPath (Join-Path $sourceRoot "extensions\services\demo\README.md") -Value "nested runtime asset"
    Set-Content -LiteralPath (Join-Path $sourceRoot "extensions\services\demo\docs\runtime.txt") -Value "nested runtime asset"
    Set-Content -LiteralPath (Join-Path $sourceRoot "config\runtime.yaml") -Value "runtime"

    foreach ($directory in @("docs", "data", "models")) {
        New-Item -ItemType Directory -Force -Path (Join-Path $installDir $directory) | Out-Null
    }
    Set-Content -LiteralPath (Join-Path $installDir "docs\stale.txt") -Value "stale"
    Set-Content -LiteralPath (Join-Path $installDir "README.md") -Value "stale"
    Set-Content -LiteralPath (Join-Path $installDir "data\preserve.db") -Value "user data"
    Set-Content -LiteralPath (Join-Path $installDir "models\preserve.gguf") -Value "model"

    $devOnlyDirectories = @("tests", "docs", "examples", ".github")
    $devOnlyFiles = @(
        "CHANGELOG.md", "CODE_OF_CONDUCT.md", "CONTRIBUTING.md",
        "EDGE-QUICKSTART.md", "FAQ.md", "QUICKSTART.md",
        "SECURITY.md", "README.md",
        ".shellcheckrc", "PSScriptAnalyzerSettings.psd1",
        "test-stack.sh", ".gitignore"
    )
    $robocopyArgs = @(
        $sourceRoot, $installDir,
        "/E", "/NFL", "/NDL", "/NJH", "/NJS",
        "/XD", ".git", "data", "logs", "models", "node_modules", "dist"
    )
    $robocopyArgs += @($devOnlyDirectories | ForEach-Object {
        Join-Path $sourceRoot $_
    })
    $robocopyArgs += @(
        "/XF", ".env", "*.log", ".current-mode", ".profiles",
        ".target-model", ".target-quantization", ".offline-mode"
    )
    $robocopyArgs += @($devOnlyFiles | ForEach-Object {
        Join-Path $sourceRoot $_
    })

    & robocopy @robocopyArgs | Out-Null
    if ($LASTEXITCODE -gt 7) {
        throw "robocopy failed with exit code $LASTEXITCODE"
    }

    foreach ($devOnlyPath in @($devOnlyDirectories + $devOnlyFiles)) {
        $stalePath = Join-Path $installDir $devOnlyPath
        if (Test-Path -LiteralPath $stalePath) {
            Remove-Item -LiteralPath $stalePath -Recurse -Force
        }
    }

    $expectedPresent = @(
        "extensions\services\demo\README.md",
        "extensions\services\demo\docs\runtime.txt",
        "config\runtime.yaml",
        "data\preserve.db",
        "models\preserve.gguf"
    )
    foreach ($relativePath in $expectedPresent) {
        if (-not (Test-Path -LiteralPath (Join-Path $installDir $relativePath))) {
            throw "expected preserved path is missing: $relativePath"
        }
    }

    foreach ($relativePath in @("tests", "docs", "examples", ".github", "README.md")) {
        if (Test-Path -LiteralPath (Join-Path $installDir $relativePath)) {
            throw "development-only path remains installed: $relativePath"
        }
    }

    Write-Host "[PASS] Windows installed-footprint contract"
    exit 0
} finally {
    $resolvedTestRoot = [IO.Path]::GetFullPath($testRoot)
    $resolvedTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if ($resolvedTestRoot.StartsWith($resolvedTempRoot, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
