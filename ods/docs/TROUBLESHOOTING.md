# ODS Troubleshooting

Common issues and solutions.

---

## Installation Issues

### Docker Permission Denied

**Error:** `permission denied while trying to connect to the Docker daemon`

**Fix:**
```bash
# Add yourself to docker group
sudo usermod -aG docker $USER

# Log out and back in (or reboot)
# Verify with:
docker ps
```

### NVIDIA Container Toolkit Missing

**Error:** `could not select device driver "" with capabilities: [[gpu]]`

**Fix:**
```bash
# Install NVIDIA Container Toolkit
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### AMD GPU Devices Missing in LXD/LXC

**Error:** installer reports missing `/dev/kfd`, `/dev/dri`, or `/dev/dri/renderD*`, or AMD services never become healthy inside an LXD container.

**Cause:** LXD/LXC containers can expose enough host CPU/sysfs information for ODS to recognize AMD/Strix Halo hardware while still hiding the actual GPU device nodes that Docker must mount into ROCm containers.

**Fix for GPU acceleration:** pass the GPU devices from the LXD host into the container, then re-run the installer.

```bash
lxc config set <container> security.nesting=true
lxc config device add <container> gpu gpu
lxc config device add <container> kfd unix-char path=/dev/kfd
```

**CPU fallback:** if you do not need GPU acceleration inside the container, run:

```bash
GPU_BACKEND=cpu ./install.sh
```

Fresh installs now fall back to CPU mode automatically when AMD is auto-detected but the required device nodes are unavailable inside a container. If you explicitly force `GPU_BACKEND=amd`, the installer fails fast with passthrough guidance instead.

---

## Startup Issues

### llama-server Container Won't Start

**Check logs:**
```bash
docker compose logs llama-server
```

**Common causes:**

1. **Not enough VRAM:**
   - Reduce context: Edit `.env`, set `CTX_SIZE=4096`
   - Use smaller model: Set `LLM_MODEL=qwen2.5-7b-instruct`

2. **Model download failed:**
   - Check disk space: `df -h`
   - Restart: `docker compose restart llama-server`

3. **GPU not detected:**
   - Check: `nvidia-smi`
   - Restart Docker: `sudo systemctl restart docker`

### Open WebUI Shows "No Models Available"

**Cause:** llama-server is still loading the model.

**Check:**
```bash
# Watch llama-server logs
docker compose logs -f llama-server

# Wait for "Application startup complete"
```

**Model loading time:**
- 7B model: ~30 seconds
- 32B model: ~2 minutes
- 72B model: ~5 minutes

### Port Already in Use

**Error:** `Bind for 0.0.0.0:3000 failed: port is already allocated`

**Fix:**
1. Find what's using the port:
   ```bash
   lsof -i :3000
   ```

2. Change port in `.env`:
   ```bash
   WEBUI_PORT=3001
   ```

3. Restart:
   ```bash
   docker compose down && docker compose up -d
   ```

---

## Runtime Issues

### CUDA Out of Memory

**Error:** `torch.cuda.OutOfMemoryError: CUDA out of memory`

**Fixes:**

1. **Reduce context window:**
   ```bash
   # In .env
   CTX_SIZE=4096  # or even 2048
   ```

2. **Reduce VRAM utilization:**
   ```bash
   # In .env
   GPU_UTIL=0.8  # default is 0.9
   ```

3. **Use smaller model:**
   ```bash
   LLM_MODEL=qwen2.5-7b-instruct
   ```

### Responses Very Slow

**Possible causes:**

1. **First request (cold start):** Wait 30-60 seconds for model warm-up
2. **Swapping to disk:** Check `free -h` — if swap is heavily used, reduce context
3. **Network issue:** Verify GPU is being used: `watch nvidia-smi`

### Whisper Not Transcribing

**Check:**
```bash
docker compose logs whisper
```

**Common fixes:**
1. Whisper may need to download model on first use — wait
2. Check that Whisper is running: `docker compose ps whisper`
3. Check GPU memory — Whisper needs ~3GB for medium model

---

## Network Issues

### Can't Access WebUI Remotely

**By default, services bind to localhost only.**

To allow remote access:

1. **Warning:** Only do this on trusted networks!

2. Edit the compose file for your platform:
   - NVIDIA: `docker-compose.base.yml` + `docker-compose.nvidia.yml`
   - AMD Strix Halo: `docker-compose.base.yml` + `docker-compose.amd.yml`
   Then change ports, for example:
   ```yaml
   ports:
     - "0.0.0.0:3000:8080"  # Was "3000:8080"
   ```

3. Configure firewall:
   ```bash
   sudo ufw allow 3000
   ```

### Services Can't Communicate

**Check container network:**
```bash
docker network inspect ods-network
```

**Ensure all services use the same network** (default in our compose file).

---

## Data Issues

### Reset Everything

**Warning:** This deletes all data!

```bash
cd ~/ods  # or wherever you installed
./ods-uninstall.sh --force
./install.sh
```

### Backup Data

```bash
cd ~/ods
tar -czvf ods-backup-$(date +%Y%m%d).tar.gz data/
```

### Restore Data

```bash
cd ~/ods
docker compose down
tar -xzvf ods-backup-YYYYMMDD.tar.gz
docker compose up -d
```

---

## Extension runtime check (post-install)

After installation, phase 13 runs **manifest validation** and a **non-blocking** check of non-core services that have a compose fragment: it verifies whether each matching container exists, is running, and (when the manifest defines a health path and port) whether `http://127.0.0.1:<port><path>` responds.

**Run manually** from your install directory (e.g. `~/ods`):

```bash
bash scripts/extension-runtime-check.sh .
```

**Strict mode** (fails if any running service fails its health probe — useful in CI or scripted verification):

```bash
EXTENSION_RUNTIME_CHECK_STRICT=1 bash scripts/extension-runtime-check.sh .
```

If Docker is not running or not reachable, the script skips checks and exits successfully. Install **jq** and **curl** where possible so related tooling and health probes behave consistently.

If a service shows **running but health failed**, inspect logs and compose state:

```bash
cd ~/ods
docker compose ps
docker compose logs <service>
ods status
```

---

## Getting Help

1. **Check logs:**
   ```bash
   docker compose logs -f
   ```

2. **Check container status:**
   ```bash
   docker compose ps
   ```

3. **Check GPU:**
   ```bash
   nvidia-smi
   ```

4. **Check disk:**
   ```bash
   df -h
   ```

5. **Open an issue:** https://github.com/Osmantic/ODS/issues

---

*Built by The Collective*
