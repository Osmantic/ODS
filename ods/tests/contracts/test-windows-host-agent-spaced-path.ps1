$ErrorActionPreference = "Stop"

# Windows PowerShell 5.1 joins a Start-Process -ArgumentList array with spaces
# and no quoting. The host agent launch passed its script, pid-file and
# install-dir paths that way, so an install under a profile with a space
# (C:\Users\John Smith\ods, the default %USERPROFILE%\ods) reached python as
# split arguments and the agent exited immediately.

$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$windowsDir = Join-Path $root "installers\windows"
. (Join-Path $windowsDir "lib\host-agent-process.ps1")

$python = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python3.exe -ErrorAction Stop }

$dir = Join-Path ([IO.Path]::GetTempPath()) ("ods agent spaced " + [guid]::NewGuid())
$installDir = Join-Path $dir "John O'Smith\ods"
New-Item -ItemType Directory -Path (Join-Path $installDir "bin") -Force | Out-Null
try {
    $agentScript = Join-Path $installDir "bin\ods-host-agent.py"
    $pidFile = Join-Path $installDir "data\host agent.pid"
    $outFile = Join-Path $dir "argv.json"
    [IO.File]::WriteAllText($agentScript,
        "import json, sys`nopen(sys.argv[-1] + '.argv', 'w', encoding='utf-8').write(json.dumps(sys.argv[1:]))`n",
        [Text.UTF8Encoding]::new($false))

    $line = ConvertTo-ODSHostAgentArgumentLine -PrefixArgs @() -AgentScript $agentScript `
        -Port 7710 -PidFile $pidFile -InstallDir $installDir

    # Reproduce the generated task script: the line is embedded as a
    # single-quoted literal and handed to Start-Process in a child powershell.
    $literal = "'" + ($line -replace "'", "''") + "'"
    $pyLiteral = "'" + ($python.Source -replace "'", "''") + "'"
    $command = "`$agentArgs = $literal`nStart-Process -FilePath $pyLiteral -ArgumentList `$agentArgs -Wait -WindowStyle Hidden"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand $encoded
    if ($LASTEXITCODE -ne 0) { throw "launcher child exited $LASTEXITCODE" }

    $argvPath = "$installDir.argv"
    if (-not (Test-Path -LiteralPath $argvPath)) {
        throw "host agent did not receive its install dir intact (no argv record at $argvPath)"
    }
    $got = @([IO.File]::ReadAllText($argvPath) | ConvertFrom-Json) | ForEach-Object { $_ }
    $expected = @('--port', '7710', '--pid-file', $pidFile, '--install-dir', $installDir)
    if (($got -join '|') -cne ($expected -join '|')) {
        throw "host agent argv split: got [$($got -join '|')], expected [$($expected -join '|')]"
    }
} finally {
    Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue
}

foreach ($rel in @("ods.ps1", "phases\07-devtools.ps1")) {
    $src = [IO.File]::ReadAllText((Join-Path $windowsDir $rel))
    if ($src -notmatch 'ConvertTo-ODSHostAgentArgumentLine') {
        throw "$rel must build the host agent argument line with ConvertTo-ODSHostAgentArgumentLine"
    }
    if ($src -match "_agentScriptLiteral|-ArgumentList @\(\`$agentScript") {
        throw "$rel still passes the host agent paths as an unquoted -ArgumentList array"
    }
}

Write-Host "PASS: host agent launch keeps spaced install paths intact"
