# ODS Quick Start

One command to a running local AI stack. The installer detects your hardware,
chooses a model, writes the config, starts the services, and leaves you with a
chat UI plus the `ods` management command.

This quickstart covers Linux, macOS, and Windows. For deeper platform notes,
see [MACOS-QUICKSTART.md](docs/MACOS-QUICKSTART.md),
[WINDOWS-QUICKSTART.md](docs/WINDOWS-QUICKSTART.md), and
[SUPPORT-MATRIX.md](docs/SUPPORT-MATRIX.md).

## Prerequisites

**Linux:**

- Docker with Compose v2+
- `curl` and `git`
- NVIDIA Container Toolkit for NVIDIA GPUs, ROCm devices for AMD Strix Halo, or
  Intel compute runtime for Arc
- 40 GB+ free disk space for models and container images

**macOS:**

- Apple Silicon Mac
- Docker Desktop running
- 16 GB+ unified memory recommended
- 20 GB+ free disk space

**Windows:**

- Windows 10/11
- Docker Desktop with WSL2 backend enabled and running
- NVIDIA GPU or AMD Strix Halo recommended
- A normal user PowerShell session. Do not run the installer as Administrator
  unless you deliberately want admin-owned files under your user profile.

## Install

### Linux/macOS One-Liner

```bash
curl -fsSL https://install.osmantic.com/ods.sh | bash
```

The hosted endpoint proxies the current bootstrap from repository `main`.
Reviewed merges reach it automatically after edge-cache refresh. `ODS_REF` selects a compatible repository checkout. See
[Installer Trust](docs/INSTALLER_TRUST.md) to inspect the script or install a
stable release or audited commit manually.

Do not run this command from Windows PowerShell; use the Windows installer below.

### Manual Clone

```bash
git clone https://github.com/Osmantic/ODS.git
cd ODS
./install.sh
```

### Windows

```powershell
$ProgressPreference = "SilentlyContinue"
$odsSrc = Join-Path $env:TEMP ("ods-install-" + [guid]::NewGuid().ToString("N"))
$odsZip = Join-Path $odsSrc "ods-main.zip"
New-Item -ItemType Directory -Path $odsSrc | Out-Null
Invoke-WebRequest "https://github.com/Osmantic/ODS/archive/refs/heads/main.zip" -OutFile $odsZip
Expand-Archive -LiteralPath $odsZip -DestinationPath $odsSrc -Force
cd (Get-ChildItem -LiteralPath $odsSrc -Directory | Select-Object -First 1).FullName
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install.ps1
```

Useful install flags:

| Linux/macOS | Windows | Purpose |
|-------------|---------|---------|
| `--all` | `-All` | Enable the recommended full stack |
| `--voice` | `-Voice` | Enable Whisper STT and Kokoro TTS |
| `--workflows` | `-Workflows` | Enable n8n workflows |
| `--rag` | `-Rag` | Enable Qdrant and embeddings |
| `--pixel` | — | Require Pixel on a qualified Linux/systemd host (separate written license required) |
| `--no-pixel` | — | Disable Pixel and use the configured fallback |
| `--no-hermes` | `-NoHermes` | Disable the Hermes fallback agent |
| `--no-bootstrap` | `-NoBootstrap` | Wait for the full model instead of fast-start |
| `--tier 3` | `-Tier 3` | Force a hardware/model tier |

## Uninstall

Linux/macOS:

```bash
cd ~/ods
./ods-uninstall.sh --force
```

Windows:

```powershell
$installDir = "$env:USERPROFILE\ods"
cd $installDir
.\ods.ps1 uninstall --force
```

Use `--keep-data` or `--keep-models` to preserve local state. If the Windows
runtime folder is partial and `.\ods.ps1` is missing, run
`.\ods\installers\windows\ods.ps1 uninstall --force` from a source checkout.

## What Happens First

Bootstrap mode is enabled by default when your selected full model is large.
ODS downloads a small model first so you can start chatting quickly,
then downloads and hot-swaps the full model in the background.

Pixel is preferred on qualified Ubuntu 24.04/Debian 12 PID1-systemd hosts only
after separate written license authorization. Everywhere else, ODS uses the
Hermes fallback. See [docs/PIXEL.md](docs/PIXEL.md). Hermes-enabled installs
keep the bootstrap model at a 64K context floor, then promote the full local
model target to 128K after the background swap.

Check progress:

```bash
ods status
tail -f ~/ods/logs/model-upgrade.log
```

On Windows:

```powershell
cd $env:USERPROFILE\ods
.\ods.ps1 status
Get-Content .\logs\model-upgrade.log -Wait
```

## Open The UI

- Chat UI: http://localhost:3000
- Dashboard: http://localhost:3001
- OpenCode IDE, when enabled: http://localhost:3003

Loopback-only installs open the Chat UI directly without an account. A
network-bound or ODS proxy install keeps authentication enabled and prompts the
first user to create the admin account.

## Validate The Install

```bash
ods status
ods chat "Say exactly: ODS is ready."
ods doctor
```

On Windows:

```powershell
cd $env:USERPROFILE\ods
.\ods.ps1 status
.\ods.ps1 logs llm
.\ods.ps1 report
```

For a lower-level source-tree check on Linux/macOS:

```bash
cd ~/ods
./ods-preflight.sh
./scripts/ods-test.sh
```

## Test The Local API

Use the port written to `.env`. Linux Docker installs commonly expose
llama-server on `OLLAMA_PORT=11434`; macOS native Metal installs commonly use
`8080`.

```bash
cd ~/ods
LLM_PORT="$(grep -E '^OLLAMA_PORT=' .env | tail -n1 | cut -d= -f2 | tr -d '\"')"
LLM_MODEL="$(grep -E '^LLM_MODEL=' .env | tail -n1 | cut -d= -f2 | tr -d '\"')"

curl "http://localhost:${LLM_PORT:-11434}/health"

curl "http://localhost:${LLM_PORT:-11434}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"${LLM_MODEL:-qwen3.5-2b}\",
    \"messages\": [{\"role\": \"user\", \"content\": \"Hello from ODS\"}]
  }"
```

## Hardware Tiers

The installer auto-detects your GPU, memory, and platform, then picks an
appropriate model and context window. The canonical tier tables live in:

- [README.md](README.md#hardware-tiers)
- [HARDWARE-GUIDE.md](docs/HARDWARE-GUIDE.md)
- [SUPPORT-MATRIX.md](docs/SUPPORT-MATRIX.md)

Override detection only when you know the target tier:

```bash
./install.sh --tier 3
```

```powershell
.\install.ps1 -Tier 3
```

## Common Issues

### Out Of Memory

Choose a lower tier and reinstall, or lower `CTX_SIZE` in `.env`. If Hermes is
enabled, keep context at least `65536` or disable Hermes during install.

### WebUI Shows No Models

The inference engine may still be loading or the full model may still be
downloading. Check:

```bash
ods status
docker compose logs llama-server
```

### Port Conflicts

Edit `.env` and restart:

```bash
WEBUI_PORT=3001
OLLAMA_PORT=11435
```

If Ollama Desktop is already using `11434`, stop Ollama Desktop or choose a
different `OLLAMA_PORT`.

### Docker Desktop Not Running

Start Docker Desktop, wait until it reports ready, then rerun the installer.

## Manage The Stack

Linux/macOS:

```bash
ods status
ods start
ods stop
ods restart
ods logs llm
ods update
./ods-uninstall.sh --force
```

Windows:

```powershell
cd $env:USERPROFILE\ods
.\ods.ps1 status
.\ods.ps1 start
.\ods.ps1 stop
.\ods.ps1 restart
.\ods.ps1 logs llm
.\ods.ps1 update
.\ods.ps1 uninstall --force
```

## Next Steps

- Open the Dashboard at http://localhost:3001 to watch service health.
- Use Hermes for local agent workflows, or disable it if you only want chat.
- Enable n8n workflows when you want automations.
- Enable RAG when you want local document/vector search.
- Read [MODEL-MANAGEMENT.md](docs/MODEL-MANAGEMENT.md) before swapping GGUFs.
