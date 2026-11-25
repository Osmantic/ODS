# Contract: the Windows preflight Docker file-sharing probe must catch a mount
# that succeeds but is empty inside the container (Docker Desktop and a USB or
# removable drive), and must refuse a data\models junction/symlink that
# containers see as a dangling link (#8496).
$ErrorActionPreference = 'Stop'
$phase = [IO.File]::ReadAllText((Join-Path $PSScriptRoot '../../installers/windows/phases/01-preflight.ps1'))

function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    Write-Host "PASS $Message"
}

Check ($phase -notmatch 'docker run --rm -v "\$\{_fsProbe\}:/check:ro" \$_probeImage true') `
    'probe no longer accepts any successful mount as visible'
Check ($phase -match [regex]::Escape("'sh', '-c', 'ls -A /check | grep -q . || exit 42'")) `
    'probe requires the container to see an entry the host has'
Check ($phase -match '\$_probeExit -eq 42\)\s*\{\s*\$_shareOk = \$false\s*\$_shareEmpty = \$true') `
    'exit 42 is reported as an empty mount, not a generic probe failure'
Check ($phase -match 'if \(\$_shareEmpty\) \{[^}]*removable or USB drives') `
    'empty mount explains removable drives instead of the File Sharing allowlist'
Check ($phase -match 'ReparsePoint' -and $phase -match 'data\\models is a link') `
    'data\models links are refused before install'

# The probe script must not contain double quotes: Windows PowerShell 5.1
# mangles embedded quotes in native arguments.
$probeScript = [regex]::Match($phase, "'sh', '-c', '([^']*)'").Groups[1].Value
Check ($probeScript -and $probeScript -notmatch '"') 'probe shell script has no embedded double quotes'

# Decision table, using the same command selection the phase uses.
$root = Join-Path ([IO.Path]::GetTempPath()) ('ods-share-probe-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path (Join-Path $root 'empty') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $root 'full') -Force | Out-Null
Set-Content -LiteralPath (Join-Path $root 'full\model.gguf') -Value 'x'
try {
    foreach ($case in @(@{ Dir = 'empty'; Expected = 'true' }, @{ Dir = 'full'; Expected = 'sh' })) {
        $dir = Join-Path $root $case.Dir
        $hasEntries = [bool](Get-ChildItem -LiteralPath $dir -Force -ErrorAction SilentlyContinue | Select-Object -First 1)
        $command = @(if ($hasEntries) { 'sh', '-c', $probeScript } else { 'true' })
        Check ($command[0] -eq $case.Expected) "a $($case.Dir) install path selects the '$($case.Expected)' probe"
    }
} finally {
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
exit 0
