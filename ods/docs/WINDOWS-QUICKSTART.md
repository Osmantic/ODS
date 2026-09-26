# ODS Windows Quickstart

## Getting Started

Portal is ODS's core agent, built on Pixel / OpenClaw. On Windows, use its
supported Ubuntu/WSL2 installer. The native PowerShell stack is an alternative
with optional Hermes; it does not install Portal. Choose one installation path
and do not run both against the same Docker project or ports.

## Portal / OpenClaw (recommended)

1. Install Ubuntu 24.04 under WSL2 from PowerShell if it is not already installed:

   ```powershell
   wsl --install -d Ubuntu-24.04
   ```

   Complete any Windows restart requested by WSL, open Ubuntu, and create its
   ordinary Linux user. The Ubuntu password is used for `sudo` during setup.

2. Start Docker Desktop with its WSL2 backend and enable integration for that
   Ubuntu distribution in **Settings → Resources → WSL Integration**.

3. In the **Ubuntu terminal**, verify the prerequisites:

   ```bash
   ps -p 1 -o comm=
   docker info
   ```

   PID 1 must be `systemd`, and Docker must be reachable by the ordinary user.
   A missing prerequisite is a setup error; do not switch silently to Hermes.

4. Install from the public ODS repository in the Linux filesystem:

   ```bash
   sudo apt-get update
   sudo apt-get install -y git
   git clone https://github.com/Osmantic/ODS.git ~/ODS-source
   cd ~/ODS-source
   bash install.sh --pixel
   ```

   `--pixel` requires Portal to be installed successfully. The installer uses
   the bundled Pixel source; no private repository is needed. Wait for its
   health checks to finish, then open **http://localhost:3001** in Windows.

GPU support depends on the Linux/WSL inference backend, not just the presence of
a Windows GPU. A working Windows-hosted model can instead be reused through the
[external model route](PIXEL.md#linux-and-wsl2-eligibility); the endpoint must be
reachable from Ubuntu and Docker. Do not copy another PC's WSL IP address.

These steps describe a fresh installation. For an existing native installation,
stop its ODS containers and host services before migrating; preserve model files
you intend to reuse. The installer does not adopt an unrelated Pixel deployment.

## Alternative: native Windows stack

The native installer supports Windows 10 2004+ and Windows 11 (NVIDIA and AMD).
It detects the GPU, selects a model, starts Docker services, and creates a
Desktop shortcut. It does not provision the Portal host runtime.

**Prerequisites:** [Docker Desktop](https://www.docker.com/products/docker-desktop/) with WSL2 backend enabled. NVIDIA GPU or AMD Strix Halo recommended (CPU-only works with smaller models). 4GB+ RAM minimum, 16GB+ recommended.

Open a normal **PowerShell** session and run:

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

The installer will:
- Detect your GPU (NVIDIA or AMD) and pick the right model tier
- Download the AI model for your hardware (~1.5GB bootstrap, full model in background)
- Start all Docker services
- Run health checks and create a Desktop shortcut

### Source checkout vs runtime directory

The downloaded source folder is only the installer/source checkout. The Windows
runtime is created under `$env:USERPROFILE\ods` by default (or `$env:ODS_HOME`
if you set it before installing). That runtime directory contains `.env`,
generated secrets, model files, logs, data, and the compose state.

If your `C:` drive is tight, choose the runtime location explicitly. Running
the installer from `G:\ODS` does not automatically install the runtime
on `G:`. Pass any NTFS/ReFS path with enough space:

```powershell
$installDir = "D:\Apps\ods"
.\install.ps1 -InstallDir $installDir
```

Do not run raw `docker compose` commands from the cloned repository after
installing; Compose will not find the generated `.env` there and relative
volumes will point at the wrong data directory. Use `.\ods.ps1` from the
runtime directory, or `cd $installDir` before running manual Compose commands.

Do not run as Administrator for the normal install. The Windows preflight warns
about this because user-level paths such as `.opencode`, `.env`, and `data/`
can become admin-owned and awkward to manage afterward.

**First-run time:** 10-30 minutes depending on download speed. Bootstrap mode starts chatting in under 2 minutes while the full model downloads in background.

---

## Quick Commands

Manage ODS using `ods.ps1` from your runtime directory:

```powershell
$installDir = "$env:USERPROFILE\ods"
# If you installed with -InstallDir, use that same path instead:
# $installDir = "D:\Apps\ods"
cd $installDir

.\ods.ps1 status              # Health checks + GPU status
.\ods.ps1 start               # Start all services
.\ods.ps1 stop                # Stop all services
.\ods.ps1 restart             # Restart all services
.\ods.ps1 logs llama-server   # Tail logs (any service name)
.\ods.ps1 update              # Pull latest images and restart
.\ods.ps1 report              # Generate diagnostics bundle
.\ods.ps1 uninstall --force   # Remove ODS containers, volumes, and files
```

For development installs where you intentionally want the runtime files inside
your working tree, set `ODS_HOME` before running the installer:

```powershell
$env:ODS_HOME = "C:\path\to\ODS\ods"
.\install.ps1
```

Only use this in-place mode if you want `.env`, `data\`, logs, and downloaded
models to live inside that checkout.

---

## Open the UI

Visit **http://localhost:3000** — the chat interface is ready after the installer completes.

The normal loopback-only install opens directly without an account. A
network-bound or ODS proxy install keeps authentication enabled and prompts the
first user to create the admin account.

---

## Bootstrap Mode (Faster Start)

The installer automatically uses bootstrap mode when applicable — a small model
(~1.5 GB) downloads first so you can start chatting within 2 minutes, while the
full model downloads in the background. Hermes-enabled installs run that
bootstrap model at a 64K context floor, then keep the model selector's chosen
full-model context after the swap. Large-context tiers still use 128K when they
select it; constrained machines can stay lower. No extra flags needed.

---

## Installer Flags

| Flag | What It Does |
|------|--------------|
| `-Tier 2` | Force specific tier (1-4) |
| `-Voice` | Enable Whisper + TTS |
| `-Workflows` | Enable n8n automation |
| `-Rag` | Enable Qdrant vector DB |
| `-Recommended` | Enable LiteLLM + SearXNG + Token Spy support services |
| `-NoRecommended` | Disable LiteLLM + SearXNG + Token Spy support services |
| `-Hermes` | Enable Hermes Agent |
| `-NoHermes` | Disable Hermes Agent |
| `-NoBootstrap` | Wait for the full model before launching |
| `-OpenClaw` | Enable deprecated OpenClaw legacy agent framework |
| `-Comfyui` | Enable ComfyUI image generation |
| `-Langfuse` | Enable Langfuse LLM observability |
| `-All` | Full stack enabled, except deprecated OpenClaw unless `-OpenClaw` is also passed |
| `-Cloud` | Use cloud LLM provider instead of local |
| `-DryRun` | Simulate install without making changes |
| `-InstallDir <path>` | Install runtime files on a specific drive/path |

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

NVIDIA GPU access: Windows driver → WSL2 → Docker Container Toolkit → llama-server

AMD Strix Halo local inference runs through the Windows host accelerated path
selected by the installer; Docker services reach it through
`host.docker.internal`.

---

## Files & Locations

| What | Where |
|------|-------|
| Install directory | `$env:USERPROFILE\ods` by default; override with `-InstallDir` or `ODS_HOME` |
| Config | `$installDir\.env` |
| Models | `$installDir\data\models\` |
| Logs | `.\ods.ps1 logs <service>` or `docker compose logs` after `cd $installDir` |
| Data | Docker volumes (auto-managed) |

---

## Updating

```powershell
$installDir = "$env:USERPROFILE\ods"
# If you installed with -InstallDir, use that same path instead.
cd $installDir
.\ods.ps1 update
```

---

## Uninstalling

Start Docker Desktop first so ODS can remove its containers and volumes, then run:

```powershell
$installDir = "$env:USERPROFILE\ods"
# If you installed with -InstallDir, use that same path instead.
cd $installDir
.\ods.ps1 uninstall --force
```

To preserve local state:

```powershell
.\ods.ps1 uninstall --force --keep-data
.\ods.ps1 uninstall --force --keep-models
```

If the runtime folder is partial and `.\ods.ps1` is missing, run the cleanup command from a source checkout:

```powershell
cd ODS
.\ods\installers\windows\ods.ps1 uninstall --force
```

That fallback removes Docker resources labelled as the ODS compose project before removing the runtime directory.

---

## Need Help?

- Full walkthrough: [WINDOWS-INSTALL-WALKTHROUGH.md](WINDOWS-INSTALL-WALKTHROUGH.md)
- GPU issues: [WSL2-GPU-TROUBLESHOOTING.md](WSL2-GPU-TROUBLESHOOTING.md)
- Docker tuning: [DOCKER-DESKTOP-OPTIMIZATION.md](DOCKER-DESKTOP-OPTIMIZATION.md)
- General FAQ: [FAQ.md](../FAQ.md)

---

*Last updated: 2026-05-20*
