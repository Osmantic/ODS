# Docker Desktop Optimization Guide for Windows + WSL2

Running Large Language Models (LLMs) locally on Windows requires proper Docker Desktop configuration. This guide covers Windows-specific optimizations for ODS.

---

## Windows-Specific Settings

### 1. WSL2 Backend (Required)

Docker Desktop on Windows supports two backends: Hyper-V and WSL2. **You must use WSL2** for GPU support.

**Configure:**
1. Open Docker Desktop
2. Settings → General
3. Check **"Use the WSL 2 based engine"**
4. Click "Apply & Restart"

**Verify:**
```powershell
docker info | findstr "OS Type"
# Should show: OS Type: linux
```

### 2. WSL2 Integration

Enable WSL2 integration for your Linux distro:

1. Docker Desktop → Settings → Resources → WSL Integration
2. Enable **"Ubuntu"** (or your default distro)
3. Click "Apply & Restart"

**Verify:**
```powershell
wsl -d Ubuntu -e docker ps
# Should show running containers
```

### 3. Resource Allocation (Critical)

LLMs need significant resources. Default Docker limits are too low.

**Recommended Settings:**

| Hardware | CPU | Memory | Swap |
|----------|-----|--------|------|
| 16GB RAM | 4 cores | 12GB | 4GB |
| 32GB RAM | 8 cores | 24GB | 8GB |
| 64GB RAM | 12+ cores | 48GB | 16GB |

**Configure:**
1. Docker Desktop → Settings → Resources → Advanced
2. Set sliders to values above
3. Click "Apply & Restart"

**Note:** Docker restarts all containers when you change resources. Plan for brief downtime.

### 4. Disk Image Location

Move Docker disk image to fastest drive (preferably NVMe SSD).

**Configure:**
1. Docker Desktop → Settings → Resources → Advanced
2. Change **"Disk image location"** to `D:\Docker` (or your fast drive)
3. Click "Apply & Restart"

**Why this matters:**
- Docker images and containers live in this disk image
- NVMe SSD = 5-10x faster container startup
- Large models (20-40GB) load much faster

---

## General Optimization

## 1. Recommended Memory Allocation

For running LLMs, Docker needs sufficient memory to accommodate the model's requirements. The recommended memory allocation depends on the size of the LLM you intend to run:

- **Small to Medium Models**: Allocate at least 8GB of RAM.
- **Large Models**: Allocate at least 16GB of RAM, preferably 32GB or more.

To set the memory allocation in Docker Desktop:
1. Open Docker Desktop.
2. Go to **Settings** > **Resources** > **Memory**.
3. Set the memory limit based on the above recommendations.

## 2. GPU Resource Limits

Using a GPU can significantly accelerate LLM inference. Docker Desktop supports GPU acceleration through NVIDIA Docker. Ensure that you have the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html#docker) installed.

To expose the GPU to Docker containers:
1. Install the NVIDIA Container Toolkit following the [official documentation](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html#docker).
2. Restart Docker Desktop.
3. Verify GPU access by running a test container:
   ```bash
docker run --rm --gpus all nvidia/cuda:11.0-base nvidia-smi
   ```

## 3. WSL2 Backend Configuration

Docker Desktop on Windows supports two backends: Hyper-V and WSL2. For optimal performance, especially for GPU-accelerated workloads, use the WSL2 backend.

To configure Docker Desktop to use WSL2:
1. Open Docker Desktop.
2. Go to **Settings** > **General**.
3. Enable **Use the WSL 2 based engine**.
4. Restart Docker Desktop.

Ensure you have the latest version of WSL2 installed and that your default Linux distribution is set up correctly.

## 4. Networking Tips for Multi-container AI Stacks

When deploying multiple containers for an AI stack, proper networking is essential for efficient communication between services.

- **Use Docker Compose**: Define your multi-container applications using compose files. This ensures that all services are linked correctly.
- **Network Mode**: Use the `bridge` network mode for most scenarios. For better performance, consider using the `host` network mode if your containers need direct access to the host's network interfaces.
- **Service Discovery**: Use Docker's built-in DNS service discovery to resolve container names to IP addresses.

Example compose snippet:
```yaml
version: '3.8'
services:
  llm-service:
    image: your-llm-image
    ports:
      - "5000:5000"
    networks:
      - ai-network
  database-service:
    image: postgres
    networks:
      - ai-network
networks:
  ai-network:
```

## 5. Performance Tuning (Disk I/O, Caching)

Optimizing disk I/O and caching can improve the performance of your Docker containers.

- **Use SSDs**: Ensure that your system uses SSDs for storage to improve disk I/O performance.
- **Docker Storage Driver**: Use the `overlay2` storage driver, which is the default in recent versions of Docker.
- **Increase Swap Space**: Configure swap space to handle memory overflow gracefully. However, excessive swapping can degrade performance.

To check and modify the storage driver in Docker Desktop:
1. Open Docker Desktop.
2. Go to **Settings** > **Resources** > **Advanced**.
3. Ensure that the storage driver is set to `overlay2`.

## 6. Common Mistakes to Avoid

- **Insufficient Resources**: Allocating too little memory or CPU can severely impact performance. Always ensure that Docker has enough resources.
- **Incorrect Network Configuration**: Misconfigured networking can lead to slow communication between containers. Use Docker Compose to manage multi-container setups.
- **Ignoring Disk Performance**: Using HDDs instead of SSDs can significantly slow down your LLM inference. Upgrade to SSDs for better performance.
- **Neglecting GPU Setup**: Not properly setting up GPU acceleration can prevent you from taking advantage of hardware acceleration. Follow the NVIDIA Container Toolkit installation steps carefully.
- **Overlooking Docker Updates**: Regularly update Docker Desktop to benefit from performance improvements and security patches.

By following these guidelines, you can optimize Docker Desktop for running local LLMs on Windows, ensuring efficient and effective model inference.

---

## Windows-Specific Troubleshooting

### Issue: "Out of memory" crashes

**Cause:** Docker Desktop's default 2GB memory limit.

**Fix:** Increase memory allocation to at least 12GB (see Resource Allocation above).

### Issue: Slow container startup

**Cause:** Docker disk image on slow HDD or fragmented SSD.

**Fix:** 
1. Move disk image to NVMe SSD (see Disk Image Location above)
2. Defragment SSD (Windows optimize drives)
3. Prune unused images: `docker system prune -a`

### Issue: GPU not visible in containers

**Cause:** Wrong Docker backend or missing WSL2 integration.

**Fix:**
1. Verify WSL2 backend is enabled
2. Verify WSL2 integration for Ubuntu is on
3. Restart Docker Desktop
4. Test: `docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi`

### Issue: WSL2 distro won't start

**Cause:** WSL2 kernel out of date.

**Fix:**
```powershell
wsl --update
wsl --shutdown
```

### Issue: Port conflicts with Windows services

**Common conflicts:**
- Port 3000 (IIS, some apps)
- Port 5432 (PostgreSQL if installed natively)
- Port 6379 (Redis if installed natively)

**Fix:** Edit `.env` file to change ports:
```
WEBUI_PORT=3001
POSTGRES_PORT=5433
```

---

## Performance Monitoring (Windows)

### Check Resource Usage

**Docker Desktop Dashboard:**
- Shows CPU, memory, network per container
- Access: Click whale icon → Dashboard

**Windows Task Manager:**
- Shows overall Docker + WSL2 resource usage
- Look for `vmmemWSL` process (WSL2 memory)

**PowerShell:**
```powershell
# Docker stats live view
docker stats

# WSL2 memory usage
wsl -d Ubuntu -e free -h
```

### Expected Performance

With proper optimization:
- **Model load time:** 30-60 seconds for 32B AWQ
- **First token latency:** 0.5-2 seconds
- **Tokens/second:** 20-40 tok/s on RTX 4090

---

## Windows Best Practices

1. **Keep WSL2 kernel updated:** `wsl --update` monthly
2. **Restart Docker Desktop weekly:** Prevents memory leaks
3. **Use NVMe SSD for Docker image:** 5-10x performance gain
4. **Disable Windows Search indexing** on Docker directories
5. **Exclude Docker paths from antivirus** real-time scanning
6. **Enable Hardware-accelerated GPU scheduling** (Windows 11): Settings → System → Display → Graphics → Default graphics settings

---

*Last updated: 2026-02-13*
