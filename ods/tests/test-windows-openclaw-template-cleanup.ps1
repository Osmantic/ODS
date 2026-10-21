$ErrorActionPreference = 'Stop'

# Windows phase 06 deletes OpenClaw templates that are byte-identical to a
# shipped version and keeps everything else. It must never follow a junction
# or symbolic link out of config\openclaw. Runs the real block against
# throwaway folders; Windows only, because the link cases use junctions.

$root = Split-Path -Parent $PSScriptRoot
$phase06 = Join-Path $root 'installers/windows/phases/06-directories.ps1'
$lines = [IO.File]::ReadAllLines($phase06)
$start = -1
$end = -1
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($start -lt 0 -and $lines[$i] -like '# Every release copied the OpenClaw templates into config\openclaw*') { $start = $i }
    if ($start -ge 0 -and $lines[$i] -like '# Copy extensions library to data dir*') { $end = $i; break }
}
if ($start -lt 0 -or $end -lt 0) { throw 'The OpenClaw template cleanup block was not found in phase 06' }
$cleanup = [scriptblock]::Create(($lines[$start..($end - 1)] -join "`r`n"))

function Invoke-Cleanup([string]$Install, [string]$Source) {
    function Write-AI([string]$Message) { }
    function Write-AIWarn([string]$Message) { }
    $sourceRoot = $Source
    $_configDir = Join-Path $Install 'config'
    $_dataDir = Join-Path $Install 'data'
    . $cleanup
}

function Assert-True($Value, [string]$Message) {
    if (-not $Value) { throw $Message }
}

function Remove-Links([string]$Install) {
    # Delete junctions themselves, never their targets.
    foreach ($link in @((Join-Path $Install 'config\openclaw\workspace'), (Join-Path $Install 'config\openclaw'))) {
        $item = Get-Item -LiteralPath $link -Force -ErrorAction SilentlyContinue
        if ($null -ne $item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { $item.Delete() }
    }
}

$work = Join-Path ([IO.Path]::GetTempPath()) ('ods-openclaw-cleanup-' + [guid]::NewGuid())
try {
    $source = Join-Path $work 'source'
    $shipped = Join-Path $work 'shipped'
    New-Item -ItemType Directory -Force -Path (Join-Path $source 'installers\lib'), $shipped | Out-Null
    [IO.File]::WriteAllText((Join-Path $shipped 'openclaw.json'), "shipped openclaw.json`n")
    [IO.File]::WriteAllText((Join-Path $shipped 'SYSTEM.md'), "shipped workspace notes`n")
    $manifest = foreach ($entry in @(@('openclaw.json', 'openclaw.json'), @('SYSTEM.md', 'workspace/SYSTEM.md'))) {
        $digest = (Get-FileHash -LiteralPath (Join-Path $shipped $entry[0]) -Algorithm SHA256).Hash.ToLowerInvariant()
        "$digest  $($entry[1])"
    }
    [IO.File]::WriteAllLines((Join-Path $source 'installers\lib\retired-openclaw-config.sha256'), [string[]]$manifest)

    # Untouched templates go, changed templates and owner files stay.
    $install = Join-Path $work 'used'
    $config = Join-Path $install 'config\openclaw'
    New-Item -ItemType Directory -Force -Path (Join-Path $config 'workspace') | Out-Null
    Copy-Item -LiteralPath (Join-Path $shipped 'SYSTEM.md') -Destination (Join-Path $config 'workspace\SYSTEM.md')
    [IO.File]::WriteAllText((Join-Path $config 'openclaw.json'), "owner changed this template`n")
    [IO.File]::WriteAllText((Join-Path $config 'pro.json'), "owner file`n")
    Invoke-Cleanup $install $source
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $config 'workspace'))) 'An untouched template or its empty folder was kept'
    Assert-True (Test-Path -LiteralPath (Join-Path $config 'openclaw.json')) 'A template the owner changed was deleted'
    Assert-True (Test-Path -LiteralPath (Join-Path $config 'pro.json')) 'A file the owner added was deleted'

    # A junction is never followed, whether it is a folder inside
    # config\openclaw or config\openclaw itself.
    foreach ($case in @('workspace', 'config')) {
        $install = Join-Path $work "linked-$case"
        $outside = Join-Path $work "outside-$case"
        New-Item -ItemType Directory -Force -Path (Join-Path $install 'config'), $outside | Out-Null
        Copy-Item -LiteralPath (Join-Path $shipped 'SYSTEM.md') -Destination (Join-Path $outside 'SYSTEM.md')
        Copy-Item -LiteralPath (Join-Path $shipped 'openclaw.json') -Destination (Join-Path $outside 'openclaw.json')
        if ($case -eq 'workspace') {
            New-Item -ItemType Directory -Force -Path (Join-Path $install 'config\openclaw') | Out-Null
            New-Item -ItemType Junction -Path (Join-Path $install 'config\openclaw\workspace') -Target $outside | Out-Null
        } else {
            New-Item -ItemType Junction -Path (Join-Path $install 'config\openclaw') -Target $outside | Out-Null
        }
        try {
            Invoke-Cleanup $install $source
            Assert-True ((Test-Path -LiteralPath (Join-Path $outside 'SYSTEM.md')) -and
                (Test-Path -LiteralPath (Join-Path $outside 'openclaw.json'))) "The cleanup followed a junctioned $case folder"
        } finally {
            Remove-Links $install
        }
    }
    Write-Host '[PASS] Windows removes only untouched OpenClaw templates and never follows a junction'
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
