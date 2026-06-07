# Dream Server Windows Installation Walkthrough

Step-by-step guide for installing Dream Server on Windows 10/11 with WSL2,
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
from AMD. The Dream Server installer selects the Windows host accelerated path
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

## Step 4: Run Dream Server Installer

Open **PowerShell** (not as admin) and run:

```powershell
git clone https://github.com/Light-Heart-Labs/DreamServer.git
cd DreamServer
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install.ps1
```

The installer will:
- Detect your GPU and pick the right model tier
- Check prerequisites (WSL2, Docker, NVIDIA/AMD runtime path)
- Create the runtime directory at `$env:USERPROFILE\dream-server` by default,
  or at the path passed to `-InstallDir`
- Download and start all services

### Important: repo checkout vs runtime directory

The `DreamServer` folder you cloned is the source checkout used by the
installer. The running Windows install lives in `$env:USERPROFILE\dream-server`
by default, or in `$env:DREAM_HOME` if you set that variable before install.
The runtime directory is where the installer writes `.env`, generated secrets,
model files, logs, data directories, and compose state.

Running the installer from another drive does not change the runtime target. If
you cloned the repo on another drive because `C:` is low on space, pass any
NTFS/ReFS target path with enough space explicitly:

```powershell
$installDir = "D:\Apps\dream-server"
.\install.ps1 -InstallDir $installDir
```

After installation, run management commands from the runtime directory:

```powershell
$installDir = "$env:USERPROFILE\dream-server"
# If you installed with -InstallDir, use that same path instead:
# $installDir = "D:\Apps\dream-server"
cd $installDir
.\dream.ps1 status
.\dream.ps1 logs llama-server
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

For in-place development only, set `DREAM_HOME` before running the installer so
the runtime is intentionally created inside your checkout:

```powershell
$env:DREAM_HOME = "C:\path\to\DreamServer\dream-server"
.\install.ps1
```

**First run takes 10-30 minutes** depending on download speed. Bootstrap mode
starts a small model first, then downloads and hot-swaps the full model in the
background.

### Installer Options

```powershell
# Specific tier with voice
.\install.ps1 -Tier 2 -Voice

# Full stack with everything
.\install.ps1 -All

# Simulate installer planning without making changes
.\install.ps1 -DryRun

# Wait for the full model instead of using bootstrap fast-start
.\install.ps1 -NoBootstrap

# Install runtime files on a specific drive/path
$installDir = "D:\Apps\dream-server"
.\install.ps1 -InstallDir $installDir
```

---

## Step 5: Verify Installation

### Check Services Are Running

```powershell
# In PowerShell
$installDir = "$env:USERPROFILE\dream-server"
# If you installed with -InstallDir, use that same path instead.
cd $installDir
docker compose ps
```

You should see containers: `llama-server`, `open-webui`, `searxng`, etc.

### Test GPU Access

```powershell
# Test inside llama-server container
docker exec -it dream-server-llama-server-1 nvidia-smi
```

### Open Web UI

Visit: **http://localhost:3000**

1. Create first account (becomes admin)
2. Select model from dropdown
3. Start chatting!

---

## Step 6: Run Diagnostics

```powershell
$installDir = "$env:USERPROFILE\dream-server"
# If you installed with -InstallDir, use that same path instead.
cd $installDir
.\dream.ps1 report
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
| Stop Dream Server | `cd $installDir; .\dream.ps1 stop` |
| Start Dream Server | `cd $installDir; .\dream.ps1 start` |
| View logs | `cd $installDir; .\dream.ps1 logs` |
| Update | `cd $installDir; .\dream.ps1 update` |
| Enable voice | Add `-Voice` flag or edit `.env` |
| Enable workflows | Add `-Workflows` flag |
| Support report | `cd $installDir; .\dream.ps1 report` |

---

## Getting Help

- **Troubleshooting:** See [WSL2-GPU-TROUBLESHOOTING.md](WSL2-GPU-TROUBLESHOOTING.md)
- **Docker optimization:** See [DOCKER-DESKTOP-OPTIMIZATION.md](DOCKER-DESKTOP-OPTIMIZATION.md)
- **FAQ:** See [FAQ.md](../FAQ.md)

---

## Uninstall

```powershell
# Stop and remove containers
$installDir = "$env:USERPROFILE\dream-server"
# If you installed with -InstallDir, use that same path instead.
cd $installDir
$composeFlags = (Get-Content .compose-flags -Raw).Split(' ', [System.StringSplitOptions]::RemoveEmptyEntries)
docker compose @composeFlags down -v --remove-orphans

# Remove installation directory
Remove-Item -Recurse -Force $installDir
```

---

*Last updated: 2026-05-20*
