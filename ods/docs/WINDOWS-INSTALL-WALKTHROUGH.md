# Legacy native Windows installation walkthrough

> **Release channel:** the install commands on this page fetch development `main`, which is not signed. A signed-source path is staged in [Verified Install Preview](VERIFIED_INSTALL_PREVIEW.md); it is not active until the first eligible immutable release is published, and historical `v3.0.0` is not eligible.

For new Pixel/Portal installations, use [Windows Quickstart](WINDOWS-QUICKSTART.md). The root `install.ps1` now guides Ubuntu/WSL2 setup. This page describes only the legacy native implementation and Windows runtime paths; it does not install Pixel in Ubuntu.

Step-by-step guide for installing ODS on Windows 10/11 with WSL2,
Docker Desktop, and NVIDIA or AMD GPU support.

---

## Prerequisites

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| Windows | 10 version 2004+ (build 19041) | Windows 11 |
| GPU | NVIDIA with 8GB VRAM or AMD Strix Halo | RTX 3060 12GB+, RTX 4090, or Ryzen AI MAX+ |
| RAM | 16GB | 32GB+ |
| Disk | 100GB free SSD | 200GB+ NVMe |
| WSL2 | Enabled | Latest kernel |
| Docker | Docker Desktop | Latest stable |

---

## Step 1: Enable WSL2

Open **PowerShell as Administrator** and run:

```powershell
wsl --install
```

This installs WSL2 and Ubuntu automatically.

**Verify:**
```powershell
wsl --status
# Should show: Default Version: 2
```

**Restart your computer** when prompted.

---

## Step 2: Install GPU Drivers

For NVIDIA:

1. Download latest drivers: https://www.nvidia.com/drivers
2. Install on Windows (do NOT install in WSL2)
3. Verify:
   ```powershell
   nvidia-smi
   # Should show GPU name, driver version, VRAM
   ```

**Note:** Windows drivers automatically provide GPU access to WSL2. No separate WSL driver needed.

For AMD Strix Halo, install the current AMD Windows graphics/compute driver
from AMD. The ODS installer selects the Windows host accelerated path
and falls back when Lemonade is unavailable.

---

## Step 3: Install Docker Desktop

1. Download: https://docker.com/products/docker-desktop
2. During install, **check "Use WSL2 instead of Hyper-V"**
3. After install, open Docker Desktop → Settings → General
4. Confirm **"Use the WSL 2 based engine"** is checked
5. Go to Settings → Resources → WSL Integration
6. Enable integration for **Ubuntu**

**Verify NVIDIA GPU in Docker:**
```powershell
docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi
```

Skip this NVIDIA CUDA container check on AMD systems.

---

## Step 4: Run ODS Installer

Open **PowerShell** (not as admin) and run:

```powershell
$ProgressPreference = "SilentlyContinue"
$odsSrc = Join-Path $env:TEMP ("ods-install-" + [guid]::NewGuid().ToString("N"))
$odsZip = Join-Path $odsSrc "ods-main.zip"
New-Item -ItemType Directory -Path $odsSrc | Out-Null
Invoke-WebRequest "https://github.com/Osmantic/ODS/archive/refs/heads/main.zip" -OutFile $odsZip
Expand-Archive -LiteralPath $odsZip -DestinationPath $odsSrc -Force
cd (Get-ChildItem -LiteralPath $odsSrc -Directory | Select-Object -First 1).FullName
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\ods\installers\windows\install-windows.ps1
```

The installer will:
- Detect your GPU and pick the right model tier
- Check prerequisites (WSL2, Docker, NVIDIA/AMD runtime path)
- Create the runtime directory at `$env:USERPROFILE\ods` by default,
  or at the path passed to `-InstallDir`
- Download and start the selected services

A fresh native install selects **Core Only** when you press Enter. Optional
voice, workflows, RAG, Hermes, ComfyUI, Perplexica, Privacy Shield, and Langfuse
stay off until selected. Choose **Full Stack** or pass `-All` to opt in. A normal
rerun reads the installed `.compose-flags` selection before copying source
files, so Enter keeps the existing enabled services. If that record is missing
or incomplete on an existing installation, choose a feature set explicitly;
the installer will not silently treat it as a fresh Core install. Selection
changes do not delete the optional services' data directories.
Hermes alone does not select SearXNG. Recommended, Perplexica, and legacy
OpenClaw still select it; a SearXNG service enabled through Dashboard Library
is retained on an ordinary installer rerun. Choosing **Core Only** explicitly
turns it off without deleting its data.

### Important: repo checkout vs runtime directory

The `ODS` folder you cloned is the source checkout used by the
installer. The running Windows install lives in `$env:USERPROFILE\ods`
by default, or in `$env:ODS_HOME` if you set that variable before install.
The runtime directory is where the installer writes `.env`, generated secrets,
model files, logs, data directories, and compose state.

Running the installer from another drive does not change the runtime target. If
you cloned the repo on another drive because `C:` is low on space, pass any
NTFS/ReFS target path with enough space explicitly:

```powershell
$installDir = "D:\Apps\ods"
.\ods\installers\windows\install-windows.ps1 -InstallDir $installDir
```

After installation, run management commands from the runtime directory:

```powershell
$installDir = "$env:USERPROFILE\ods"
# If you installed with -InstallDir, use that same path instead:
# $installDir = "D:\Apps\ods"
cd $installDir
.\ods.ps1 status
.\ods.ps1 logs llama-server
```

If you run raw `docker compose` from the cloned source checkout, Compose will
not see the generated `.env` and relative volume paths will point at the wrong
place. Manual Compose commands are supported, but run them from the runtime
directory:

```powershell
cd $installDir
docker compose ps
docker compose logs -f
```

For in-place development only, set `ODS_HOME` before running the installer so
the runtime is intentionally created inside your checkout:

```powershell
$env:ODS_HOME = "C:\path\to\ODS\ods"
.\ods\installers\windows\install-windows.ps1
```

An in-place source update cannot infer prior remote-provider Library choices
from newly checked-out disabled recipes. If an active route conflicts with
those markers, the installer stops with a recovery instruction before changing
the service choices. Restore the prior selection from a trusted backup, or
install from an independent source checkout into a fresh runtime directory.
Ordinary installs that copy from a separate checkout preserve existing choices
and migrate legacy active routes under the Library's Linux transaction lock.
The installer uses the pinned Python base image already required by Dashboard
API; a fresh native install may pull it earlier, before building Dashboard API.
Docker must be available even for this source-copy stage. Interrupted writers
are reconciled by their exact container identity before another transaction.

Manage Remote Provider Egress and Remote Provider SSH Tunnel through Dashboard
Library. Native `ods.ps1 enable/disable` refuses these two services, including
`-Force`, before stopping anything or changing selections. On older releases
they were core services; making them optional does not expose the generic
native CLI writer to their route lifecycle.

**First run takes 10-30 minutes** depending on download speed. Bootstrap mode
starts a small model first, then downloads and hot-swaps the full model in the
background.

### Installer Options

```powershell
# Specific tier with voice
.\ods\installers\windows\install-windows.ps1 -Tier 2 -Voice

# Keep the rest of an -All install, but leave both voice services off
.\ods\installers\windows\install-windows.ps1 -All -NoVoice

# Full stack with everything (re-enables Library-disabled voice services)
.\ods\installers\windows\install-windows.ps1 -All

# Add OpenCode, Claude Code, and Codex CLI to an otherwise normal install
.\ods\installers\windows\install-windows.ps1 -DevTools

# Disable their login task on a rerun, without removing binaries or stopping a session
.\ods\installers\windows\install-windows.ps1 -NoDevTools

# Simulate installer planning without making changes
.\ods\installers\windows\install-windows.ps1 -DryRun

# Wait for the full model instead of using bootstrap fast-start
.\ods\installers\windows\install-windows.ps1 -NoBootstrap

# Install runtime files on a specific drive/path
$installDir = "D:\Apps\ods"
.\ods\installers\windows\install-windows.ps1 -InstallDir $installDir
```

Fresh native Windows installs skip the developer tools. A rerun keeps them
selected only when the ODS OpenCode login task is enabled; a disabled task stays
disabled. `-NoDevTools` disables that ODS-owned login task without deleting
binaries or stopping a current session. The ODS host agent remains part of the
install in either case.

---

## Step 5: Verify Installation

### Check Services Are Running

```powershell
# In PowerShell
$installDir = "$env:USERPROFILE\ods"
# If you installed with -InstallDir, use that same path instead.
cd $installDir
docker compose ps
```

The running containers follow your selected services. A fresh native Core
install omits optional services such as SearXNG; selecting Hermes alone does
not add it. Full Stack includes it.

### Test GPU Access

```powershell
# Test inside llama-server container
docker exec -it ods-llama-server-1 nvidia-smi
```

### Open Web UI

Visit: **http://localhost:3000**

1. Create first account (becomes admin)
2. Select model from dropdown
3. Start chatting!

---

## Step 6: Run Diagnostics

```powershell
$installDir = "$env:USERPROFILE\ods"
# If you installed with -InstallDir, use that same path instead.
cd $installDir
.\ods.ps1 report
```

This verifies:
- WSL2 version and kernel
- Docker Desktop WSL2 backend
- NVIDIA GPU visibility at all layers
- Container health
- Model loading status

---

## Common First-Run Issues

### "Docker Desktop not running"
**Fix:** Start Docker Desktop from Start menu. Wait for whale icon to stabilize.

### "WSL2 not detected"
**Fix:** 
```powershell
wsl --update
wsl --shutdown
```
Then restart Docker Desktop.

### "nvidia-smi fails in Docker"
**Fix:** Ensure Docker Desktop WSL2 backend is enabled. Restart Docker Desktop after enabling.

### "Port 3000 already in use"
**Fix:** Edit `$installDir\.env`:
```
WEBUI_PORT=3001
```
Then:
```powershell
cd $installDir
docker compose up -d
```

### Model download stuck
**Fix:** Check disk space. Cancel with Ctrl+C, then restart installer — it resumes downloads.

---

## Next Steps

| Task | Command |
|------|---------|
| Stop ODS | `cd $installDir; .\ods.ps1 stop` |
| Start ODS | `cd $installDir; .\ods.ps1 start` |
| View logs | `cd $installDir; .\ods.ps1 logs` |
| Update | `cd $installDir; .\ods.ps1 update` |
| Add voice separately | In Dashboard Extensions Library, add Whisper STT or Kokoro TTS |
| Enable both voice services | Rerun the installer with `-Voice` |
| Disable both voice services | Rerun the installer with `-NoVoice` |
| Enable workflows | Add `-Workflows` flag |
| Support report | `cd $installDir; .\ods.ps1 report` |

---

## Getting Help

- **Troubleshooting:** See [WSL2-GPU-TROUBLESHOOTING.md](WSL2-GPU-TROUBLESHOOTING.md)
- **Docker optimization:** See [DOCKER-DESKTOP-OPTIMIZATION.md](DOCKER-DESKTOP-OPTIMIZATION.md)
- **FAQ:** See [FAQ.md](../FAQ.md)

---

## Uninstall

Start Docker Desktop first so ODS can remove the Docker side of the install.

```powershell
$installDir = "$env:USERPROFILE\ods"
# If you installed with -InstallDir, use that same path instead.
cd $installDir
.\ods.ps1 uninstall --force
```

Use `--keep-data` or `--keep-models` if you want to preserve local state.

If the runtime folder is partial and `.\ods.ps1` is missing, run the same cleanup from a source checkout:

```powershell
cd ODS
.\ods\installers\windows\ods.ps1 uninstall --force
```

The fallback path removes Docker containers, networks, and volumes labelled as the ODS compose project, so it still works when `.compose-flags` or the compose files were deleted before uninstall.

---

*Last updated: 2026-05-20*
