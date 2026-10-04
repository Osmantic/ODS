# ODS FAQ

Frequently asked questions about installing, running, and troubleshooting ODS.

> **Also see:** [`docs/FAQ.md`](docs/FAQ.md) for hardware sizing and how ODS
> compares with other local AI tools.

---

## General Questions

### What is ODS?
ODS installs a local AI stack on your own hardware:

- a local model server (llama.cpp's `llama-server`; Lemonade on Linux AMD;
  native Metal on macOS) running a model the installer picks from its catalog
  for your hardware;
- the ODS Dashboard (http://localhost:3001), with the Portal chat agent on
  qualified Linux hosts and the Models page for downloading and switching
  models;
- Open WebUI (http://localhost:3000) as the chat UI on hosts without Portal.

The default **Core Only** install is deliberately small. **Full Stack** (or
`ods enable <service>` later) adds voice (Whisper speech-to-text through
Speaches, Kokoro text-to-speech), n8n workflows, RAG (Qdrant and embeddings),
Privacy Shield, LiteLLM, Hermes, ComfyUI and more.

### What are the minimum requirements?
The installer checks free disk space (a blocker) and recommends RAM (a warning)
for your hardware tier, and it needs room for the chosen model plus 15 GB:

| Hardware (GPU memory) | Free disk | Recommended RAM |
|-----------------------|-----------|-----------------|
| NVIDIA GPU under 4 GB (tier 0) | 15 GB | 4 GB |
| CPU only, or GPU 4-11 GB (tier 1) | 30 GB | 16 GB |
| 12-19 GB (tier 2) | 50 GB | 32 GB |
| 20-39 GB (tier 3) | 80 GB | 48 GB |
| 40-89 GB (tier 4) | 150 GB | 64 GB |
| AMD Strix Halo, under 90 GB / 90 GB or more | 80 / 120 GB | 64 / 96 GB |

Linux (Ubuntu, Debian, Fedora/RHEL, Arch and openSUSE families), Apple Silicon
Macs with Docker Desktop, and Windows with WSL2 and Docker Desktop are
supported; on most Linux distributions the installer installs Docker for you. See the
[Support Matrix](docs/SUPPORT-MATRIX.md) for what is validated on each
platform. The Portal agent needs Ubuntu 24.04/26.04 or Debian 12 with systemd;
other Linux hosts use Hermes.

### Do I need an internet connection?
**Initial setup:** Yes, to download models and container images.

**After setup:** Inference, chat and your files work offline. A few optional
features (web search, update checks, cloud providers) use the network; see
the next answer.

### Is my data private?
Inference, chat history, voice processing and your documents stay on your
hardware, and ODS does not collect telemetry. Bundled services run with their
own usage telemetry and update checks turned off. By default ODS reaches the
internet only to:
- download models and container images, at install time and when you add models;
- check GitHub for new ODS releases (set `DISABLE_UPDATE_CHECK=true` in `.env` to stop it);
- run web searches the Portal agent makes for you, through its search provider.
  Fresh Pixel installs use OpenClaw's keyless Parallel search; set
  `PIXEL_WEB_SEARCH_PROVIDER=searxng` in `.env` before installing (or re-run the
  installer) to use the bundled local SearXNG instead.

Optional features such as cloud mode, remote model providers, the OpenCode web
UI's search, and n8n templates contact their own services when you turn them on.
The optional Privacy Shield redacts PII from requests you route through it.

### How much does it cost?
ODS is free and open source. Original ODS code is Apache-2.0; some bundled
components have their own licenses (see [Licensing](LICENSING.md)). You pay
only for your hardware and its electricity.

---

## Installation

### The installer fails with "Docker not found"
**Linux:** the installer installs Docker itself on supported distributions. If
it could not, install Docker Engine for your distribution
(https://docs.docker.com/engine/install/), add yourself to the `docker` group
(`sudo usermod -aG docker $USER`), log out and back in, and re-run the installer.

**Windows and macOS:** install Docker Desktop, start it once, then re-run the
installer. On Windows, Docker Desktop must use the WSL2 backend.

### "Permission denied" when running install.sh
Run it with Bash from the `ods` folder: `bash install.sh`. Do not run the
installer as root or with `sudo`; it asks for `sudo` itself when it needs it.

### The installer seems stuck while downloading a model
Large models take a while. Downloads resume if interrupted, and the full model
downloads in the background after the bootstrap model is running (see the next
question), so you can use ODS meanwhile. Progress is in
`~/ods/logs/model-upgrade.log`.

### Bootstrap mode started but I want the full model now
ODS starts with a small Qwen3.5 2B model (about 1.3 GB) so you can chat right
away. It downloads your full model in the background and switches to it
automatically once the download is verified; the Dashboard shows the progress.
Chat pauses briefly while the model server restarts on the new model. If the
download failed or stalled, `ods restart` retries it.

### How do I skip bootstrap mode?
```bash
./install.sh --no-bootstrap
```

The installer downloads the full model first, so you wait longer before first
use. On Windows, use `-NoBootstrap`.

### How do I switch to a different model?
Use Dashboard → Models: download a catalog model (or import a GGUF from Hugging
Face), choose a context size, and load it. If the new model fails its health
check, ODS restores the previous one automatically.

On Linux and WSL, `ods model current` shows the active model, and
`ods model swap <tier>` switches to that tier's default model if it is already
downloaded. See [Model Management](docs/MODEL-MANAGEMENT.md).

### Can I use my own GGUF model?
Yes. Copy the single `.gguf` file into `~/ods/data/models/`, then load it from
Dashboard → Models. Editing `GGUF_FILE` and `LLM_MODEL` in `.env` by hand
bypasses the health check and the automatic rollback; if you do it anyway,
apply it with `ods restart llama-server`.

On Lemonade installs, load the model through ODS rather than only opening it
in the Lemonade app. The Lemonade app can load the file for direct testing, but
Open WebUI uses ODS's persisted LiteLLM route and may switch Lemonade back to
the configured model on the next chat.

### Which model will I get?
The installer measures your GPU memory (or system RAM) and picks a model and
context size from its catalog; the
[hardware table in the README](../README.md#hardware-auto-detection) shows the
current picks. Dashboard → Models lists every catalog model with a memory
estimate, so you can choose a different one.

### NVIDIA GPU not detected
**Check the driver:**
```bash
nvidia-smi
```

ODS needs NVIDIA driver **570 or newer** (575 or newer for GPU-accelerated
Whisper; older drivers run Whisper on the CPU). On Ubuntu the installer offers
to install driver 570 and asks you to reboot. Blackwell GPUs need the open
kernel modules (`sudo apt install nvidia-open`). The installer also sets up the
NVIDIA Container Toolkit for Docker.

**On WSL2:** update the driver in Windows only. Never install NVIDIA drivers
inside Ubuntu.

### "CUDA out of memory" errors
The model and its context don't fit in GPU memory. In Dashboard → Models,
choose a shorter context or a smaller model; each option shows a memory
estimate. Advanced: set `LLAMA_ARG_CACHE_TYPE_K=q8_0` and
`LLAMA_ARG_CACHE_TYPE_V=q8_0` in `.env`, then run `ods restart llama-server`.
To run on the CPU only (slow), reinstall with `GPU_BACKEND=cpu ./install.sh`.

### Windows: WSL2 installation fails
Run `.\install.ps1` from a normal (not elevated) PowerShell window; it guides
WSL2, Ubuntu and Docker Desktop setup. To enable WSL2 by hand:
```powershell
wsl --install -d Ubuntu-24.04
```

Restart Windows if asked, then run the installer again. See the
[Windows Quickstart](docs/WINDOWS-QUICKSTART.md).

### The web dashboard won't load
```bash
ods status
ods logs dashboard
ods logs dashboard-api
```

Give the services a minute after starting. The Dashboard is
http://localhost:3001 (its API listens on 3002); Open WebUI, if installed, is
http://localhost:3000. Restart with `ods restart`.

### How do I uninstall?
**Linux/macOS:**

```bash
cd ~/ods
./ods-uninstall.sh --force
```

**Windows:**

```powershell
$installDir = "$env:USERPROFILE\ods"
cd $installDir
.\ods.ps1 uninstall --force
```

These use ODS's saved compose stack when available, remove the matching containers and volumes, and then remove the install directory. Use `--keep-data` or `--keep-models` if you want to preserve local state.

On Windows, if the runtime folder is partial and `.\ods.ps1` is missing, run the cleanup from a source checkout:

```powershell
cd ODS
.\ods\installers\windows\ods.ps1 uninstall --force
```

On Linux and macOS, use `ods stop` to pause services. It keeps the stopped containers so the uninstaller can verify which Docker volumes belong to this installation. If you need to run Docker Compose manually on those platforms, ODS does not use a top-level `docker-compose.yml`; use the saved flags:

```bash
cd ~/ods
docker compose $(cat .compose-flags) stop
```

For a full removal, use `./ods-uninstall.sh --force`. If an older `ods stop` already removed the containers, the uninstaller may refuse to purge volumes it cannot prove belong to this installation. `--keep-data` preserves them; a full purge then needs individual ownership review.

---

## Usage

### How do I access the web interface?
Open http://localhost:3001 for the ODS Dashboard (Portal, Models, services and
settings). Open WebUI, if installed, is at http://localhost:3000. The installer
prints the exact addresses when it finishes.

### What's the default password?
There is no default password, and the installer prints only addresses. On the
ODS computer, http://localhost:3001 opens without signing in. From another
device you sign in with a dashboard password you choose.

To set or change it, open Your profile → Change dashboard password, or run
`ods dashboard-login` on the ODS computer for a one-time link. Generated
service secrets (for example n8n's `N8N_USER` and `N8N_PASS`) are in
`~/ods/.env`, which only your user can read.

### Can I access from other devices on my network?
Yes. Reinstall with `./install.sh --lan`, or set `BIND_ADDRESS=0.0.0.0` in
Dashboard → Settings → Advanced and run `ods restart`. The Dashboard is then at
`http://<your-ip>:3011` and always asks for sign-in; Open WebUI, if installed,
is at `http://<your-ip>:3000` with its sign-in turned on. For
`http://<device>.local` addresses, enable the ODS proxy
(`ods enable ods-proxy`). Read
[Quick LAN Access](SECURITY.md#quick-lan-access) first: it explains which
passwords to set before you expose anything.

### How do I create a workflow?
Workflows run in n8n, which is optional: choose Full Stack or `--workflows`
when installing, or run `ods enable n8n` and then `ods start n8n`. Open
http://localhost:5678 and sign in with `N8N_USER` and `N8N_PASS` from
`~/ods/.env` (change that password in n8n). Create a workflow, add a trigger
and actions, save it, and switch it to Active.

### What's n8n?
n8n is the optional workflow engine bundled with ODS. It provides a visual
editor, hundreds of integrations, webhook triggers, scheduled jobs and AI
agent nodes that can use your local model.

### Can I send requests to cloud APIs?
Yes, optionally. Add `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` to `.env`, run
`ods mode cloud` (or `ods mode hybrid`), then `ods restart`; LiteLLM then routes
requests to your provider.

To strip personal data from requests first, enable Privacy Shield
(`ods enable privacy-shield`, then `ods start privacy-shield`) and send
OpenAI-style requests to `http://localhost:8085/chat/completions` with
`Authorization: Bearer <SHIELD_API_KEY from .env>`. Only requests sent to port
8085 are scrubbed.

### How do I use voice features?
Voice is optional: choose Full Stack or `--voice` when installing, or run
`ods enable whisper` and `ods enable tts`. Talk to ODS at
http://localhost:3001/talk (ODS Talk), or use Open WebUI's voice mode if it is
installed.

### Which speech-to-text model is used?
`deepdml/faster-whisper-large-v3-turbo-ct2` on NVIDIA GPUs with driver 575 or
newer, and `Systran/faster-whisper-base` elsewhere. Change it with
`AUDIO_STT_MODEL` in `.env`, then run `ods restart` and `ods stt download`.
`ods stt status` shows what is installed.

### Which text-to-speech voice is used?
Kokoro's `af_heart` voice by default. Set `AUDIO_TTS_VOICE` in `.env` (for
example `af_bella`, `am_adam` or `am_michael`) and run `ods restart` to change it.

---

## Troubleshooting

### Where are the logs?
```bash
ods logs <service>          # e.g. ods logs llm, ods logs dashboard-api
ods logs <service> 500      # more lines
```

`ods list` shows the service names. ODS has no top-level `docker-compose.yml`,
so plain `docker compose logs` fails in `~/ods`; for raw Compose access use
`docker compose $(cat .compose-flags) logs -f`. On macOS, use
`~/ods/ods-macos.sh logs <service>`.

### How do I restart everything?
```bash
ods restart
```

Or restart one service:
```bash
ods restart llama-server
```

### "Connection refused" to the API
1. Check the services: `ods status`
2. Read the API log: `ods logs dashboard-api`
3. Check that nothing else uses port 3002: `sudo lsof -i :3002`
4. Restart it: `ods restart dashboard-api`

### Models won't load
**Check disk space:**
```bash
df -h
```

**Check the model files:**
```bash
ls -la ~/ods/data/models/
```

If a download is missing or incomplete, download the model again from
Dashboard → Models, or run `ods restart` to resume a stalled bootstrap upgrade.

### Voice quality is poor
**Speech-to-text:** check the microphone level, reduce background noise, or
choose a larger `AUDIO_STT_MODEL`.

**Text-to-speech:** check the speakers or headphones, or try another
`AUDIO_TTS_VOICE`.

### Slow response times
**Check GPU use:**
```bash
nvidia-smi
ods gpu status
```

If the GPU is fully busy, use a smaller model or a shorter context in
Dashboard → Models. If no `llama-server` process appears in `nvidia-smi`, the
model is running on the CPU; see "NVIDIA GPU not detected" above.

### Workflows not triggering
Check that the webhook URL is reachable from the service that calls it, read
`ods logs n8n`, and make sure the workflow is switched to Active in the editor.

### Docker volumes taking too much space
Use the ODS uninstaller with `--keep-data` if you want to remove the
application while keeping its volumes. For a full ODS removal, run
`./ods-uninstall.sh --force`; it checks volume ownership before deleting
data. If it cannot prove ownership, it leaves the volumes for individual
review. Avoid Docker-wide volume cleanup commands on a host with other apps.

---

## Advanced

### How do I add a custom model?
See [How do I switch to a different model?](#how-do-i-switch-to-a-different-model)
and [Can I use my own GGUF model?](#can-i-use-my-own-gguf-model) above.

### How do I enable HTTPS?
ODS has no built-in HTTPS; the optional ODS proxy and Tailscale add-on are
HTTP only. For TLS, put your own reverse proxy (Caddy, nginx, Traefik) in front
of ODS, or reach it over a private VPN such as Tailscale. See
[Exposing to Internet](SECURITY.md#exposing-to-internet-not-recommended). The Dashboard asks
for sign-in when it is reached through a proxy.

### Can I run on multiple GPUs?
Yes. The installer detects multiple GPUs and assigns them automatically. Use
`ods gpu status` and `ods gpu assignment` to inspect the assignment and
`ods gpu reassign` (`--auto` or `--manual`) to change it. Don't edit the compose
files.

### How do I back up my data?
```bash
ods backup                # chats, workflows, vectors and agent state
ods backup -t full -c     # also downloaded models, compressed
ods restore               # restore a backup
```

Backups go to `~/ods/.backups` (the newest five are kept). On Portal (Pixel)
installs, ordinary backups refuse to run because they can't capture Portal's
state; `ods backup -t config` still works.

### How do I update ODS?
`ods update` refreshes the container images and recreates the containers; it
does not change ODS code. To get new ODS code, including security fixes:

```bash
ods backup
git clone --depth 1 https://github.com/Osmantic/ODS.git ~/ods-update
cd ~/ods-update/ods && ./install.sh
```

This updates `~/ods` in place and keeps `.env` and `data/`. Don't use `--force`
or uninstall and reinstall to update. `~/ods` is not a git checkout, so
`git pull` there does nothing. See
[Updating an existing installation](SECURITY.md#updating-an-existing-installation).

### Where is the data stored?
In folders under `~/ods/data/` rather than Docker volumes: for example n8n's
workflows and credentials in `~/ods/data/n8n/` and Open WebUI's in
`~/ods/data/open-webui/`. Stop a service before copying its files, or use
`ods backup`.

### Can I use OpenAI or Anthropic models?
Yes. See [Can I send requests to cloud APIs?](#can-i-send-requests-to-cloud-apis)
above.

### How do I monitor performance?
Dashboard → GPU Monitor shows per-GPU use, memory and temperature, and
Dashboard → Usage shows token and cost history. `ods gpu status` shows the GPU
view in a terminal.

### What ports are used?
All ports bind to 127.0.0.1 unless you enable LAN access.

| Port | Service |
|------|---------|
| 3001 | ODS Dashboard |
| 3011 | Dashboard from other devices (sign-in required; LAN mode only) |
| 3002 | Dashboard API |
| 3000 | Open WebUI (when installed) |
| 11434 | Model server API (llama-server; 8080 for native llama-server on macOS) |
| 4000 | LiteLLM (when enabled) |
| 8085 | Privacy Shield (when enabled) |
| 5678 | n8n (when enabled) |
| 9000 | Whisper speech-to-text (when enabled) |
| 8880 | Kokoro text-to-speech (when enabled) |
| 6333 | Qdrant vector database (when enabled) |
| 8090 | Embeddings (when enabled) |
| 80 | ODS proxy (when enabled) |

### How do I change a port?
Set the service's `*_PORT` variable in `~/ods/.env` (for example
`WEBUI_PORT=3100`) and run `ods restart`.

---

## Getting Help

### Documentation
- Main README: [`README.md`](../README.md)
- Product overview: [`ods/README.md`](README.md)
- Security: [`SECURITY.md`](SECURITY.md)
- Installer architecture: [`docs/INSTALLER-ARCHITECTURE.md`](docs/INSTALLER-ARCHITECTURE.md)

### Community
- Questions: [GitHub Discussions](https://github.com/Osmantic/ODS/discussions)
- Bugs: [GitHub Issues](https://github.com/Osmantic/ODS/issues)
- Security problems: report them privately through
  [Security → Report a vulnerability](https://github.com/Osmantic/ODS/security/advisories/new)
  or security@osmantic.com, never in a public issue.

### Debug info for bug reports
Create a redacted support bundle and attach it to your issue after reviewing it:

```bash
cd ~/ods
scripts/ods-support-bundle.sh
```

See [Support Bundle](docs/SUPPORT-BUNDLE.md) for what it contains.
