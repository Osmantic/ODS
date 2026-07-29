$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-Equal {
    param(
        [Parameter(Mandatory = $true)][AllowNull()]$Expected,
        [Parameter(Mandatory = $true)][AllowNull()]$Actual,
        [Parameter(Mandatory = $true)][string]$Message
    )

    if ($Expected -ne $Actual) {
        throw "$Message (expected '$Expected', got '$Actual')"
    }
}

function Get-ResolverDefinition {
    param([Parameter(Mandatory = $true)][string]$Path)

    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        $Path,
        [ref]$tokens,
        [ref]$errors
    )
    if ($errors.Count -gt 0) {
        throw "PowerShell parse failed for ${Path}: $($errors[0].Message)"
    }

    $definition = $ast.Find({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq "Resolve-ODSHostAgentPython"
    }, $true)
    if (-not $definition) {
        throw "Resolve-ODSHostAgentPython was not found in $Path"
    }

    return $definition.Extent.Text
}

function Invoke-ResolverScenario {
    param(
        [Parameter(Mandatory = $true)][string]$Definition,
        [Parameter(Mandatory = $true)][hashtable]$Commands,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$ValidCandidates
    )

    $script:resolverCommands = $Commands
    $script:resolverValidCandidates = @($ValidCandidates)
    $script:resolverProbes = New-Object System.Collections.Generic.List[string]

    $mockGetCommand = {
        param(
            [string]$Name,
            [object]$CommandType,
            [switch]$All,
            [object]$ErrorAction
        )

        $null = $CommandType
        $null = $ErrorAction
        if (-not $All) {
            throw "Resolve-ODSHostAgentPython must enumerate all PATH matches for '$Name'"
        }
        if (-not $script:resolverCommands.ContainsKey($Name)) {
            return @()
        }
        return @($script:resolverCommands[$Name])
    }
    Set-Item -Path Function:Get-Command -Value $mockGetCommand

    function Test-ODSHostAgentPythonCandidate {
        param(
            [Parameter(Mandatory = $true)][string]$FilePath,
            [string[]]$PrefixArgs = @()
        )

        $key = "$FilePath|$($PrefixArgs -join ',')"
        $script:resolverProbes.Add($key)
        return $script:resolverValidCandidates -contains $key
    }

    function New-ODSHostAgentPythonCandidate {
        param(
            [Parameter(Mandatory = $true)][string]$FilePath,
            [string[]]$PrefixArgs = @()
        )

        return [pscustomobject]@{
            FilePath   = $FilePath
            PrefixArgs = @($PrefixArgs)
        }
    }

    $oldLocalAppData = $env:LOCALAPPDATA
    $oldProgramFiles = $env:ProgramFiles
    $oldProgramFilesX86 = ${env:ProgramFiles(x86)}
    $missingRoot = Join-Path ([System.IO.Path]::GetTempPath()) "ods-python-resolver-missing-$([guid]::NewGuid())"

    try {
        $env:LOCALAPPDATA = $missingRoot
        $env:ProgramFiles = $missingRoot
        ${env:ProgramFiles(x86)} = $missingRoot
        . ([scriptblock]::Create($Definition))
        $result = Resolve-ODSHostAgentPython

        return [pscustomobject]@{
            Result = $result
            Probes = @($script:resolverProbes)
        }
    } finally {
        $env:LOCALAPPDATA = $oldLocalAppData
        $env:ProgramFiles = $oldProgramFiles
        ${env:ProgramFiles(x86)} = $oldProgramFilesX86
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$sources = @(
    (Join-Path $repoRoot "installers/windows/phases/07-devtools.ps1"),
    (Join-Path $repoRoot "installers/windows/ods.ps1")
)

foreach ($source in $sources) {
    $definition = Get-ResolverDefinition -Path $source

    $direct = Invoke-ResolverScenario -Definition $definition -Commands @{
        python3 = @(
            [pscustomobject]@{ Source = "C:\WindowsApps\python3.exe" },
            [pscustomobject]@{ Source = "C:\Python312\python.exe" }
        )
        python = @()
        py = @()
    } -ValidCandidates @("C:\Python312\python.exe|")
    Assert-Equal "C:\Python312\python.exe" $direct.Result.FilePath "$source did not select the valid direct interpreter"
    Assert-Equal 0 $direct.Result.PrefixArgs.Count "$source added unexpected direct-interpreter arguments"

    $launcher = Invoke-ResolverScenario -Definition $definition -Commands @{
        python3 = @()
        python = @()
        py = @(
            [pscustomobject]@{ Source = "C:\WindowsApps\py.exe" },
            [pscustomobject]@{ Source = "C:\Windows\py.exe" }
        )
    } -ValidCandidates @("C:\Windows\py.exe|-3")
    Assert-Equal "C:\Windows\py.exe" $launcher.Result.FilePath "$source did not select the valid py launcher"
    Assert-Equal "-3" ($launcher.Result.PrefixArgs -join ",") "$source did not preserve the py launcher prefix"

    $duplicates = Invoke-ResolverScenario -Definition $definition -Commands @{
        python3 = @(
            [pscustomobject]@{ Source = "C:\Python312\python.exe" },
            [pscustomobject]@{ Source = "c:\python312\PYTHON.EXE" },
            [pscustomobject]@{ Source = $null }
        )
        python = @()
        py = @()
    } -ValidCandidates @()
    Assert-Equal $null $duplicates.Result "$source unexpectedly resolved an invalid candidate"
    Assert-Equal 1 $duplicates.Probes.Count "$source did not deduplicate case-insensitive PATH candidates"
}

Write-Host "[PASS] Windows host-agent Python resolver handles multiple PATH interpreters"
