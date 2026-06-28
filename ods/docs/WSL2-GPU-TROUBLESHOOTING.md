# WSL2 GPU Troubleshooting Guide

*For ODS on Windows with NVIDIA GPUs*

## Prerequisites Checklist

Before troubleshooting, verify you have:

- [ ] Windows 10 version 2004+ (build 19041) or Windows 11
- [ ] WSL2 enabled (not WSL1)
- [ ] Docker Desktop with WSL2 backend
- [ ] NVIDIA GPU with driver version 470.76+ (for WSL2 support)

## Quick Diagnostic Commands

### 1. Check Windows Version
```powershell
winver
# Need: Build 19041 or higher
```

### 2. Check WSL Version
```powershell
wsl --status
# Should show: Default Version: 2
```

### 3. Check NVIDIA Driver (Windows)
```powershell
nvidia-smi
# Should show driver version and GPU info
```

### 4. Check GPU in WSL
```bash
# Inside WSL Ubuntu:
nvidia-smi
# Should show same output as Windows
```

### 5. Check GPU in Docker
```powershell
docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi
# Should show GPU info inside container
```

---

## Common Issues & Solutions

### Issue 1: "nvidia-smi not found" in WSL

**Symptoms:**
- `nvidia-smi` works in Windows PowerShell
- `nvidia-smi` fails in WSL Ubuntu

**Solutions:**

**A. Update WSL kernel**
```powershell
# In PowerShell (admin):
wsl --update
wsl --shutdown
# Reopen WSL
```

**B. Update NVIDIA drivers**
1. Download latest Game Ready or Studio drivers from [nvidia.com/drivers](https://www.nvidia.com/drivers)
2. Install with "Clean installation" option
3. Restart computer
4. Verify: `nvidia-smi` should now work in WSL

**C. Install CUDA toolkit in WSL (usually not needed)**
```bash
# Only if driver method fails:
wget https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt-get update
sudo apt-get -y install cuda-toolkit-12-2
```

---

### Issue 2: Docker can't access GPU

**Symptoms:**
- `nvidia-smi` works in WSL
- `docker run --gpus all` fails with "could not select device driver"

**Solutions:**

**A. Enable WSL2 backend in Docker Desktop**
1. Open Docker Desktop
2. Settings → General → Check "Use WSL2 based engine"
3. Settings → Resources → WSL Integration → Enable for Ubuntu
4. Apply & Restart

**B. Install NVIDIA Container Toolkit in WSL**
```bash
# In WSL Ubuntu:
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | sudo apt-key add -
curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | sudo tee /etc/apt/sources.list.d/nvidia-docker.list

sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker
```

**C. Configure Docker daemon**
```bash
# In WSL Ubuntu:
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

---

### Issue 3: "CUDA out of memory" immediately

**Symptoms:**
- GPU detected but llama-server crashes with OOM
- Works for small models, fails for large ones

**Solutions:**

**A. Check VRAM usage**
```bash
nvidia-smi
# Look at "Memory-Usage" - is something else using VRAM?
```

**B. Close GPU-heavy Windows apps**
- Game launchers (Steam, Epic)
- Video editors
- Other AI tools
- Multiple browser tabs with GPU acceleration

**C. Reduce model size in .env**
```bash
# Edit %USERPROFILE%\ods\.env
# Change to smaller model:
LLM_MODEL=Qwen/Qwen2.5-7B-Instruct  # Instead of 32B
```

**D. Enable GPU memory fraction limit**
```bash
# In docker-compose.base.yml, add to llama-server service:
environment:
  - PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:512
```

---

### Issue 4: "WSL2 not installed"

**Symptoms:**
- `wsl --status` shows error or WSL1

**Solution:**
```powershell
# In PowerShell (admin):
wsl --install
# Restart computer
wsl --set-default-version 2
wsl --install -d Ubuntu
```

---

### Issue 5: Docker Desktop won't start

**Symptoms:**
- Docker Desktop hangs on startup
- "Docker Desktop stopped" error

**Solutions:**

**A. Reset Docker Desktop**
1. Quit Docker Desktop completely
2. Delete: `%APPDATA%\Docker`
3. Delete: `%LOCALAPPDATA%\Docker`
4. Restart Docker Desktop

**B. Disable Hyper-V (if using WSL2)**
```powershell
# In PowerShell (admin):
dism.exe /Online /Disable-Feature:Microsoft-Hyper-V-All
# Restart, then re-enable WSL2:
dism.exe /Online /Enable-Feature /FeatureName:VirtualMachinePlatform /All /NoRestart
dism.exe /Online /Enable-Feature /FeatureName:Microsoft-Windows-Subsystem-Linux /All /NoRestart
# Restart again
```

**C. Update Docker Desktop**
Download latest from [docker.com/products/docker-desktop](https://docker.com/products/docker-desktop)

---

### Issue 6: Slow GPU performance in Docker

**Symptoms:**
- GPU works but inference is slow
- Much slower than native WSL performance

**Solutions:**

**A. Disable GPU power management**
```bash
# In WSL:
sudo nvidia-smi -pm 1
sudo nvidia-smi -pl 250  # Set to your GPU's TDP
```

**B. Check PCIe bandwidth**
```bash
nvidia-smi -q | grep -A3 "PCIe"
# Should show Gen3 x16 or Gen4 x16
```

**C. Verify using correct GPU**
```bash
# If multiple GPUs, set in docker-compose.nvidia.yml:
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          device_ids: ['0']  # Specific GPU
          capabilities: [gpu]
```

---

## Verification Commands

After fixing issues, verify everything works:

```powershell
# 1. Windows driver
nvidia-smi

# 2. WSL GPU access
wsl -e nvidia-smi

# 3. Docker GPU access
docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi

# 4. llama-server health (after ODS starts)
curl http://localhost:8080/health
```

---

## Getting Help

If you're still stuck:

1. **Check logs:**
   ```powershell
   cd $env:USERPROFILE\ods
   docker compose logs llama-server
   ```

2. **Post in GitHub Issues** with:
   - Windows version (`winver`)
   - GPU model and driver version (`nvidia-smi`)
   - WSL version (`wsl --status`)
   - Docker version (`docker version`)
   - Full error message

---

## References

- [NVIDIA CUDA on WSL](https://developer.nvidia.com/cuda/wsl)
- [Docker Desktop GPU Support](https://docs.docker.com/desktop/features/gpu/)
- [WSL2 Installation Guide](https://docs.microsoft.com/windows/wsl/install)
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)

---

*Part of ODS Documentation*
