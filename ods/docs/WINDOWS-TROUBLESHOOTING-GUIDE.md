# Windows Troubleshooting Guide for ODS

*For non-technical users installing ODS on Windows*

---

## ⚡ Quick Fixes (Try These First)

| Problem | Quick Fix |
|---------|-----------|
| "Windows won't run the installer" | Open a normal PowerShell in the cloned repo and run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| "PowerShell won't run scripts" | Use the per-session execution policy bypass below |
| "Docker not found" | Install Docker Desktop first |
| "GPU not detected" | Update NVIDIA drivers |
| "Installation hangs" | Check internet connection, wait 30 min for model download |
| "Docker Desktop cannot bind-mount..." | Add `C:\Users\<you>\ods` to Docker Desktop -> Settings -> Resources -> File Sharing |
| "Docker could not download alpine..." | Run `docker pull alpine:3.20`, then re-run the installer |

Do not use "Run as administrator" for the ODS installer unless you
are intentionally accepting admin-owned files under your user profile. The
Windows preflight warns because `.opencode`, `.env`, and `data/` should normally
belong to your regular account.

### Lemonade MSI fails during an AMD install

Keep the ODS installer in a normal PowerShell session. ODS installs the
Lemonade runtime for the current user under `%LOCALAPPDATA%\lemonade_server`;
it does not require an Administrator shell or an all-users `Program Files`
installation. The installer writes a verbose MSI log under the ODS install directory:

```text
<ODS install directory>\logs\lemonade-msi-install.log
```

With the default location this is `%USERPROFILE%\ods\logs\lemonade-msi-install.log`.
If the installer was run with `-InstallDir`, use that directory instead.

If Lemonade still fails, attach the installer output plus that log after
reviewing it for local paths. ODS falls back to native Vulkan `llama-server`
when Lemonade cannot be installed, but the installer output will make the
Lemonade failure explicit.

---

## Docker Compose failed (during install or `ods.ps1`)

If **`install-windows.ps1`** stops with **docker compose up failed**, or **`.\ods.ps1`** reports a compose error on **start / stop / restart / update**, the installer and CLI print a **COMPOSE FAILURE DIAGNOSTICS** block. Please save that entire block when asking for help (Discord, GitHub, etc.).

What the diagnostics include (in order):

1. **`docker version`** — confirms the Docker client can talk to the engine.
2. **`docker info`** (first lines) — daemon state, WSL2, disk, etc.
3. **`docker compose … config`** (last lines of output) — merged compose after variable substitution (uses `.env` when present). **This output can contain secret values** (API keys, tokens); redact before pasting into public GitHub issues. If there is a YAML merge or syntax error, it often appears here.
4. **`docker compose … ps -a`** — which containers exist and their state.

**Things to check on your machine before re-running:**

- Docker Desktop is **running** (whale icon in the tray) and **WSL 2** is enabled for the engine (Settings → General).
- Docker Desktop can pull the small bind-mount probe image: `docker pull alpine:3.20`.
- Docker Desktop file sharing includes the **installed** ODS folder, usually `C:\Users\<you>\ods`, not just the cloned `ODS` source folder.
- No other app is blocking the same **ports** as in `.env` (e.g. another stack using 3000, 8080, 11434).
- Enough **disk space** for images and volumes.
- If you edited compose files or added overrides, temporarily remove **`docker-compose.override.yml`** and try again.

For GPU and WSL2-specific steps, see **WINDOWS-WSL2-GPU-GUIDE.md** in the same `docs` folder.

---

## Generate a support report (`ods.ps1 report`)

If install/runtime problems are hard to reproduce, generate a structured Windows report before opening an issue:

```powershell
.\ods\installers\windows\ods.ps1 report
```

This creates:

- `artifacts/windows-report/report.json` (full diagnostics payload)
- `artifacts/windows-report/report.txt` (human-readable summary)

The report includes platform/GPU basics, compose flags, `docker version`, `docker info`, `docker compose ... config`, `docker compose ... ps -a`, and key local health checks.

**Privacy:** `docker compose config` in the bundle can interpolate values from `.env` (including API keys and other secrets). Open `report.json`, search for sensitive strings, and redact or replace them before attaching to **public** GitHub issues. Discord or private support channels may still need care if you paste large excerpts.

Attach `report.json` to GitHub issues or Discord support threads after review.

---

## Before You Start

### What You Need

**Required:**
- Windows 10 (version 2004 or newer) OR Windows 11
- NVIDIA graphics card (GPU) recommended (CPU-only works with smaller models)
- 4GB+ system RAM (16GB+ recommended, 32GB ideal)
- 15GB+ free disk space (50GB recommended)
- Internet connection

**Not Required (Common Confusion):**
- ❌ You do NOT need Linux knowledge
- ❌ You do NOT need to install CUDA
- ❌ You do NOT need to buy anything extra

### How Long Will This Take?

| Step | Time |
|------|------|
| Install WSL2 | 5-10 minutes |
| Install Docker Desktop | 5-10 minutes |
| Run ODS installer | 5 minutes |
| Download AI model (first time only) | 20-40 minutes |
| **Total first time** | **45-60 minutes** |

**The AI model downloads automatically.** This is the longest part. Be patient.

---

## Step-by-Step Installation

### Step 1: Check Your Windows Version

1. Press `Windows key + R`
2. Type `winver` and press Enter
3. Look at the version number:
   - **Windows 10:** Need version 2004 or higher (build 19041+)
   - **Windows 11:** Any version works

**If your Windows is too old:** Update Windows before continuing.

### Step 2: Install WSL2 (Windows Subsystem for Linux)

1. Right-click the Start button → Select "Windows PowerShell (Admin)" or "Terminal (Admin)"
2. Type this command and press Enter:
   ```powershell
   wsl --install
   ```
3. Wait for installation to complete
4. **Restart your computer** when prompted

**Verify WSL2 worked:**
1. Open PowerShell again
2. Type: `wsl --status`
3. You should see "Default Version: 2"

**Common Problem:** "WSL already installed but wrong version"
- Fix: `wsl --set-default-version 2`

### Step 3: Install Docker Desktop

1. Go to https://docker.com/products/docker-desktop
2. Click "Download for Windows"
3. Run the installer
4. **Important:** When asked, check "Use WSL2 instead of Hyper-V"
5. Finish installation and start Docker Desktop
6. Wait for Docker Desktop to fully start (you'll see the whale icon in your system tray)

**Verify Docker works:**
1. Open PowerShell
2. Type: `docker info`
3. You should see information about Docker (not an error)

### Step 4: Install NVIDIA Drivers

1. Go to https://www.nvidia.com/drivers
2. Click "Download"
3. Run the installer with default options
4. Restart your computer

**Verify GPU works:**
1. Open PowerShell
2. Type: `nvidia-smi`
3. You should see your GPU name and driver version

### Step 5: Run ODS Installer

1. Open a normal PowerShell window.
2. Clone the repository and enter it.
3. Run these commands:
   ```powershell
   git clone https://github.com/Osmantic/ODS.git
   cd ODS
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   .\install.ps1
   ```

**If PowerShell gives an error about execution policy:**
- Run the same installer through a one-shot bypass:
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\install.ps1
  ```

### Step 6: Wait for Model Download

The installer will:
1. Detect your GPU and hardware
2. Download the right AI model for your system
3. Start all the services

**This can take 20-40 minutes on first run.** The model is several gigabytes.

To watch progress:
```powershell
docker compose logs -f llama-server
```

When you see "Application startup complete" — it's ready!

### Step 7: Access Your AI

Open your web browser and go to: **http://localhost:3000**

You should see the Open WebUI interface. Start chatting!

---

## Common Problems & Solutions

### Problem: "The installer warns that I am Administrator"

**Solution:** Close that terminal and re-open a normal PowerShell window. Then
run:

```powershell
cd path\to\ODS
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install.ps1
```

Continuing as Administrator can leave user-level files owned by the elevated
account and make later updates harder.

### Problem: "PowerShell says running scripts is disabled"

**Symptoms:**
```
File cannot be loaded because running scripts is disabled on this system.
```

**Solution:**
Run PowerShell with bypass policy:
```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

### Problem: "Docker Desktop is not running"

**Symptoms:**
```
Docker Desktop is not running. Please start Docker Desktop and try again.
```

**Solution:**
1. Look for the Docker whale icon in your system tray (bottom-right corner)
2. If you don't see it, search for "Docker Desktop" in the Start menu and open it
3. Wait for it to fully start (the whale icon stops animating)
4. Try the installer again

### Problem: "GPU not detected" or "nvidia-smi not found"

**Symptoms:**
- Installer says "No GPU detected"
- `nvidia-smi` command doesn't work

**Solutions (try in order):**

**1. Check if you have an NVIDIA GPU:**
- Right-click on your desktop → "NVIDIA Control Panel"
- If this opens, you have an NVIDIA GPU
- If you don't see this option, you may have AMD or Intel graphics (not supported)

**2. Update drivers:**
- Go to https://www.nvidia.com/drivers
- Download and install latest drivers
- Restart computer
- Try again

**3. Check if GPU works in WSL:**
```powershell
wsl nvidia-smi
```
- If this shows your GPU, the problem is with Docker
- If this fails, the problem is with WSL or drivers

### Problem: "GPU works in WSL but not in Docker"

**Symptoms:**
- `wsl nvidia-smi` works (shows GPU)
- `docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi` fails

**Solution:**
1. Open Docker Desktop
2. Click the gear icon (Settings)
3. Go to "General"
4. Check "Use the WSL2 based engine"
5. Click "Apply & Restart"
6. Go to "Resources" → "WSL Integration"
7. Turn on integration for "Ubuntu" (or your distro)
8. Click "Apply & Restart"
9. Try again

### Problem: "GPU access blocked by antivirus"

**Symptoms:**
```
docker: Error response from daemon: OCI runtime create failed: ... 
nvidia-container-cli: initialization error: driver rpc error
```

**Solution:**
1. Open Windows Security (search in Start menu)
2. Go to "Virus & threat protection"
3. Click "Manage settings"
4. Scroll to "Exclusions" → "Add or remove exclusions"
5. Click "Add an exclusion" → "Folder"
6. Add: `C:\Program Files\Docker`
7. Restart Docker Desktop
8. Try again

**If using third-party antivirus** (McAfee, Norton, etc.):
- Temporarily disable it
- Or add Docker to its exclusion list
- Restart Docker Desktop

### Problem: "Installation seems to hang"

**Symptoms:**
- Installer stops at "Pulling llama-server..." or similar
- No progress for a long time

**Solutions:**

**1. Check if it's actually downloading:**
```powershell
docker compose logs -f llama-server
```
- If you see download progress, just wait (can take 20-40 min)
- Press Ctrl+C to exit log view when done

**2. Check internet connection:**
- Open a web browser, verify you can access websites
- Slow internet = slow download

**3. Restart and try again:**
```powershell
wsl --shutdown
docker compose down
.\install.ps1
```

### Problem: "Out of memory" errors

**Symptoms:**
- Error messages about memory
- System becomes very slow
- Docker containers crash

**Solution:**
WSL2 uses 50% of your RAM by default. If you have 16GB, it only uses 8GB.

**Increase WSL2 memory:**
1. Open PowerShell
2. Type: `notepad "$env:USERPROFILE\.wslconfig"`
3. Add these lines:
   ```ini
   [wsl2]
   memory=12GB
   processors=4
   swap=4GB
   ```
4. Save the file
5. Run: `wsl --shutdown`
6. Try installation again

**Adjust based on your system:**
| Your RAM | WSL2 Memory Setting |
|----------|---------------------|
| 16GB | 10-12GB |
| 32GB | 20-24GB |
| 64GB | 40-48GB |

### Problem: "Port already in use"

**Symptoms:**
```
Bind for 0.0.0.0:3000 failed: port is already allocated
```

**Solution:**
Something else is using port 3000. You have two options:

**Option A: Stop the other program**
1. Open PowerShell as administrator
2. Find what's using the port:
   ```powershell
   netstat -ano | findstr :3000
   ```
3. Look at the last number (PID)
4. Stop it:
   ```powershell
   taskkill /PID <number> /F
   ```

**Option B: Use a different port**
1. Edit the `.env` file in your ODS folder
2. Find `WEBUI_PORT=3000`
3. Change to something else: `WEBUI_PORT=3001`
4. Restart: `docker compose up -d`
5. Access at http://localhost:3001

### Problem: "Model download keeps failing"

**Symptoms:**
- Download stops partway through
- Error about network or connection

**Solutions:**

**1. Check disk space:**
- You need at least 50GB free
- Check: Open File Explorer → This PC

**2. Stable internet:**
- Use wired connection if possible
- Don't let computer sleep during download

**3. Try again:**
```powershell
docker compose down
.\install.ps1
```

**4. Manual download (advanced):**
If automatic download keeps failing, see
[`MODEL-MANAGEMENT.md`](MODEL-MANAGEMENT.md) for the supported model directory,
catalog filename matching, and manual GGUF swap checklist.

### Problem: "Web UI loads but AI doesn't respond"

**Symptoms:**
- You can see the chat interface
- When you send a message, nothing happens or you get errors

**Solutions:**

**1. Check if llama-server is running:**
```powershell
docker compose ps
```
- You should see llama-server, webui, and other services "Up"

**2. Check llama-server logs:**
```powershell
docker compose logs llama-server
```
- Look for error messages
- If you see "CUDA out of memory", your GPU doesn't have enough VRAM

**3. Try a smaller model:**
If your GPU has <12GB VRAM, edit `.env`:
```
MODEL_NAME=Qwen/Qwen2.5-7B-Instruct-AWQ
```
Then restart:
```powershell
docker compose down
docker compose up -d
```

### Problem: "Everything was working but stopped"

**Symptoms:**
- Worked before, now doesn't
- After Windows update, driver update, etc.

**Solution:**
1. Restart Docker Desktop
2. If that doesn't work:
   ```powershell
   wsl --shutdown
   docker compose down
   docker compose up -d
   ```
3. If still not working:
   ```powershell
   docker compose down
   .\install.ps1
   ```

---

## How to Check if Everything is Working

Run these commands in PowerShell to verify your setup:

### 1. Check Windows Version
```powershell
winver
```
✅ Should open a window showing Windows 10 build 19041+ or Windows 11

### 2. Check WSL2
```powershell
wsl --status
```
✅ Should show "Default Version: 2"

### 3. Check GPU in Windows
```powershell
nvidia-smi
```
✅ Should show your GPU name, driver version, and memory

### 4. Check GPU in WSL
```powershell
wsl nvidia-smi
```
✅ Should show the same GPU information

### 5. Check GPU in Docker
```powershell
docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi
```
✅ Should show the same GPU information

### 6. Check ODS Services
```powershell
cd $env:USERPROFILE\ods
docker compose ps
```
✅ Should show llama-server, webui, and other services as "Up"

### 7. Test the AI
```powershell
curl http://localhost:8080/v1/models
```
✅ Should return a JSON response with model information

---

## Getting Help

### Before You Ask

Please run the diagnostic command and share the output:

```powershell
cd $env:USERPROFILE\ods
.\ods.ps1 report
```

Also share output from:
```powershell
wsl nvidia-smi
docker info
```

### Where to Get Help

1. **ODS Discord:** https://discord.gg/clawd
2. **GitHub Issues:** https://github.com/Osmantic/ODS/issues

### What to Include When Asking for Help

- Windows version (from `winver`)
- GPU model (from `nvidia-smi`)
- The generated support report, after reviewing it for secrets
- What step you're stuck on
- Exact error message (copy/paste)

---

## Glossary

**WSL2** — Windows Subsystem for Linux. Lets you run Linux programs on Windows.

**Docker** — A tool that packages software so it runs the same way on any computer.

**GPU** — Graphics Processing Unit. Your NVIDIA graphics card. Needed to run AI models fast.

**VRAM** — Video RAM. Memory on your GPU. More = can run bigger AI models.

**Container** — A packaged application that includes everything it needs to run.

**llama-server** — The AI inference engine that runs the language model.

**Open WebUI** — The chat interface you see in your browser.

**Model** — The AI "brain" — a large file (several GB) that contains the trained neural network.

---

## Quick Reference Commands

```powershell
# Restart WSL2 (fixes many issues)
wsl --shutdown

# Restart ODS
docker compose down
docker compose up -d

# View AI model logs (see what's happening)
docker compose logs -f llama-server

# View all service logs
docker compose logs -f

# Check if services are running
docker compose ps

# Stop ODS
docker compose down

# Update ODS (pull latest)
git pull
docker compose pull
docker compose up -d
```

---

*Last updated: 2026-02-15*
*For ODS M5 (Clonable ODS Setup Server)*
