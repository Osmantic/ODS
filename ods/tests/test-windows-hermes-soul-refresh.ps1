$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$targets = @(
    @{ Path = "installers/windows/phases/06-directories.ps1"; Parameterized = $true },
    @{ Path = "installers/windows/ods.ps1"; Parameterized = $false }
)

function Get-FunctionText {
    param([string]$Path, [string]$Name)

    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        $Path, [ref]$tokens, [ref]$errors
    )
    if ($errors.Count -ne 0) {
        throw "PowerShell parse failed for ${Path}: $($errors[0].Message)"
    }
    $functionAst = $ast.Find({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq $Name
    }, $true)
    if (-not $functionAst) { throw "Function $Name not found in $Path" }
    return $functionAst.Extent.Text
}

foreach ($target in $targets) {
    $sandbox = Join-Path ([System.IO.Path]::GetTempPath()) ("ods-soul-" + [guid]::NewGuid())
    try {
        $hermesDir = Join-Path $sandbox "extensions/services/hermes"
        $personaDir = Join-Path $sandbox "data/persona"
        New-Item -ItemType Directory -Path $hermesDir, $personaDir -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $hermesDir "SOUL.md.template") -Value "fresh template" -NoNewline
        Set-Content -LiteralPath (Join-Path $personaDir "SOUL.md") -Value "stale persona" -NoNewline

        function Write-AIWarn { param([string]$Message) }
        function Write-AISuccess { param([string]$Message) }
        function Write-Utf8NoBom {
            param([string]$Path, [string]$Content)
            [System.IO.File]::WriteAllText($Path, $Content, [System.Text.UTF8Encoding]::new($false))
        }

        $script:ODS_LOG_FILE = Join-Path $sandbox "installer.log"
        $InstallDir = $sandbox
        Invoke-Expression (Get-FunctionText -Path (Join-Path $repoRoot $target.Path) -Name "Invoke-HermesSoulRefresh")

        if ($target.Parameterized) {
            Invoke-HermesSoulRefresh -InstallRoot $sandbox
        } else {
            Invoke-HermesSoulRefresh
        }

        $actual = Get-Content -LiteralPath (Join-Path $personaDir "SOUL.md") -Raw
        if ($actual -ne "fresh template") {
            throw "$($target.Path) preserved stale SOUL.md during fallback refresh"
        }
        Write-Host "PASS $($target.Path) refreshes stale SOUL.md without Python"
    } finally {
        Remove-Item -LiteralPath $sandbox -Recurse -Force -ErrorAction SilentlyContinue
    }
}
