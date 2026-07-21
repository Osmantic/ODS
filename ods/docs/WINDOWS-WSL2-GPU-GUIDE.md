# Windows WSL2 GPU Guide for ODS

Complete guide for running ODS on Windows with WSL2 and NVIDIA GPU
passthrough. AMD Strix Halo installs also use Docker Desktop + WSL2 for the
service stack, but local inference runs through the Windows host accelerated
path selected by `install.ps1`.

## Quick Verification

After installation, verify GPU is accessible:

```powershell
# In PowerShell (Windows side)
wsl nvidia-smi

# In WSL Ubuntu
wsl
nvidia-smi

# In Docker container
docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi
```

All three should show your GPU. If any fail, see troubleshooting below.

---

## Prerequisites

### Required
- Windows 10 version 2004+ (build 19041) or Windows 11
- WSL2 enabled
- Docker Desktop with WSL2 backend
- NVIDIA GPU with 8GB+ VRAM
- Latest NVIDIA drivers (Game Ready or Studio)

### Not Required (Common Mistake)
- **Do NOT install NVIDIA drivers inside WSL2** — Windows drivers provide GPU access to WSL2
- **Do NOT install CUDA toolkit in WSL2** — containers include their own CUDA

---

## Installation Steps

### Step 1: Enable WSL2

```powershell
# Run as Administrator in PowerShell
wsl --install
```

Restart when prompted. This installs WSL2 and Ubuntu by default.

**Verify:**
```powershell
wsl --status
# Should show: Default Version: 2
```

### Step 2: Install Docker Desktop

1. Download from https://docker.com/products/docker-desktop
2. During install, **check "Use WSL2 instead of Hyper-V"**
3. After install, verify WSL2 backend:
   - Open Docker Desktop → Settings → General
   - Confirm "Use the WSL2 based engine" is checked

### Step 3: Install NVIDIA Drivers

Download latest drivers from https://www.nvidia.com/drivers

**Verify driver includes WSL2 support:**
```powershell
# Driver version 465.21 or later includes WSL2 support
# Game Ready drivers work fine for compute
```

### Step 4: Run ODS Installer

```powershell
git clone https://github.com/Osmantic/ODS.git
cd ODS
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install.ps1
```

---

## Common Issues

### Issue: "nvidia-smi not found" in WSL2

**Cause:** WSL2 doesn't have GPU support enabled or NVIDIA driver missing.

**Fix:**
```powershell
# On Windows side — check driver
nvidia-smi
# If this fails, install NVIDIA drivers on Windows

# Verify WSL2 has GPU support
wsl cat /proc/driver/nvidia/version
# Should show driver version
```

### Issue: GPU works in WSL2 but not in Docker

**Symptoms:**
- `wsl nvidia-smi` shows GPU ✓
- `docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi` fails ✗

**Cause:** Docker Desktop not using WSL2 backend.

**Fix:**
1. Open Docker Desktop
2. Settings → General → Check "Use the WSL2 based engine"
3. Settings → Resources → WSL Integration → Enable integration with your distro
4. Click "Apply & Restart"

**Verify:**
```powershell
docker info | findstr WSL
# Should show: WSL2: true
```

### Issue: "GPU access blocked by the operating system"

**Symptoms:**
```
docker: Error response from daemon: OCI runtime create failed: 
container_linux.go:380: starting container process caused: 
process_linux.go:545: container init caused: Running hook #0:: 
error running hook: exit status 1, stderr: nvidia-container-cli: 
initialization error: driver rpc error: failed to process request
```

**Cause:** Windows Defender or other security software blocking GPU access.

**Fix:**
1. Add Docker Desktop to Windows Defender exclusions:
   - Windows Security → Virus & threat protection → Manage settings
   - Add or remove exclusions → Add an exclusion → Folder
   - Add `C:\Program Files\Docker`

2. If using third-party antivirus, temporarily disable or add similar exclusion.

3. Restart Docker Desktop.

### Issue: CUDA version mismatch errors

**Symptoms:**
```
nvidia-container-cli: requirement error: unsatisfied condition: cuda>=11.6
```

**Cause:** Windows NVIDIA driver is too old for the container's CUDA version.

**Fix:** Update NVIDIA drivers on Windows side. The driver in WSL2 comes from Windows — you don't install CUDA drivers in WSL2.

### Issue: Out of memory errors

**Cause:** WSL2 defaults to using 50% of available RAM.

**Fix:** Create `.wslconfig` to increase memory:

```powershell
# In PowerShell (Windows side)
notepad "$env:USERPROFILE\.wslconfig"
```

Add:
```ini
[wsl2]
memory=24GB
processors=8
swap=4GB
```

Save, then:
```powershell
wsl --shutdown
# Restart WSL2
```

### Issue: PowerShell execution policy blocks script

**Symptoms:**
```
.\install.ps1 : File cannot be loaded because running scripts is disabled on this system.
```

**Fix (temporary, per-session):** run the installer through a one-shot bypass:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

---

## Performance Tuning

### Optimal `.wslconfig` for ODS

Create `%USERPROFILE%\.wslconfig`:

```ini
[wsl2]
# Memory: Leave 4-8GB for Windows, rest for WSL2
memory=20GB
processors=8
swap=4GB
swapFile=C:\temp\wsl-swap.vhdx
localhostForwarding=true
# Disable Windows interoperability if not needed (slight performance gain)
# interop.enabled=false
```

After editing:
```powershell
wsl --shutdown
# WSL2 will restart with new settings
```

### Docker Desktop Resource Limits

1. Open Docker Desktop
2. Settings → Resources → Advanced
3. Set:
   - CPUs: 75% of available cores
   - Memory: 75% of available RAM
   - Swap: 2GB

---

## Verification Checklist

Before reporting issues, verify:

- [ ] Windows 10 build 19041+ or Windows 11
- [ ] `wsl --status` shows Default Version: 2
- [ ] `wsl nvidia-smi` shows GPU info
- [ ] Docker Desktop is running
- [ ] Docker Desktop uses WSL2 backend (Settings → General)
- [ ] WSL integration enabled for Ubuntu (Settings → Resources → WSL Integration)
- [ ] `docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi` shows GPU

---

## Quick Commands Reference

```powershell
# Restart WSL2 (fixes many issues)
wsl --shutdown

# Check WSL2 distros
wsl -l -v

# Set WSL2 as default
wsl --set-default-version 2

# Check Docker WSL2 backend
docker info | findstr WSL

# View WSL2 logs (for debugging)
Get-Content "$env:LOCALAPPDATA\Packages\CanonicalGroupLimited.UbuntuonWindows_79rhkp1fndgsc\LocalState\ext4.vhdx"
```

---

## Getting Help

If you've verified the checklist and still have issues:

1. After install, run diagnostics: `cd $env:USERPROFILE\ods; .\ods.ps1 report`
2. Check WSL2 GPU issues: https://github.com/microsoft/WSL/issues?q=label%3Agpu
3. ODS Discord: https://discord.gg/clawd

**When reporting issues, include:**
- Output of `wsl nvidia-smi`
- Output of `docker info | findstr WSL`
- Windows build number: `winver`
- Docker Desktop version
