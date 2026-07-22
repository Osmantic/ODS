# ============================================================================
# ODS Windows Installer -- Phase 01: Pre-flight Checks
# ============================================================================
# Part of: installers/windows/phases/
# Purpose: Admin check, PowerShell version, Windows version, Docker Desktop,
#          initial disk space, Ollama conflict, compose file presence.
#
# Reads (set by install-windows.ps1 before dot-sourcing):
#   $installDir    -- target install path (for disk check)
#   $sourceRoot    -- cloned repo root (for compose file check)
#   $force         -- skip non-fatal failures
#   $nonInteractive -- suppress interactive prompts
#
# Writes:
#   $preflight_docker  -- result hashtable from Test-DockerDesktop
#                         (consumed by phase 02 for GPU support status)
#
# Modder notes:
#   Add new pre-flight checks (e.g., minimum Windows build) here.
#   All fatal exits use `throw "ODS_INSTALL_ABORTED"` (caught by the
#   orchestrator, which exits 1). Non-fatal issues use Write-AIWarn.
# ============================================================================

Write-Phase -Phase 1 -Total 13 -Name "PRE-FLIGHT CHECKS" -Estimate "~30 seconds"
Write-AI "Scanning your system for required components..."

# ── Admin check ─────────────────────────────────────────────────────────────
# ODS should NOT run as Administrator. User-level paths
# ($USERPROFILE\.opencode, data/, .env) created as admin become inaccessible
# to the normal user account, breaking OpenCode and ods-cli.
$_isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if ($_isAdmin) {
    Write-AIWarn "Running as Administrator is not recommended."
    Write-AI "  User-level paths (.opencode, data/, .env) created as admin may be"
    Write-AI "  inaccessible to your normal account. Re-run without 'Run as Administrator'."
    if (-not $nonInteractive) {
        $adminChoice = Read-Host "  Continue as Administrator anyway? [y/N]"
        if ($adminChoice -notmatch "^[yY]") {
            Write-AI "Exiting. Re-run this installer as your normal user account."
            exit 0
        }
    }
    Write-AIWarn "Continuing as Administrator. You may need to fix ownership later."
}

# ── PowerShell version ───────────────────────────────────────────────────────
$_psVer = Test-PowerShellVersion
Write-InfoBox "PowerShell:" "$($_psVer.Version)"
if (-not $_psVer.Sufficient) {
    Write-AIError "PowerShell 5.1 or later is required."
    Write-AI "  Download: https://github.com/PowerShell/PowerShell/releases"
    throw "ODS_INSTALL_ABORTED"
}
Write-AISuccess "PowerShell $($_psVer.Version) OK"

# ── Windows version ──────────────────────────────────────────────────────────
# WSL2 requires Windows 10 build 18362 (version 1903) or later.
try {
    $_build = [System.Environment]::OSVersion.Version.Build
    if ($_build -lt 18362) {
        Write-AIError "Windows 10 build 18362 (version 1903) or later is required for WSL2."
        Write-AI "  Your build: $_build. Update Windows and re-run."
        throw "ODS_INSTALL_ABORTED"
    }
    $_winVer = [System.Environment]::OSVersion.Version
    Write-AISuccess "Windows $($_winVer.Major).$($_winVer.Minor) (build $_build) OK"
} catch {
    if ($_.FullyQualifiedErrorId -eq "ODS_INSTALL_ABORTED") { throw }
    Write-AIWarn "Could not determine Windows build -- continuing"
}

# ── Install target visibility ────────────────────────────────────────────────
Write-InfoBox "Source checkout:" "$sourceRoot"
Write-InfoBox "Install target:" "$installDir"
$_sourceDrive = ""
$_installDrive = ""
if ($sourceRoot -match "^([A-Za-z]):") { $_sourceDrive = $Matches[1].ToUpperInvariant() }
if ($installDir -match "^([A-Za-z]):") { $_installDrive = $Matches[1].ToUpperInvariant() }
if ($_sourceDrive -and $_installDrive -and $_sourceDrive -ne $_installDrive) {
    Write-AIWarn "Source checkout is on ${_sourceDrive}: but the runtime install target is on ${_installDrive}:."
    Write-AI "  To install the runtime on another drive, rerun with:"
    Write-AI "  .\install.ps1 -InstallDir ${_sourceDrive}:\ods"
}

# ── Docker Desktop ───────────────────────────────────────────────────────────
$preflight_docker = Test-DockerDesktop

if (-not $preflight_docker.Installed) {
    Write-AIError "Docker Desktop is not installed."
    Write-AI "  Download: https://docs.docker.com/desktop/install/windows-install/"
    Write-AI "  After installing, enable WSL2: Docker Desktop > Settings > General > Use WSL 2 based engine"
    throw "ODS_INSTALL_ABORTED"
}
Write-AISuccess "Docker CLI found"

if (-not $preflight_docker.Running) {
    Write-AIError "Docker Desktop is not running."
    Write-AI "  Start it from the Start Menu, then re-run this installer."
    Write-Host "  & 'C:\Program Files\Docker\Docker\Docker Desktop.exe'" -ForegroundColor Cyan
    throw "ODS_INSTALL_ABORTED"
}
Write-AISuccess "Docker Desktop running (v$($preflight_docker.Version))"

if (-not $preflight_docker.WSL2Backend) {
    Write-AIWarn "WSL2 backend not detected. GPU passthrough requires WSL2."
    Write-AI "  Enable: Docker Desktop > Settings > General > Use WSL 2 based engine"
    if (-not $force) { throw "ODS_INSTALL_ABORTED" }
    Write-AIWarn "--Force specified, continuing without confirmed WSL2 backend."
}

# ── Filesystem POSIX-permission check ────────────────────────────────────────
# Phase 06 (.env generation) writes secrets and relies on filesystem ACLs.
# exFAT and FAT32 cannot represent per-user permissions at all, so the
# secrets file ends up readable by every account on the machine. Refuse
# install up front so the user can choose an NTFS/ReFS path. NTFS is fine
# from native Windows; we only flag NTFS as fatal when the install path is
# inside a 9p/DrvFs WSL mount (rare for this PowerShell installer, but kept
# for safety when somebody runs install.ps1 from inside a WSL shell).
$_fsProbe = $installDir
while ($_fsProbe -and -not (Test-Path -LiteralPath $_fsProbe)) {
    $_fsProbe = Split-Path -Parent $_fsProbe
}
if (-not $_fsProbe) { $_fsProbe = $installDir }

$_fsType = ""
try {
    $_di = [System.IO.DriveInfo]::new($_fsProbe)
    $_fsType = $_di.DriveFormat
} catch {
    # Path is on a non-Windows mount (e.g. WSL 9p) — DriveInfo throws.
    $_fsType = ""
}

# WSL detection (when running install.ps1 from inside WSL)
$_isWslPath = $false
if ($_fsProbe -match "^(?i)/mnt/[a-z]/" -or $env:WSL_DISTRO_NAME) {
    $_isWslPath = $true
}

Write-InfoBox "Filesystem:" $(if ($_fsType) { $_fsType } else { "unknown" })

$_fsFatal = $false
switch -Regex ($_fsType) {
    "^(exFAT|FAT32|FAT)$" { $_fsFatal = $true }
    "^NTFS$"              { if ($_isWslPath) { $_fsFatal = $true } }
}

if ($_fsFatal) {
    Write-AIError "INSTALL_DIR ($installDir) is on a $_fsType filesystem."
    Write-AIError "ODS stores secrets in .env and depends on filesystem"
    Write-AIError "permissions. $_fsType cannot represent per-user permissions,"
    Write-AIError "which would leave secrets readable by every account on this machine."
    Write-AI "  Pick a path on an NTFS/ReFS volume (e.g. C:\Users\<you>\ods) and re-run."
    throw "ODS_INSTALL_ABORTED"
}
Write-AISuccess "Filesystem supports POSIX-style permissions"

# Networked filesystem advisory (warn-only)
# NTFS ACLs on a mapped network drive (SMB/CIFS) or a UNC share are enforced
# by the SERVER, not this client. chmod-style local permission bits and any
# ACLs we set are advisory; another client of the same share may read .env
# regardless of how this Windows install enforces ACLs locally. Warn-only -
# installs to network homes are common and not always insecure.
$_fsNetworked = $false
$_fsNetworkType = ""
try {
    if ($installDir -match '^\\\\') {
        # UNC path - \\server\share\... - always networked. DriveInfo would
        # throw for these, so check the path shape first.
        $_fsNetworked = $true
        $_fsNetworkType = "UNC share"
    } elseif ($_di -and $_di.DriveType -eq 'Network') {
        # Mapped drive - DriveInfo.DriveType reports Network for SMB-mapped
        # drive letters (Z:\ pointing at \\server\share).
        $_fsNetworked = $true
        $_fsNetworkType = "mapped network drive"
    }
} catch {
    # Same graceful-degradation pattern as the FATAL detection above -
    # if we can't determine drive type, skip the warning silently.
    $_fsNetworked = $false
}

if ($_fsNetworked) {
    Write-AIWarn "INSTALL_DIR ($installDir) is on a $_fsNetworkType."
    Write-AIWarn ".env permissions are advisory - actual access control is governed by the share's ACL on the server."
    Write-AIWarn "If this share is exposed to other clients, sensitive credentials may be readable from those hosts."
}

# ── Docker Desktop file-sharing allowlist check ──────────────────────────────
# Bind-mounting a path outside the Docker Desktop file-sharing list fails at
# `docker compose up` with a cryptic OCI error. Probe with a throwaway Alpine
# container so we surface a clear message before any compose work starts.
# Pull the probe image first so a missing image/network problem is not reported
# as a Docker Desktop file-sharing problem.
$_shareOk = $true
$_shareErr = ""
$_probeImage = "alpine:3.20"
try {
    $_prevProbeEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & docker image inspect $_probeImage *> $null
    $_inspectExit = $LASTEXITCODE
    $ErrorActionPreference = $_prevProbeEap
    if ($_inspectExit -ne 0) {
        Write-AI "Downloading Docker bind-mount probe image ($_probeImage)..."
        $_prevPullEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $_pullOut = & docker pull $_probeImage 2>&1
        $_pullExit = $LASTEXITCODE
        $ErrorActionPreference = $_prevPullEap
        if ($_pullExit -ne 0) {
            Write-AIError "Docker could not download $_probeImage, which is required for the file-sharing probe."
            Write-AI "  Start Docker Desktop, confirm it has internet access, then run:"
            Write-AI "  docker pull $_probeImage"
            Write-AI "  Re-run this installer after the pull succeeds."
            if ($_pullOut) {
                Write-AI "  Pull output:"
                ($_pullOut -join "`n") -split "`n" | ForEach-Object { Write-Host "    $_" }
            }
            throw "Docker probe image download failed"
        }
    }

    # PowerShell -v argument needs careful quoting for paths with spaces.
    $_prevRunEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $_probeOut = & docker run --rm -v "${_fsProbe}:/check:ro" $_probeImage true 2>&1
    $_probeExit = $LASTEXITCODE
    $ErrorActionPreference = $_prevRunEap
    $_probeText = ($_probeOut -join "`n")
    if ($_probeExit -ne 0 -and $_probeText -match "not shared from the host|Mounts denied|file sharing|filesharing") {
        $_shareOk = $false
        $_shareErr = $_probeText
    } elseif ($_probeExit -ne 0) {
        Write-AIError "Docker bind-mount probe failed before compose startup."
        Write-AI "  This probe uses $_probeImage to verify Docker can mount:"
        Write-AI "  $installDir"
        Write-AI "  Confirm Docker Desktop is running, WSL2 backend is enabled, and $_probeImage is downloaded."
        if ($_probeText) {
            Write-AI "  Probe output:"
            $_probeText -split "`n" | ForEach-Object { Write-Host "    $_" }
        }
        throw "Docker bind-mount probe failed"
    }
} catch {
    if ($_prevProbeEap) { $ErrorActionPreference = $_prevProbeEap }
    if ($_prevPullEap) { $ErrorActionPreference = $_prevPullEap }
    if ($_prevRunEap) { $ErrorActionPreference = $_prevRunEap }
    if ($_.Exception.Message -in @(
        "Docker probe image download failed",
        "Docker bind-mount probe failed"
    )) {
        throw
    }
    $_shareOk = $false
    $_shareErr = $_.Exception.Message
}
if (-not $_shareOk) {
    Write-AIError "Docker Desktop cannot bind-mount $installDir."
    Write-AIError "Add the path to Docker Desktop > Settings > Resources > File Sharing,"
    Write-AIError "apply, then re-run this installer."
    Write-AI "  The probe image ($_probeImage) is already available; this is a file-sharing path issue."
    if ($_shareErr) {
        Write-AI "  Probe output:"
        $_shareErr -split "`n" | ForEach-Object { Write-Host "    $_" }
    }
    throw "Docker Desktop cannot bind-mount $installDir"
}
Write-AISuccess "Docker Desktop file sharing OK"

# ── Initial disk space check ─────────────────────────────────────────────────
# 20 GB minimum before model size is known (tier-aware check happens in phase 04).
$_disk = Test-DiskSpace -Path $installDir -RequiredGB 20
Write-InfoBox "Disk free:" "$($_disk.FreeGB) GB on $($_disk.Drive)"
if (-not $_disk.Sufficient) {
    Write-AIError "At least 20 GB free space is required. Found $($_disk.FreeGB) GB."
    Write-AI "  Install target checked: $installDir"
    $_installDirHint = "<path-with-enough-space>\ods"
    if ($sourceRoot -match "^([A-Za-z]):") {
        $_installDirHint = "$($Matches[1].ToUpperInvariant()):\ods"
    }
    Write-AI "  Free up space on $($_disk.Drive), or rerun from the source checkout with:"
    Write-AI "  .\install.ps1 -InstallDir $_installDirHint"
    throw "ODS_INSTALL_ABORTED"
}
Write-AISuccess "Disk space OK ($($_disk.FreeGB) GB free)"

# ── Source file existence check ──────────────────────────────────────────────
# Verify the compose base file is present in the source tree.
$_composeBase = Join-Path $sourceRoot "docker-compose.base.yml"
if (-not (Test-Path $_composeBase)) {
    Write-AIError "docker-compose.base.yml not found in: $sourceRoot"
    Write-AI "  Make sure you are running this installer from the ODS clone:"
    Write-AI "  git clone https://github.com/Osmantic/ODS.git"
    Write-AI "  cd ODS && .\install.ps1"
    throw "ODS_INSTALL_ABORTED"
}
Write-AISuccess "Source tree OK"

# ── Ollama conflict detection ────────────────────────────────────────────────
# Ollama defaults to port 11434. On Windows, it runs as a tray app and
# Open WebUI may auto-discover it (shadowing llama-server on 8080).
$_ollamaProc = Get-Process -Name "ollama" -ErrorAction SilentlyContinue
if ($_ollamaProc) {
    Write-AIWarn "Ollama is running (PID $($_ollamaProc.Id)) and may conflict with ODS."
    Write-AI "  Open WebUI can auto-discover Ollama and prefer it over llama-server,"
    Write-AI "  causing 'model not found' errors in OpenCode and other host tools."
    Write-Host ""
    if (-not $nonInteractive) {
        $ollamaChoice = Read-Host "  Stop Ollama for this session? [Y/n]"
        if ($ollamaChoice -notmatch "^[nN]") {
            Stop-Process -Name "ollama" -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
            $_ollamaStill = Get-Process -Name "ollama" -ErrorAction SilentlyContinue
            if ($_ollamaStill) {
                Write-AIWarn "Ollama restarted automatically (likely in Windows Startup)."
                # Remove the startup shortcut so it does not respawn on next login
                $_lnk = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\Ollama.lnk"
                if (Test-Path $_lnk) {
                    Remove-Item $_lnk -Force -ErrorAction SilentlyContinue
                    Stop-Process -Name "ollama" -Force -ErrorAction SilentlyContinue
                    Start-Sleep -Seconds 2
                    if (-not (Get-Process -Name "ollama" -ErrorAction SilentlyContinue)) {
                        Write-AISuccess "Ollama stopped and removed from Windows Startup"
                    } else {
                        Write-AIWarn "Could not fully stop Ollama. Port conflicts may occur."
                        Write-AI "  Fix: Settings > Apps > Startup > disable Ollama"
                    }
                } else {
                    Write-AIWarn "Remove Ollama from Startup: Settings > Apps > Startup"
                }
            } else {
                Write-AISuccess "Ollama stopped"
            }
        } else {
            Write-AIWarn "Ollama left running. Open WebUI may prefer it over llama-server."
        }
    } else {
        Write-AIWarn "Ollama detected (PID $($_ollamaProc.Id)). Stop it manually to avoid conflicts."
    }
}

Write-AISuccess "Pre-flight checks passed"
