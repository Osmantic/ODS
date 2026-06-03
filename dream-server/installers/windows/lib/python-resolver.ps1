# ============================================================================
# Dream Server Windows Installer -- Python Resolver
# ============================================================================
# Finds a runnable Python 3 interpreter on Windows without trusting Store
# execution aliases that exist on PATH but fail when launched.
# ============================================================================

function New-DreamPythonCandidate {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [string[]]$PythonArgs = @(),
        [string]$Label = ""
    )

    [pscustomobject]@{
        Source = $Source
        PythonArgs = @($PythonArgs)
        Label = $(if ($Label) { $Label } else { $Source })
    }
}

function Test-DreamPythonCandidate {
    param(
        [Parameter(Mandatory = $true)]$Candidate,
        [int]$MinimumMajor = 3,
        [int]$MinimumMinor = 8
    )

    try {
        $probe = "import sys; raise SystemExit(0 if sys.version_info >= ($MinimumMajor, $MinimumMinor) else 1)"
        & $Candidate.Source @($Candidate.PythonArgs) -c $probe *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Get-DreamPythonVersion {
    param([Parameter(Mandatory = $true)]$Candidate)

    try {
        $version = & $Candidate.Source @($Candidate.PythonArgs) -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version) {
            return ($version | Select-Object -First 1).Trim()
        }
    } catch { }
    return ""
}

function Resolve-DreamWindowsPython {
    <#
    .SYNOPSIS
        Return a runnable Python 3 command for Windows installer helpers.

    .DESCRIPTION
        Checks explicit DREAM_PYTHON first, then PATH commands, py.exe launcher,
        and common Python install directories. Every candidate must execute a
        Python 3.8+ probe, which filters out Microsoft Store aliases that appear
        in PATH but do not provide a usable interpreter.
    #>
    param(
        [int]$MinimumMajor = 3,
        [int]$MinimumMinor = 8
    )

    $candidates = New-Object 'System.Collections.Generic.List[object]'

    if (-not [string]::IsNullOrWhiteSpace($env:DREAM_PYTHON)) {
        [void]$candidates.Add((New-DreamPythonCandidate -Source $env:DREAM_PYTHON -Label "DREAM_PYTHON"))
    }

    foreach ($name in @("python", "python3")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source) {
            [void]$candidates.Add((New-DreamPythonCandidate -Source $cmd.Source -Label $name))
        }
    }

    $pyLauncher = Get-Command "py" -ErrorAction SilentlyContinue
    if ($pyLauncher -and $pyLauncher.Source) {
        [void]$candidates.Add((New-DreamPythonCandidate -Source $pyLauncher.Source -PythonArgs @("-3") -Label "py -3"))
    }

    $searchRoots = @()
    if (-not [string]::IsNullOrWhiteSpace($env:LocalAppData)) {
        $searchRoots += (Join-Path $env:LocalAppData "Programs\Python")
    }
    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        $searchRoots += (Join-Path $env:ProgramFiles "Python")
    }
    if (${env:ProgramFiles(x86)}) {
        $searchRoots += (Join-Path ${env:ProgramFiles(x86)} "Python")
    }

    foreach ($root in $searchRoots) {
        if ([string]::IsNullOrWhiteSpace($root) -or -not (Test-Path -LiteralPath $root)) { continue }
        Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match "^Python3" } |
            Sort-Object -Property Name -Descending |
            ForEach-Object {
                $pythonExe = Join-Path $_.FullName "python.exe"
                if (Test-Path -LiteralPath $pythonExe) {
                    [void]$candidates.Add((New-DreamPythonCandidate -Source $pythonExe -Label $_.Name))
                }
            }
    }

    $seen = @{}
    foreach ($candidate in $candidates) {
        $key = "$($candidate.Source)|$(@($candidate.PythonArgs) -join ' ')"
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true

        if (Test-DreamPythonCandidate -Candidate $candidate -MinimumMajor $MinimumMajor -MinimumMinor $MinimumMinor) {
            $candidate | Add-Member -NotePropertyName Version -NotePropertyValue (Get-DreamPythonVersion -Candidate $candidate) -Force
            return $candidate
        }
    }

    return $null
}

function ConvertTo-DreamPowerShellLiteral {
    param([AllowNull()][string]$Value)
    "'" + (($Value -as [string]) -replace "'", "''") + "'"
}

function ConvertTo-DreamPowerShellArrayExpression {
    param([string[]]$Values = @())

    if (-not $Values -or $Values.Count -eq 0) {
        return "@()"
    }

    $literals = @()
    foreach ($value in $Values) {
        $literals += (ConvertTo-DreamPowerShellLiteral $value)
    }
    "@(" + ($literals -join ", ") + ")"
}
