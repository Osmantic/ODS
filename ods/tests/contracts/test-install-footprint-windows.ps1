$ErrorActionPreference = "Stop"

$footprintHelper = Join-Path $PSScriptRoot "..\..\installers\windows\lib\installed-footprint.ps1"
. $footprintHelper

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

    foreach ($directory in @("docs", "data", "models", "config", "extensions\user")) {
        New-Item -ItemType Directory -Force -Path (Join-Path $installDir $directory) | Out-Null
    }
    Set-Content -LiteralPath (Join-Path $installDir "docs\stale.txt") -Value "modified documentation"
    Set-Content -LiteralPath (Join-Path $installDir "README.md") -Value "modified readme"
    Set-Content -LiteralPath (Join-Path $installDir ".env") -Value "ODS_VERSION=2.6.0"
    Set-Content -LiteralPath (Join-Path $installDir "manifest.json") -Value "{}"
    Set-Content -LiteralPath (Join-Path $installDir "docker-compose.base.yml") -Value "services: {}"
    Set-Content -LiteralPath (Join-Path $installDir "data\preserve.db") -Value "user data"
    Set-Content -LiteralPath (Join-Path $installDir "models\preserve.gguf") -Value "model"
    Set-Content -LiteralPath (Join-Path $installDir "config\user.yaml") -Value "user config"
    Set-Content -LiteralPath (Join-Path $installDir "extensions\user\keep.txt") -Value "user extension"

    $junctionTarget = Join-Path $testRoot "outside-junction-target"
    New-Item -ItemType Directory -Path $junctionTarget | Out-Null
    Set-Content -LiteralPath (Join-Path $junctionTarget "outside.txt") -Value "outside data"
    New-Item -ItemType Junction -Path (Join-Path $installDir "examples") -Target $junctionTarget | Out-Null

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

    $freshInstallDir = Join-Path $testRoot "fresh install"
    New-Item -ItemType Directory -Path $freshInstallDir | Out-Null
    $freshRobocopyArgs = @($robocopyArgs)
    $freshRobocopyArgs[1] = $freshInstallDir
    & robocopy @freshRobocopyArgs | Out-Null
    if ($LASTEXITCODE -gt 7) {
        throw "fresh-install robocopy failed with exit code $LASTEXITCODE"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $freshInstallDir "config\runtime.yaml"))) {
        throw "fresh install omitted a runtime file"
    }
    foreach ($relativePath in @("tests", "docs", "examples", ".github", "README.md")) {
        if (Test-Path -LiteralPath (Join-Path $freshInstallDir $relativePath)) {
            throw "fresh install included development-only path: $relativePath"
        }
    }
    if (Test-Path -LiteralPath (Join-Path $freshInstallDir "data\installer-backups")) {
        throw "fresh install created an unnecessary upgrade backup"
    }

    $pruneStaleDevPaths = (
        (Test-Path -LiteralPath (Join-Path $installDir ".env") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $installDir "manifest.json") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $installDir "docker-compose.base.yml") -PathType Leaf)
    )

    & robocopy @robocopyArgs | Out-Null
    if ($LASTEXITCODE -gt 7) {
        throw "robocopy failed with exit code $LASTEXITCODE"
    }

    $devBackup = $null
    if ($pruneStaleDevPaths) {
        $devBackup = Move-ODSDevelopmentPathsToBackup `
            -InstallDir $installDir `
            -RelativePaths @($devOnlyDirectories + $devOnlyFiles)
    }
    if ([string]::IsNullOrWhiteSpace($devBackup) -or
        -not (Test-Path -LiteralPath $devBackup -PathType Container)) {
        throw "managed upgrade did not create a development-file backup"
    }

    $expectedPresent = @(
        "extensions\services\demo\README.md",
        "extensions\services\demo\docs\runtime.txt",
        "config\runtime.yaml",
        "config\user.yaml",
        "data\preserve.db",
        "models\preserve.gguf",
        "extensions\user\keep.txt"
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

    if ((Get-Content -LiteralPath (Join-Path $devBackup "README.md") -Raw).Trim() -ne "modified readme" -or
        (Get-Content -LiteralPath (Join-Path $devBackup "docs\stale.txt") -Raw).Trim() -ne "modified documentation") {
        throw "modified development files were not preserved in the backup"
    }
    $backedUpJunction = Get-Item -LiteralPath (Join-Path $devBackup "examples") -Force
    if (($backedUpJunction.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) {
        throw "junction was traversed instead of being moved as a directory entry"
    }
    if ((Get-Content -LiteralPath (Join-Path $junctionTarget "outside.txt") -Raw).Trim() -ne "outside data") {
        throw "junction target outside the installation was modified"
    }

    $unmanagedInstallDir = Join-Path $testRoot "unmanaged install"
    New-Item -ItemType Directory -Force -Path (Join-Path $unmanagedInstallDir "docs") | Out-Null
    Set-Content -LiteralPath (Join-Path $unmanagedInstallDir "README.md") -Value "personal readme"
    Set-Content -LiteralPath (Join-Path $unmanagedInstallDir "docs\personal.txt") -Value "personal docs"
    Set-Content -LiteralPath (Join-Path $unmanagedInstallDir ".env") -Value "APP_ENV=development"

    $unmanagedWasManaged = (
        (Test-Path -LiteralPath (Join-Path $unmanagedInstallDir ".env") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $unmanagedInstallDir "manifest.json") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $unmanagedInstallDir "docker-compose.base.yml") -PathType Leaf)
    )
    if ($unmanagedWasManaged) {
        throw "unmanaged fixture was incorrectly classified as an ODS install"
    }

    $unmanagedRobocopyArgs = @($robocopyArgs)
    $unmanagedRobocopyArgs[1] = $unmanagedInstallDir
    & robocopy @unmanagedRobocopyArgs | Out-Null
    if ($LASTEXITCODE -gt 7) {
        throw "unmanaged robocopy failed with exit code $LASTEXITCODE"
    }
    if ($unmanagedWasManaged) {
        Move-ODSDevelopmentPathsToBackup `
            -InstallDir $unmanagedInstallDir `
            -RelativePaths @($devOnlyDirectories + $devOnlyFiles) | Out-Null
    }

    if (-not (Test-Path -LiteralPath (Join-Path $unmanagedInstallDir "README.md")) -or
        -not (Test-Path -LiteralPath (Join-Path $unmanagedInstallDir "docs\personal.txt"))) {
        throw "unmanaged target data was removed"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $unmanagedInstallDir "config\runtime.yaml"))) {
        throw "runtime files were not copied to unmanaged target"
    }

    $invalidPathWasRejected = $false
    try {
        Move-ODSDevelopmentPathsToBackup -InstallDir $installDir -RelativePaths "..\outside"
    } catch {
        $invalidPathWasRejected = $true
    }
    if (-not $invalidPathWasRejected) {
        throw "backup helper accepted a path outside the installation root"
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
