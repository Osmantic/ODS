#!/usr/bin/env pwsh
<#
ODS Windows installer (WSL2-delegated MVP).
Runs preflight checks on Windows, then delegates to install-core.sh inside WSL.
#>

[CmdletBinding()]
param(
    [switch]$NoDelegate,
    [switch]$SkipDockerCheck,
    [string]$Distro = "",
    [string]$ReportPath = "$env:TEMP\\ods-windows-preflight.json",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PassthroughArgs
)

$ErrorActionPreference = "Stop"
$checks = @()

function Write-Section([string]$Message) {
    Write-Host ""
    Write-Host $Message -ForegroundColor Cyan
}

function Add-Check([string]$Id, [string]$Status, [string]$Message, [string]$Action = "") {
    $script:checks += [pscustomobject]@{
        id = $Id
        status = $Status
        message = $Message
        action = $Action
    }
}

function Convert-ToWslPath([string]$WindowsPath) {
    if ($WindowsPath -match '^([A-Za-z]):\\(.*)$') {
        $drive = $Matches[1].ToLower()
        $rest = $Matches[2] -replace '\\', '/'
        return "/mnt/$drive/$rest"
    }
    return $WindowsPath -replace '\\', '/'
}

Write-Host "ODS Windows installer (WSL2 path)" -ForegroundColor Cyan

Write-Section "Checking prerequisites"
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] WSL is not installed." -ForegroundColor Red
    Write-Host "Install WSL first: wsl --install"
    Add-Check "wsl-installed" "blocker" "WSL is not installed." "Run: wsl --install"
} else {
    Add-Check "wsl-installed" "pass" "WSL command is available."
}

$wslStatus = ""
if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
    try {
        $wslStatus = (& wsl.exe --status 2>$null | Out-String)
    } catch { }
    if ($wslStatus -match "Default Version:\s*2") {
        Add-Check "wsl-default-version" "pass" "WSL default version is 2."
    } else {
        Add-Check "wsl-default-version" "warn" "WSL default version is not clearly set to 2." "Run: wsl --set-default-version 2"
    }
}

$distroList = @()
if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
    $distroList = (& wsl.exe -l -q 2>$null | Where-Object { $_.Trim() -ne "" })
}
if (-not $distroList) {
    Write-Host "[ERROR] No WSL distro found." -ForegroundColor Red
    Write-Host "Install Ubuntu (example): wsl --install -d Ubuntu"
    Add-Check "wsl-distro" "blocker" "No WSL distro found." "Run: wsl --install -d Ubuntu"
} else {
    Add-Check "wsl-distro" "pass" "Detected WSL distro(s): $($distroList -join ', ')"
}

if ([string]::IsNullOrWhiteSpace($Distro)) {
    if ($distroList.Count -gt 0) {
        $Distro = $distroList[0].Trim()
    }
}

if (-not $SkipDockerCheck) {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host "[WARN] docker CLI not found on Windows PATH." -ForegroundColor Yellow
        Write-Host "Install Docker Desktop and enable WSL integration."
        Add-Check "docker-cli" "warn" "docker CLI not found on Windows PATH." "Install Docker Desktop and reopen terminal."
    } else {
        Add-Check "docker-cli" "pass" "docker CLI found."
        try {
            $dockerInfo = docker info 2>$null | Out-String
            $null = docker version --format '{{.Server.Version}}' 2>$null
            Write-Host "[OK] Docker Desktop engine reachable."
            Add-Check "docker-daemon" "pass" "Docker Desktop engine reachable."
            if ($dockerInfo -match "WSL2:\s*true") {
                Add-Check "docker-wsl2" "pass" "Docker reports WSL2 backend enabled."
            } else {
                Add-Check "docker-wsl2" "warn" "Docker WSL2 backend not confirmed from docker info output." "Enable 'Use the WSL2 based engine' in Docker Desktop settings."
            }
        } catch {
            Write-Host "[WARN] Docker Desktop not reachable yet." -ForegroundColor Yellow
            Write-Host "Start Docker Desktop before running install for real."
            Add-Check "docker-daemon" "warn" "Docker Desktop not reachable." "Start Docker Desktop and retry."
        }
    }
}

if ($Distro) {
    try {
        $wslDocker = (& wsl.exe -d $Distro -- bash -lc "command -v docker >/dev/null && echo ok || echo missing" 2>$null).Trim()
        if ($wslDocker -eq "ok") {
            Add-Check "wsl-docker-cli" "pass" "docker CLI available inside WSL distro '$Distro'."
        } else {
            Add-Check "wsl-docker-cli" "warn" "docker CLI unavailable inside WSL distro '$Distro'." "Enable Docker Desktop WSL integration for this distro."
        }
    } catch {
        Add-Check "wsl-docker-cli" "warn" "Could not verify docker CLI inside WSL distro '$Distro'." "Open WSL and run: docker info"
    }
}

if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    Write-Host "[OK] NVIDIA tooling detected on Windows host."
    Add-Check "windows-nvidia-smi" "pass" "nvidia-smi available on Windows host."
} else {
    Write-Host "[INFO] nvidia-smi not found on Windows host (non-NVIDIA or not installed)."
    Add-Check "windows-nvidia-smi" "warn" "nvidia-smi not detected on Windows host." "Install/update NVIDIA driver if targeting NVIDIA acceleration."
}

if ($Distro) {
    try {
        $wslNvidia = (& wsl.exe -d $Distro -- bash -lc "if command -v nvidia-smi >/dev/null 2>&1; then nvidia-smi -L >/dev/null 2>&1 && echo ok || echo missing; else echo missing; fi" 2>$null).Trim()
        if ($wslNvidia -eq "ok") {
            Add-Check "wsl-nvidia-smi" "pass" "NVIDIA GPU visible inside WSL."
        } else {
            Add-Check "wsl-nvidia-smi" "warn" "NVIDIA GPU not visible inside WSL." "Verify WSL GPU support and Docker Desktop GPU passthrough."
        }
    } catch {
        Add-Check "wsl-nvidia-smi" "warn" "Could not verify NVIDIA GPU inside WSL." "Open WSL and run: nvidia-smi"
    }
}

try {
    $blockers = @($checks | Where-Object { $_.status -eq "blocker" }).Count
    $warnings = @($checks | Where-Object { $_.status -eq "warn" }).Count
    $report = [pscustomobject]@{
        version = "1"
        generated_at = (Get-Date).ToUniversalTime().ToString("o")
        distro = $Distro
        summary = [pscustomobject]@{
            checks = $checks.Count
            blockers = $blockers
            warnings = $warnings
            can_proceed = ($blockers -eq 0)
        }
        checks = $checks
    }
    $report | ConvertTo-Json -Depth 8 | Set-Content -Path $ReportPath -Encoding UTF8
    Write-Host "[INFO] Preflight report: $ReportPath"
} catch {
    Write-Host "[WARN] Could not write preflight report: $($_.Exception.Message)" -ForegroundColor Yellow
}

if (@($checks | Where-Object { $_.status -eq "blocker" }).Count -gt 0) {
    Write-Host "[ERROR] Preflight blockers found. Fix them, then retry." -ForegroundColor Red
    $checks | Where-Object { $_.status -eq "blocker" } | ForEach-Object {
        Write-Host "  - $($_.message)" -ForegroundColor Red
        if ($_.action) { Write-Host "    Fix: $($_.action)" }
    }
    exit 1
}

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$repoRootWsl = Convert-ToWslPath $repoRoot
$argsString = ""
if ($PassthroughArgs) {
    $escaped = $PassthroughArgs | ForEach-Object { "'" + ($_ -replace "'", "'\\''") + "'" }
    $argsString = ($escaped -join " ")
}

Write-Section "WSL delegation target"
Write-Host "Repo path (Windows): $repoRoot"
Write-Host "Repo path (WSL):     $repoRootWsl"

$wslCommand = "cd '$repoRootWsl' && bash install-core.sh $argsString"
Write-Host "Command:"
Write-Host "  wsl.exe bash -lc `"$wslCommand`""

if ($NoDelegate) {
    Write-Host ""
    Write-Host "Delegation skipped (--NoDelegate)." -ForegroundColor Yellow
    exit 0
}

Write-Section "Running installer in WSL"
if ($Distro) {
    & wsl.exe -d $Distro bash -lc $wslCommand
} else {
    & wsl.exe bash -lc $wslCommand
}
exit $LASTEXITCODE
