$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root "installers/windows/lib/detection.ps1")

function Assert-Equal {
    param($Actual, $Expected, [string]$Label)
    if ($Actual -ne $Expected) {
        throw "$Label expected '$Expected', got '$Actual'"
    }
}

$onWindows = ($PSVersionTable.PSEdition -eq "Desktop") -or $IsWindows
$fakeDir = Join-Path ([System.IO.Path]::GetTempPath()) ("ods-fake-docker-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $fakeDir | Out-Null

function Set-FakeDocker {
    param([ValidateSet("hang", "ok", "down")][string]$Mode)

    $versionJson = '{"Client":{"Version":"27.0.0"},"Server":{"Version":"27.0.0"}}'
    $infoJson = '{"KernelVersion":"5.15.167.4-microsoft-standard-WSL2","NCPU":8}'
    if ($onWindows) {
        $body = switch ($Mode) {
            "hang" { "@echo off`r`nping -n 127 127.0.0.1 >nul`r`n" }
            "down" { "@echo off`r`necho error during connect 1>&2`r`nexit /b 1`r`n" }
            "ok"   { "@echo off`r`nif ""%~1""==""version"" echo $versionJson`r`nif ""%~1""==""info"" if ""%~3""==""{{.NCPU}}"" (echo 8) else (echo $infoJson)`r`n" }
        }
        Set-Content -LiteralPath (Join-Path $fakeDir "docker.cmd") -Value $body -Encoding ASCII
    } else {
        $body = switch ($Mode) {
            "hang" { "#!/bin/sh`nsleep 127 &`nwait`n" }
            "down" { "#!/bin/sh`necho 'error during connect' >&2`nexit 1`n" }
            "ok"   { "#!/bin/sh`ncase `"`$1`" in`n  version) echo '$versionJson' ;;`n  info) if [ `"`$3`" = '{{.NCPU}}' ]; then echo 8; else echo '$infoJson'; fi ;;`nesac`n" }
        }
        $path = Join-Path $fakeDir "docker"
        Set-Content -LiteralPath $path -Value $body -NoNewline
        chmod +x $path
    }
}

function Get-HungChildCount {
    if ($onWindows) {
        return @(Get-CimInstance Win32_Process -Filter "Name = 'PING.EXE'" |
            Where-Object { $_.CommandLine -like "*-n 127*" }).Count
    }
    return @(pgrep -f "sleep 127").Count
}

$oldPath = $env:PATH
$oldTimeout = $env:ODS_DOCKER_PROBE_TIMEOUT_SECONDS
try {
    $env:PATH = $fakeDir + [System.IO.Path]::PathSeparator + $oldPath
    $env:ODS_DOCKER_PROBE_TIMEOUT_SECONDS = "2"

    Set-FakeDocker -Mode "hang"
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $hung = Test-DockerDesktop
    $watch.Stop()
    Assert-Equal $hung.Installed $true "hung engine: CLI installed"
    Assert-Equal $hung.Running $false "hung engine: not running"
    Assert-Equal $hung.NotResponding $true "hung engine: reported as not responding"
    Assert-Equal $hung.TimeoutSeconds 2 "hung engine: timeout honours override"
    if ($watch.Elapsed.TotalSeconds -gt 15) {
        throw "hung engine: Test-DockerDesktop took $([int]$watch.Elapsed.TotalSeconds)s, expected the 2s probe timeout"
    }

    Start-Sleep -Milliseconds 500
    Assert-Equal (Get-HungChildCount) 0 "hung engine: timed-out probe left no child process"

    $watch.Restart()
    $cpu = Get-DockerAvailableCpuCount
    $watch.Stop()
    Assert-Equal $cpu (Get-HostLogicalCpuCount) "hung engine: CPU count falls back to host count"
    if ($watch.Elapsed.TotalSeconds -gt 15) {
        throw "hung engine: Get-DockerAvailableCpuCount took $([int]$watch.Elapsed.TotalSeconds)s"
    }
    Start-Sleep -Milliseconds 500
    Assert-Equal (Get-HungChildCount) 0 "hung engine: CPU probe left no child process"

    Set-FakeDocker -Mode "down"
    $down = Test-DockerDesktop
    Assert-Equal $down.Running $false "stopped engine: not running"
    Assert-Equal $down.NotResponding $false "stopped engine: fails fast, not a timeout"

    Set-FakeDocker -Mode "ok"
    $ok = Test-DockerDesktop
    Assert-Equal $ok.Running $true "healthy engine: running"
    Assert-Equal $ok.NotResponding $false "healthy engine: responding"
    Assert-Equal $ok.Version "27.0.0" "healthy engine: server version"
    Assert-Equal $ok.WSL2Backend $true "healthy engine: WSL2 kernel detected"
    Assert-Equal (Get-DockerAvailableCpuCount) 8 "healthy engine: NCPU parsed"
} finally {
    $env:PATH = $oldPath
    $env:ODS_DOCKER_PROBE_TIMEOUT_SECONDS = $oldTimeout
    Remove-Item -LiteralPath $fakeDir -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "PASS: Docker preflight probes time out instead of hanging"
