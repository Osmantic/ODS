# Dream Server Windows Quickstart

## Getting Started

Dream Server is fully supported on Windows 10 2004+ and Windows 11 (NVIDIA and AMD). The installer detects your GPU, selects the right model, downloads it, starts all Docker services, and creates a Desktop shortcut.

**Prerequisites:** [Docker Desktop](https://www.docker.com/products/docker-desktop/) with WSL2 backend enabled. NVIDIA GPU recommended (CPU-only works with smaller models). 4GB+ RAM minimum, 16GB+ recommended.

Open **PowerShell as Administrator** and run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
git clone https://github.com/Light-Heart-Labs/DreamServer.git
cd DreamServer
.\install.ps1
```

The installer will:
- Detect your GPU (NVIDIA or AMD) and pick the right model tier
- Download the AI model for your hardware (~1.5GB bootstrap, full model in background)
- Start all Docker services
- Run health checks and create a Desktop shortcut

**First-run time:** 10-30 minutes depending on download speed. Bootstrap mode starts chatting in under 2 minutes while the full model downloads in background.

---

## Quick Commands

Manage Dream Server using `dream.ps1` from your install directory:

```powershell
cd $env:USERPROFILE\dream-server

.\dream.ps1 status              # Health checks + GPU status
.\dream.ps1 start               # Start all services
.\dream.ps1 stop                # Stop all services
.\dream.ps1 restart             # Restart all services
.\dream.ps1 logs llama-server   # Tail logs (any service name)
.\dream.ps1 update              # Pull latest images and restart
.\dream.ps1 report              # Generate diagnostics bundle
```

---

## Open the UI

Visit **http://localhost:3000** — the chat interface is ready after the installer completes.

First user becomes admin. Start chatting immediately.

---

## Bootstrap Mode (Faster Start)

The installer automatically uses bootstrap mode when applicable — a small model (~1.5 GB) downloads first so you can start chatting within 2 minutes, while the full model downloads in the background. Hermes-enabled installs run that bootstrap model at a 64K context floor, then promote the full local model context to 128K after the swap. No extra flags needed.

---

## Installer Flags

| Flag | What It Does |
|------|--------------|
| `-Tier 2` | Force specific tier (1-4) |
| `-Voice` | Enable Whisper + TTS |
| `-Workflows` | Enable n8n automation |
| `-Rag` | Enable Qdrant vector DB |
| `-Hermes` | Enable Hermes Agent explicitly |
| `-NoHermes` | Disable Hermes Agent |
| `-NoBootstrap` | Wait for the full model before launching |
| `-OpenClaw` | Enable deprecated OpenClaw agent framework |
| `-Comfyui` | Enable ComfyUI image generation |
| `-Langfuse` | Enable Langfuse LLM observability |
| `-All` | Recommended full stack enabled (OpenClaw remains opt-in) |
| `-Cloud` | Use cloud LLM provider instead of local |
| `-DryRun` | Simulate install without making changes |

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| "Docker not running" | Start Docker Desktop, wait for whale icon |
| "WSL2 not found" | `wsl --install` then restart |
| "nvidia-smi fails" | Update NVIDIA drivers; restart Docker Desktop |
| "Port in use" | Edit `.env`, change `WEBUI_PORT=3001` |
| Out of memory | Lower tier: `.\install.ps1 -Tier 1` |

Full guide: [WINDOWS-INSTALL-WALKTHROUGH.md](WINDOWS-INSTALL-WALKTHROUGH.md)

---

## System Requirements by Tier

| Tier | VRAM | Model | Use Case |
|------|------|-------|----------|
| 1 | 8-12GB | 7B Qwen | Basic chat, coding help |
| 2 | 12-20GB | 14B AWQ | Daily driver, good reasoning |
| 3 | 20-40GB | 32B AWQ | Power user, complex tasks |
| 4 | 40GB+ | 72B AWQ | Maximum capability |

---

## Architecture

```
Windows Host
  ├── Docker Desktop (WSL2 backend)
  │     ├── llama-server container (GPU accelerated)
  │     ├── Open WebUI (port 3000)
  │     ├── SearXNG search
  │     └── PostgreSQL + Qdrant
  └── WSL2 Ubuntu (file system, networking)
```

GPU access: Windows driver → WSL2 → Docker Container Toolkit → llama-server

---

## Files & Locations

| What | Where |
|------|-------|
| Install directory | `%LOCALAPPDATA%\DreamServer` |
| Config | `.env` file in install directory |
| Models | `%LOCALAPPDATA%\DreamServer\data\models\` |
| Logs | `docker compose logs` |
| Data | Docker volumes (auto-managed) |

---

## Updating

```powershell
cd $env:LOCALAPPDATA\DreamServer
# Get latest
git pull
# Update containers
docker compose pull
# Restart
docker compose up -d
```

---

## Need Help?

- Full walkthrough: [WINDOWS-INSTALL-WALKTHROUGH.md](WINDOWS-INSTALL-WALKTHROUGH.md)
- GPU issues: [WSL2-GPU-TROUBLESHOOTING.md](WSL2-GPU-TROUBLESHOOTING.md)
- Docker tuning: [DOCKER-DESKTOP-OPTIMIZATION.md](DOCKER-DESKTOP-OPTIMIZATION.md)
- General FAQ: [FAQ.md](../FAQ.md)

---

*Last updated: 2026-04-16*
