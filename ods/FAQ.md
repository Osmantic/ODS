# ODS FAQ

Frequently asked questions about installing, running, and troubleshooting ODS.

> **Also see:** [`docs/FAQ.md`](docs/FAQ.md) for hardware requirements, pricing, and comparisons with alternatives.

---

## General Questions

### What is ODS?
ODS is a turnkey local AI stack that runs entirely on your own hardware. It includes:
- LLM inference via llama-server (qwen2.5-32b-instruct)
- Web dashboard for chat and model management
- Voice capabilities (STT via Whisper, TTS via Kokoro)
- Workflow automation via n8n
- API gateway with privacy shield for external services

### What are the minimum requirements?
**Minimum (bootstrap mode):**
- Any modern CPU
- 8GB RAM
- 10GB disk space
- Docker + Docker Compose

**Recommended (full experience):**
- NVIDIA GPU with 24GB+ VRAM (RTX 3090/4090)
- 32GB+ system RAM
- 100GB+ SSD storage
- Ubuntu 22.04/24.04 or WSL2 on Windows

### Do I need an internet connection?
**Initial setup:** Yes, to download models and Docker images.

**After setup:** No. ODS is designed for offline/air-gapped operation. All models run locally.

### Is my data private?
Yes. Everything runs on your hardware:
- Conversations never leave your machine
- Voice processing is local
- API calls to external services go through the Privacy Shield (PII redaction)
- No telemetry or analytics

### How much does it cost?
ODS is **free and open source** (Apache 2.0 license). You only pay for:
- Your hardware (one-time cost)
- Electricity to run it

---

## Installation

### The installer fails with "Docker not found"
**Linux:**
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker
```

**Windows:**
Install Docker Desktop from https://docs.docker.com/desktop/install/windows-install/
Enable WSL2 backend in Docker Desktop settings.

### "Permission denied" when running install.sh
Make the script executable:
```bash
chmod +x install.sh
./install.sh
```

### The installer hangs during model download
This is normal for large models (20GB+). The installer shows progress bars with:
- Download speed
- Time elapsed
- ETA

**To speed up:** Use a wired connection. WiFi can be unstable for large downloads.

**To restart:** The installer resumes partial downloads automatically.

### Bootstrap mode started but I want the full model now
```bash
./scripts/upgrade-model.sh
```

This hot-swaps from the 1.5B bootstrap model to your full model without downtime.

### How do I skip bootstrap mode?
```bash
./install.sh --no-bootstrap
```

This downloads the full model first. You'll wait longer before first use.

### How do I switch to a different model?
Use the `ods` CLI:
```bash
ods model current              # See what's running
ods model list                 # Show available tiers and models
ods model swap T3              # Switch to Tier 3 (e.g., Qwen3 30B-A3B)
```

The model file must already be downloaded. If it isn't, pre-fetch it first:
```bash
./scripts/pre-download.sh --tier 3
```

### Can I use my own GGUF model?
Yes. Drop the single `.gguf` file into `data/models/`, then open Dashboard ->
Models and load the local entry. For headless maintenance or older installs,
update `.env`:
```bash
GGUF_FILE=my-model.gguf
LLM_MODEL=my-model
```
Restart the inference server:
```bash
docker compose restart llama-server
```
The model will load in ~30-120 seconds depending on size. If it fails, ODS automatically rolls back to the previous model.

On Lemonade installs, load the model through ODS rather than only
opening it in the Lemonade app. The Lemonade app can load the file for direct
testing, but Open WebUI uses ODS's persisted LiteLLM route and may
switch Lemonade back to the configured/default model on the next chat.

### What models are available?
The installer auto-selects based on your GPU, but you can switch between any tier:

| Tier | Model | Min VRAM |
|------|-------|----------|
| T1 | Qwen3.5 9B | 8 GB |
| T2 | Qwen3.5 9B | 12 GB |
| T3 | Qwen3 30B-A3B | 20 GB |
| T4 | Qwen3 30B-A3B (MoE) | 40 GB |
| SH_COMPACT | Qwen3 30B-A3B (MoE) | 64 GB unified |
| SH_LARGE | Qwen3 Coder Next 80B (MoE) | 90 GB unified |

Run `ods model list` for the full list on your system.

### NVIDIA GPU not detected
**Check driver:**
```bash
nvidia-smi
```

**If missing:** Install NVIDIA drivers:
```bash
# Ubuntu
sudo apt update
sudo apt install nvidia-driver-550
sudo reboot
```

**Check Docker runtime:**
```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### "CUDA out of memory" errors
Your GPU doesn't have enough VRAM. Options:
1. Use a smaller model (qwen2.5-7b-instruct instead of 32b)
2. All models use GGUF Q4_K_M quantization by default
3. Reduce `CTX_SIZE` in `.env` (try 4096)
4. Run on CPU only (slower but works)

### Windows: WSL2 installation fails
Enable WSL2 manually:
```powershell
wsl --install -d Ubuntu-24.04
wsl --set-default-version 2
```

Then restart the installer.

### The web dashboard won't load
**Check if services are running:**
```bash
docker compose ps
```

**Check logs:**
```bash
docker compose logs dashboard-api
docker compose logs llama-server
```

**Common fixes:**
- Wait 30 seconds for services to start
- Check http://localhost:3001 (direct API) vs http://localhost:3000 (UI)
- Restart: `docker compose restart`

### How do I uninstall?
```bash
cd ~/ods
./ods-uninstall.sh --force
```

This uses ODS's saved `.compose-flags` stack, removes the matching containers and volumes, and then removes the install directory. Use `--keep-data` or `--keep-models` if you want to preserve local state.

If you need to run Docker Compose manually, do not use bare `docker compose down`: ODS does not use a top-level `docker-compose.yml`. Use the saved flags instead:

```bash
cd ~/ods
docker compose $(cat .compose-flags) down -v --remove-orphans
```

---

## Usage

### How do I access the web interface?
```
http://localhost:3000
```

On first run, the installer displays a QR code. Scan it with your phone for instant mobile access.

### What's the default password?
The installer generates secure random passwords and displays them at the end. Look for:
```
✓ Dashboard URL: http://localhost:3000
✓ API Key: dsf8a9s7df8a9s7df...
```

Passwords are also saved to `.env` in the ods directory.

### How do I change the password?
Edit `.env`:
```bash
nano .env
# Change: DASHBOARD_PASSWORD=your-new-password
docker compose restart dashboard
```

### Can I access from other devices on my network?
Yes! Use your machine's local IP:
```
http://192.168.1.xxx:3000
```

The installer shows this URL with a QR code at the end.

### How do I create a workflow?
1. Open http://localhost:3000/workflows
2. Click "New Workflow"
3. Select a template or start from scratch
4. Connect nodes (triggers → actions)
5. Save and activate

### What's n8n?
n8n is the workflow engine built into ODS. It provides:
- Visual workflow editor
- 400+ integrations (GitHub, Slack, email, etc.)
- Webhook triggers
- Scheduled jobs
- AI agent capabilities

### Can I connect to external APIs?
Yes, through the **Privacy Shield**:
1. Configure the shield service (runs on port 8085)
2. Route API calls through `http://localhost:8085/proxy/{service}`
3. PII is automatically redacted before leaving your network

### How do I use voice features?
**Prerequisites:** Microphone and speakers/headphones

1. Open the Voice page in the dashboard
2. Click "Start Conversation"
3. Allow microphone access
4. Speak naturally — the system handles STT → LLM → TTS automatically

### Which STT model should I use?
| Model | Speed | Accuracy | Use Case |
|-------|-------|----------|----------|
| tiny | ~400ms | Good | Quick commands |
| base | ~700ms | Better | General use |
| small | ~2s | Best | Accuracy critical |
| large-v3 | ~8s | Excellent | Offline transcription |

Default is `base`. Change in Settings → Voice.

### Which TTS voice is best?
Kokoro provides high-quality voices. Options:
- `af_bella` — Natural female (default)
- `af_nicole` — Professional female
- `am_adam` — Natural male
- `am_michael` — Professional male

Preview voices in Settings → Voice → Test.

---

## Troubleshooting

### Where are the logs?
**All services:**
```bash
docker compose logs -f
```

**Specific service:**
```bash
docker compose logs -f llama-server
docker compose logs -f dashboard-api
docker compose logs -f whisper
docker compose logs -f tts
```

**To file:**
```bash
docker compose logs > ods.log 2>&1
```

### How do I restart everything?
```bash
ods restart
```

Or restart specific services:
```bash
ods restart llama-server
```

### "Connection refused" to API
1. Check if the API container is running: `docker compose ps dashboard-api`
2. Check logs: `docker compose logs dashboard-api`
3. Verify port 3001 is not in use: `sudo lsof -i :3001`
4. Restart: `docker compose restart dashboard-api`

### Models won't load
**Check disk space:**
```bash
df -h
```

Models need ~20GB per model. Free up space if needed.

**Check model download:**
```bash
ls -la data/models/
```

If empty or incomplete, re-download:
```bash
./scripts/pre-download.sh
```

### Voice quality is poor
**STT issues:**
- Check microphone input level
- Reduce background noise
- Try a different STT model (base → small)

**TTS issues:**
- Check speaker/headphone connection
- Adjust TTS speed in Settings
- Try different voices

### Slow response times
**Check GPU utilization:**
```bash
nvidia-smi
```

If GPU is at 100%, you're GPU-bound. Solutions:
- Reduce concurrent requests
- Use a smaller model
- Enable KV cache quantization

**Check if using CPU:**
If `nvidia-smi` shows no process, the model is running on CPU (very slow). Fix GPU detection issues above.

### "Rate limit exceeded" errors
The Privacy Shield has rate limiting to prevent abuse. Default: 100 requests/minute.

To increase:
1. Edit `.env`
2. Change `RATE_LIMIT_REQUESTS_PER_MINUTE=100`
3. Restart: `docker compose restart privacy-shield`

### Workflows not triggering
**Check webhook URL:**
Must be accessible from the triggering service.

**Check n8n logs:**
```bash
docker compose logs n8n
```

**Verify workflow is active:**
In the workflow editor, toggle must be ON (green).

### Docker volumes taking too much space
Clean up unused volumes:
```bash
docker volume prune
```

Or remove everything (destructive):
```bash
cd ~/ods
docker compose $(cat .compose-flags) down -v --remove-orphans
```

---

## Advanced

### How do I add a custom model?
See [How do I switch to a different model?](#how-do-i-switch-to-a-different-model) and [Can I use my own GGUF model?](#can-i-use-my-own-gguf-model) above.

**Short version:** Drop your `.gguf` file into `data/models/`, set `GGUF_FILE` and `LLM_MODEL` in `.env`, run `docker compose restart llama-server`. Rollback is automatic on failure.

### How do I enable HTTPS?
For production deployments, use a reverse proxy (nginx, Caddy, Traefik) in front of ODS:

```bash
# Example with Caddy (auto-HTTPS with Let's Encrypt)
caddy reverse-proxy --from your-domain.com --to localhost:3000
```

For local development, browsers accept self-signed certs at `https://localhost`.

### Can I run on multiple GPUs?
Yes! Edit `docker-compose.nvidia.yml` to expose multiple GPUs:
```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: 2  # Number of GPUs
          capabilities: [gpu]
```

### How do I backup my data?
**Configs and data:**
```bash
tar -czf ods-backup.tar.gz .env data/
```

**Models (large):**
```bash
rsync -av models/ /backup/location/models/
```

### How do I update ODS?
```bash
./ods-update.sh
```

Or manually:
```bash
git pull
docker compose pull
docker compose up -d
```

This pulls latest code, updates Docker images, and migrates data.

### Where is the database?
SQLite databases are in Docker volumes:
- `ods_n8n-data` — Workflows and credentials
- `ods_agent-monitor` — Metrics and logs

Access via:
```bash
docker compose exec n8n sqlite3 /home/node/.n8n/database.sqlite
```

### Can I use OpenAI/Anthropic APIs?
Yes, through the Privacy Shield. Configure in Settings → API Keys.

Your requests go: You → Shield (PII redaction) → OpenAI → Shield (deanonymization) → You

### How do I monitor performance?
Open the Dashboard → Metrics page for:
- GPU utilization and temperature
- Request latency (P50, P95, P99)
- Token throughput
- Active connections

Or use the API:
```bash
curl http://localhost:3001/api/metrics
```

### What ports are used?
| Port | Service |
|------|---------|
| 3000 | Open WebUI (chat interface) |
| 3001 | Dashboard |
| 3002 | Dashboard API |
| 8080 | llama-server API |
| 8085 | Privacy Shield |
| 5678 | n8n workflow editor |
| 7880 | LiveKit voice server |
| 9000 | Whisper STT |
| 8880 | Kokoro TTS |
| 6333 | Qdrant vector DB |
| 8090 | Embeddings service |

### How do I change the port?
Edit `.env`:
```bash
DASHBOARD_PORT=8080
```

Then restart: `docker compose up -d`

---

## Getting Help

### Documentation
- Main README: `ods/README.md`
- Installer Architecture: `docs/INSTALLER-ARCHITECTURE.md`
- Security: `SECURITY.md`

### Community
- GitHub Issues: https://github.com/Osmantic/ODS/issues
- Discord: #general channel

### Debug info for bug reports
Include this output:
```bash
# Collect system info
echo "=== Docker Compose ===" && docker compose version
echo "=== Services ===" && docker compose ps
echo "=== Recent Logs ===" && docker compose logs --tail=50
echo "=== GPU ===" && nvidia-smi 2>/dev/null || echo "No GPU"
```

Copy the output into your GitHub issue.

---

*Last updated: 2026-03-05*
