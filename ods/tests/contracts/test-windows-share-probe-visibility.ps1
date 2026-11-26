# Contract: the Windows preflight Docker file-sharing probe must catch a mount
# that succeeds but does not show the host's files inside the container
# (Docker Desktop with a USB/removable drive mounts an empty or stale tree with
# exit 0), and must refuse a data\models junction/symlink that containers see
# as a dangling link (#8496).
$ErrorActionPreference = 'Stop'
$phase = [IO.File]::ReadAllText((Join-Path $PSScriptRoot '../../installers/windows/phases/01-preflight.ps1'))

function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    Write-Host "PASS $Message"
}

Check ($phase -notmatch 'docker run --rm -v "\$\{_fsProbe\}:/check:ro" \$_probeImage true') `
    'probe no longer accepts any successful mount as visible'
Check ($phase -match 'ODS_PROBE_ENTRIES=\$\(\$_probeEntries -join ''/''\)') `
    'probe passes sampled host entry names to the container'
Check ($phase -match '\$_probeExit -eq 42\)\s*\{\s*\$_shareOk = \$false\s*\$_shareEmpty = \$true') `
    'exit 42 is reported as an unreadable mount, not a generic probe failure'
Check ($phase -match 'if \(\$_shareEmpty\) \{[^}]*removable or USB drives') `
    'unreadable mount explains removable drives instead of the File Sharing allowlist'
Check ($phase -match 'ReparsePoint' -and $phase -match 'data\\models is a link') `
    'data\models links are refused before install'

$probeScript = [regex]::Match($phase, '\$_probeScript = "([^"]*)"').Groups[1].Value
Check ([bool]$probeScript) 'probe script is defined in one place'
# Windows PowerShell 5.1 mangles embedded double quotes in native arguments.
Check ($probeScript -notmatch '"') 'probe shell script has no embedded double quotes'

# Run the real probe script against fixture directories when a POSIX shell is
# available, standing in for what the container sees at /check.
$sh = Get-Command sh -ErrorAction SilentlyContinue
if (-not $sh) { Write-Host 'SKIP probe script behaviour: no sh on PATH'; exit 0 }

$root = Join-Path ([IO.Path]::GetTempPath()) ('ods-share-probe-' + [guid]::NewGuid().ToString('N'))
try {
    $seen = (Join-Path $root 'seen') -replace '\\', '/'
    $cases = @(
        @{ Name = 'container sees every host entry'; Host = @('model one.gguf', 'b.gguf'); Container = @('model one.gguf', 'b.gguf', 'extra'); Exit = 0 },
        @{ Name = 'container sees an empty mount'; Host = @('a.gguf'); Container = @(); Exit = 42 },
        @{ Name = 'container sees a stale tree missing a host entry'; Host = @('models', 'ods-hackathon'); Container = @('models'); Exit = 42 }
    )
    foreach ($case in $cases) {
        $check = Join-Path $root ([guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $check -Force | Out-Null
        foreach ($name in $case.Container) { Set-Content -LiteralPath (Join-Path $check $name) -Value 'x' }
        $script = $probeScript.Replace('/tmp/seen', $seen).Replace('/check', ($check -replace '\\', '/'))
        $env:ODS_PROBE_ENTRIES = $case.Host -join '/'
        & $sh.Source -c $script
        Check ($LASTEXITCODE -eq $case.Exit) "$($case.Name) -> exit $($case.Exit)"
    }
} finally {
    Remove-Item Env:ODS_PROBE_ENTRIES -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
exit 0
