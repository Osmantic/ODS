# ODS V3 Pre-Release

> **Release channel:** the install commands on this page fetch development `main`, which is not signed. A signed-source path is staged in [Verified Install Preview](docs/VERIFIED_INSTALL_PREVIEW.md); it is not active until the first eligible immutable release is published, and historical `v3.0.0` is not eligible.

**Osmantic Deployment System**

**Public testing and refinement ahead of the official V3 launch.**
Try it, share feedback, and help us improve the experience. See the
[V3 Pre-Release notes](docs/RELEASE_NOTES_3.0.0.md) for current qualification status.

[![License: Apache 2.0 + Pixel ODS-only](https://img.shields.io/badge/License-Apache%202.0%20%2B%20Pixel%20ODS--only-blue.svg)](LICENSING.md)
[![Docker](https://img.shields.io/badge/Docker-Required-2496ED?logo=docker)](https://docs.docker.com/get-docker/)
[![NVIDIA](https://img.shields.io/badge/NVIDIA-GPU%20Accelerated-76B900?logo=nvidia)](https://developer.nvidia.com/cuda-toolkit)
[![AMD](https://img.shields.io/badge/AMD-Strix%20Halo%20ROCm-ED1C24?logo=amd)](https://rocm.docs.amd.com/)
[![n8n](https://img.shields.io/badge/n8n-Workflows-FF6D5A?logo=n8n)](https://n8n.io)

**Your turnkey local AI stack.** Buy hardware. Run installer. AI running.

Pixel source ships inside ODS with an ODS-only use and distribution grant;
other ODS code remains Apache-2.0. See [Licensing](LICENSING.md) for the
boundary and third-party notices.

---

## Platform Support

> | Platform | Status |
> |----------|--------|
> | **Linux** (NVIDIA + AMD Strix Halo) | **Supported** — install and run today; Intel Arc is experimental |
> | **macOS** (Apple Silicon) | **Supported** — install and run today |
> | **Windows** (NVIDIA + AMD) | **Supported** — install and run today |
>
> All three platforms have one-command installers. See [`docs/SUPPORT-MATRIX.md`](docs/SUPPORT-MATRIX.md) for each platform's support tier.

See [`docs/SUPPORT-MATRIX.md`](docs/SUPPORT-MATRIX.md) for current support tiers and platform status.
Launch-claim guardrails: [`docs/PLATFORM-TRUTH-TABLE.md`](docs/PLATFORM-TRUTH-TABLE.md)
Known-good version baselines: [`docs/KNOWN-GOOD-VERSIONS.md`](docs/KNOWN-GOOD-VERSIONS.md)

## Installer Evidence

- Run simulation suite: `bash scripts/simulate-installers.sh`
- Output artifacts:
  - `artifacts/installer-sim/summary.json`
  - `artifacts/installer-sim/SUMMARY.md`
- CI uploads these artifacts on each PR via `.github/workflows/test-linux.yml`
- One-command maintainer gate: `bash scripts/release-gate.sh`

---

## 5-Minute Quickstart

> **Prerequisites:** `curl` and `jq` must be installed. The installer will auto-install `jq` if missing, but `curl` is required to fetch the installer itself.

```bash
# One-line install for Linux/macOS shells
curl -fsSL https://install.osmantic.com/ods.sh | bash
```

The hosted endpoint proxies the current bootstrap from repository `main`.
Reviewed merges reach it automatically after edge-cache refresh. `ODS_REF` selects a compatible repository checkout. See
[Installer Trust](docs/INSTALLER_TRUST.md) to inspect the script or install an
audited commit manually; no release has qualified for the verified channel yet.

Do not run the `curl ... | bash` installer from Windows PowerShell. Use the
Windows PowerShell installer below.

Or manually:

```bash
git clone https://github.com/Osmantic/ODS.git ~/src/ODS
cd ~/src/ODS
./install.sh
```

On macOS, keep the clone out of `~/ODS`: the disk is case-insensitive, so that
is the same folder as the default install directory `~/ods`.

On Linux, the Core Only and API-only gateway choices skip the optional Node.js,
Claude Code, and Codex CLI install. Use `./install.sh --with-devtools` to add
those host tools; `--no-devtools` skips future installs without removing any
existing binaries. The Custom menu offers the same separate choice.

The installer auto-detects your GPU, picks the right model, generates secure passwords, and starts everything. Open the address it prints when it finishes: **http://localhost:3001** for the ODS Dashboard and Portal, or **http://localhost:3000** for Open WebUI on hosts that use it.

On Linux Docker installs, llama-server is exposed to the host on **http://localhost:11434** (`OLLAMA_PORT`) and runs on `8080` inside Docker. Use `llama-server:8080` only from other containers on the ODS network. macOS native Metal and the Windows AMD `llama-server.exe` use **http://localhost:8080** unless overridden; the Windows server requires its API key.

To use a model server you already run, such as Ollama, LM Studio or Lemonade
Server, install with `--external-llm-url` and the related options; see
[Can ODS reuse a model already running in Ollama or LM Studio?](docs/FAQ.md#can-ods-reuse-a-model-already-running-in-ollama-or-lm-studio).
Installs made with the retired `--use-existing-lemonade` option move to that
route when the installer runs again; see
[AMD GPUs now run on llama.cpp](docs/MIGRATION-LEMONADE-TO-LLAMACPP.md).

### Instant Start (Bootstrap Mode)

By default, ODS uses **bootstrap mode** so you can start before the full model arrives:

1. It starts Qwen3.5 2B (about 1.3 GB) as soon as the core services are up
2. You can chat while the full model for your hardware downloads in the background
3. Once that download is verified, ODS restarts the model server on the full model
4. Use the Dashboard **Models** page to download and load other catalog models

Hermes-enabled installs keep this fast-start path: the bootstrap model runs at a
64K context floor so the agent can start cleanly, then the background full-model
swap keeps the tier selector's chosen context for the full model. On capable
tiers that may still be 128K; constrained tiers stay at the smaller selected
context instead of being forced higher.

Curated and Hugging Face GGUF discovery, verified imports, switching, and recovery: [docs/MODEL-MANAGEMENT.md](docs/MODEL-MANAGEMENT.md)

To skip bootstrap and wait for the full model: `./install.sh --no-bootstrap`

### macOS (Apple Silicon)

> **Prerequisite:** Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and make sure it is running before you start.

```bash
./install.sh    # Auto-detects chip, launches Metal-accelerated inference + Docker services
```

llama-server runs natively with Metal GPU acceleration; all other services run in Docker. See [`docs/MACOS-QUICKSTART.md`](docs/MACOS-QUICKSTART.md) for details.

If an initial native Pixel installation stopped at its final service checks,
keep Docker running and resume the retained installation as the same Mac user:

```bash
curl -fsSL https://raw.githubusercontent.com/Osmantic/ODS/main/ods/get-ods.sh | bash -s -- --recover
```

Recovery verifies the retained activation, reuses the installer Python and saved
choices, completes selected setup, opens Portal and follows the full-model
download. An older installation may ask whether you originally chose OpenCode.
It preserves the installation and stops if the retained activation cannot be
verified. See [native Pixel recovery](docs/PIXEL.md#guided-recovery-and-model-progress)
for progress, noninteractive use and the limits of these checks.

### Windows (NVIDIA + AMD)

> **Prerequisite:** Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) with WSL2 backend and make sure it is running before you start.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'
    $odsSrc = Join-Path $env:TEMP ('ods-install-' + [guid]::NewGuid().ToString('N'))
    $odsZip = Join-Path $odsSrc 'ods-main.zip'
    New-Item -ItemType Directory -Path $odsSrc | Out-Null
    Invoke-WebRequest -UseBasicParsing 'https://github.com/Osmantic/ODS/archive/refs/heads/main.zip' -OutFile $odsZip
    Expand-Archive -LiteralPath $odsZip -DestinationPath $odsSrc
    $odsEntry = Join-Path $odsSrc 'ODS-main\install.ps1'
    if (-not (Test-Path -LiteralPath $odsEntry -PathType Leaf)) { throw 'The downloaded archive does not contain the ODS installer.' }
    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
    & $odsEntry
}
```

The Windows entry point guides Ubuntu/WSL2 preparation and requires Pixel with
Hermes disabled. Start in normal PowerShell; feature preparation may request
administrator approval and a restart. The runtime lives at `~/ods` inside Ubuntu;
manage it there with `ods status` (or `./ods-cli status`). Docker Desktop must expose Docker/Compose
to that distribution. Existing native Windows installations are not migrated.

See [`docs/WINDOWS-QUICKSTART.md`](docs/WINDOWS-QUICKSTART.md) for details.

### Fresh reinstall with cached models

On Linux, WSL, or native macOS, `get-ods.sh --force --keep-models` replaces an
existing ODS installation while retaining `data/models`. All other installation
data, configuration, and runtime files are replaced. Without `--keep-models`,
`--force` keeps its normal full cleanup behavior. The option requires an
identified existing installation and is consumed by the bootstrap, not `install.sh`.

Preservation temporarily uses a `.models-backup` directory next to the
installation (for example `~/ods.models-backup`); an existing backup, a legacy
`~/.ods-models-backup`, or a symlink conflict blocks replacement. Resolve that backup manually before retrying.
If moving the models fails, remaining files stay in the original model directory
and/or the backup for recovery. Restored models follow the ordinary installer
validation and download path; retention does not itself verify their contents.

### Uninstall

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

Use `--keep-data` or `--keep-models` if you want to preserve local state.
On Linux and macOS, `--keep-data` keeps the `data` folder, the backups in
`.backups/`, the `presets/` folder, and the update snapshots in `~/.ods`.
It still deletes `.env` (your settings and generated secrets) and `config/`.
If you plan to reinstall over the kept data, copy those somewhere safe first
and put `.env` back before running the installer; without it, the installer
generates new secrets.

On Linux and macOS, `--keep-models` moves models to `<install>.models-backup`
(for example `~/ods.models-backup`). A later install does not automatically
restore a backup left by a standalone uninstall. After reinstalling, recover
needed models into `data/models` without overwriting existing files. Verify
the recovered models before deleting the backup, or move the backup aside;
the next `--keep-models` uninstall stops while it exists.
This is separate from `get-ods.sh --force --keep-models`, which restores the
backup it creates during that reinstall and refuses a pre-existing backup.

If a
Windows runtime is partial and `.\ods.ps1` is missing, run the cleanup from a
source checkout with `.\ods\installers\windows\ods.ps1 uninstall --force`.

---

## What's Included

| Component | Purpose | Port | Backend |
|-----------|---------|------|---------|
| **llama-server** | LLM inference engine | Linux Docker: 11434 host / 8080 container; native macOS/Windows: 8080 host | Core GPU backend |
| **Open WebUI** | Alternative chat interface; add from the Extensions Library on qualified Linux installs | 3000 when enabled | Optional on qualified Linux hosts |
| **Dashboard** | System status, GPU metrics, service health | 3001 | Core |
| **Dashboard API** | Backend API for dashboard | 3002 | Core |
| **LiteLLM** | Multi-model API gateway | 4000 | Recommended |
| **Token Spy** | Token usage monitor | 3005 | Recommended |
| **SearXNG** | Self-hosted web search | 8888 | Recommended |
| **Portal** | Core conversational assistant in Dashboard; default chat on fresh qualified Linux installs | Private Unix socket; no host TCP port | Core feature on qualified hosts |
| **Hermes Agent** | Independent general-purpose agent | 9120 via auth proxy; 9119 internal | Optional |
| **APE** | Agent Policy Engine for policy/audit controls | 7890 | Optional |
| **OpenCode** | Browser IDE / coding assistant | 3003 | Optional host service |
| **Perplexica** | Deep research engine | 3004 | Optional |
| **Brave Search** | Paid Brave Search API bridge | 8585 | Optional |
| **n8n** | Workflow automation | 5678 | Optional |
| **Qdrant** | Vector database for RAG | 6333 / 6334 gRPC | Optional |
| **TEI Embeddings** | Text embeddings for RAG | 8090 | Optional |
| **Whisper** | Speech-to-text | 9000 | Optional |
| **Kokoro** | Text-to-speech | 8880 | Optional |
| **Privacy Shield** | PII protection for API calls | 8085 | Optional |
| **Langfuse** | LLM observability and tracing | 3006 | Optional |
| **ComfyUI** | Image generation | 8188 | Optional GPU service |
| **Memory Shepherd** | Agent memory lifecycle management | — | Host/systemd helper |

On a fresh Linux host qualified for Pixel, the standard installer with default feature choices selects Portal chat and skips the Open WebUI image. Existing installations keep their saved WebUI choice. Use `--with-webui` during installation or add Open WebUI later from the Extensions Library. Hosts without a qualified Pixel runtime, and installs that select voice, RAG, or the LAN proxy, keep WebUI so those journeys remain available.

## Hardware Tiers

The installer **automatically detects your GPU**, assigns a hardware tier, then uses the versioned catalog selector to choose the best installable GGUF for the detected memory envelope. Linux and macOS call `scripts/select-model.py`; Windows uses the PowerShell selector in `installers/windows/lib/tier-map.ps1`. Both read `config/model-library.json`, and the final choice is written to `.env` as `LLM_MODEL`, `GGUF_FILE`, `MAX_CONTEXT`, and `MODEL_RECOMMENDATION_*`.

`MODEL_PROFILE=qwen` is the default non-Gemma catalog profile. The selector ranks installable models by a curated priority for the memory class (discrete GPU, unified memory or CPU), prefers a model that fits at the 64K context Hermes needs, and checks fit with a memory estimate built from each model's attention layout; file size only breaks ties. `MODEL_PROFILE=gemma4` and `MODEL_PROFILE=auto` are also supported where the tier map has Gemma 4 GGUFs available. When Hermes is enabled and the pick is below 64K, the installers re-check the fit at 64K before raising it, pick a model that fits at 64K when the installer chose the model, and otherwise keep the context that fits and report that ODS Talk is unavailable. A Dashboard model switch uses the same rule.

Large-context tiers still use 128K where the selected tier/model supports it.

The examples below are current catalog-selector outputs for common hardware envelopes. Exact installs can differ with detected VRAM/RAM, host architecture, existing downloads, or explicit profile overrides. Throughput still needs a local benchmark after first launch.

### AMD Strix Halo (Unified Memory)

| Tier / envelope | Current default catalog pick | Context | Example hardware |
|------|--------------|---------|-----------------|
| SH_COMPACT / 64GB unified RAM | qwen3.6-35b-a3b | 128K | Ryzen AI MAX+ 395 (64GB) |
| SH_LARGE / 96GB unified RAM | qwen3.6-35b-a3b | 128K | Ryzen AI MAX+ 395 (96GB) |
| SH_LARGE / 124GB unified RAM | qwen3.6-35b-a3b | 128K | Ryzen AI MAX+ 395 (128GB class) |

Unified-memory hosts are routed away from qwen3-coder-next when that model would otherwise be selected, because current repo policy documents correctness issues on those backends. Bootstrap mode uses `qwen3.5-2b` for instant startup; the full model downloads in the background via GGUF from HuggingFace.

**Inference backend:** llama.cpp's `llama-server`, as on every platform. Linux AMD installs run its Vulkan container image by default (ROCm is optional); Windows AMD installs run `llama-server.exe` (Vulkan) on Windows.

### NVIDIA (Discrete GPU)

| Tier / envelope | Current default catalog pick | Context | Example GPUs |
|------|--------------|---------|--------------|
| 0 / 8GB CPU fallback | qwen3.5-2b (Q8 KV CPU profile) | 64K | Low-RAM CPU-only |
| 1 / 8GB discrete VRAM | qwen3.5-9b (Q8 KV profile) | 64K | RTX 4060, RTX 5070 Laptop |
| 2 / 12-16GB discrete VRAM | qwen3.5-9b | 64K | RTX 4070-class, RTX 4080 |
| 3 / 24-32GB discrete VRAM | qwen3.5-27b | 64K | RTX 4090, RTX 5090 |
| 4 / 40-62GB discrete VRAM | qwen3.6-35b-a3b | 128K | A100 40GB, A6000 Ada, L40S |
| NV_ULTRA / 90GB+ amd64 discrete VRAM | qwen3-coder-next | 128K | Multi-GPU A100/H100 |
| NV_ULTRA / 90GB+ arm64 unified memory | qwen3.6-35b-a3b | 128K | DGX Spark / GB10-class hosts |

### Apple Silicon (Unified Memory, Metal)

| Tier / envelope | Current default catalog pick | Context | Example hardware |
|------|--------------|---------|-----------------|
| 0 / 8GB unified RAM | nvidia-nemotron3-nano-4b | 64K | M1/M2 base (8GB) |
| 1 / 16GB unified RAM | qwen3.5-9b | 64K | M4 Mac Mini (16GB) |
| 2 / 24-36GB unified RAM | qwen3.5-9b | 64K | M4 Pro Mac Mini, M3 Max MacBook Pro |
| 3 / 48GB unified RAM | qwen3.6-35b-a3b | 128K | M4 Pro (48GB), M2 Max (48GB) |
| 4 / 64GB+ unified RAM | qwen3.6-35b-a3b | 128K | M2 Ultra Mac Studio, M4 Max (64GB+) |

### Intel Arc (Linux, SYCL)

| Tier / envelope | Current default catalog pick | Context | Example hardware |
|------|--------------|---------|------------------|
| ARC_LITE / 6GB discrete VRAM | qwen3.5-4b | 64K | Arc A380 |
| ARC_LITE / 8GB discrete VRAM | qwen3.5-4b | 64K | Arc A750 |
| ARC / 16GB discrete VRAM | qwen3.5-9b | 64K | Arc A770 16GB, newer Arc GPUs |

Gemma 4 profile tiers remain in the installer tier maps: E2B on entry hardware, E4B on midrange hardware, 26B-A4B on pro hardware, and 31B on large/ultra hardware. Override with: `./install.sh --tier 3`.

See [docs/HARDWARE-GUIDE.md](docs/HARDWARE-GUIDE.md) for buying recommendations.

---

## Architecture

### AMD Strix Halo (platform-selected accelerated backend)

```
┌─────────────────────────────────────────────────┐
│                   Open WebUI                    │
│               (localhost:3000)                  │
└─────────────────────┬───────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────┐
│               llama-server backend              │
│     Linux host :11434 / Docker :8080/v1       │
│     native macOS/Windows host :8080/v1        │
│        catalog-selected local GGUF model        │
└─────────────────────────────────────────────────┘
         │                              │
┌────────▼────────┐            ┌───────▼────────┐
│ Pixel / Hermes  │            │    Dashboard    │
│ agent selection │            │ (Pixel toolbar) │
└─────────────────┘            └────────────────┘

┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ n8n (:5678) │  │Qdrant(:6333)│  │LiteLLM(:4000)│
│  Workflows  │  │  Vector DB  │  │ API Gateway │
└─────────────┘  └─────────────┘  └─────────────┘
```

### NVIDIA (llama-server + CUDA)

```
┌─────────────────────────────────────────────────┐
│                   Open WebUI                    │
│               (localhost:3000)                  │
└─────────────────────┬───────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────┐
│               llama-server (CUDA)               │
│     Linux host :11434 / Docker :8080/v1          │
│        catalog-selected local GGUF model        │
└─────────────────────────────────────────────────┘
         │                              │
┌────────▼────────┐            ┌───────▼────────┐
│    Whisper      │            │     Kokoro      │
│ (STT :9000)     │            │ (TTS :8880)     │
└─────────────────┘            └────────────────┘

┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ n8n (:5678) │  │Qdrant(:6333)│  │LiteLLM(:4000)│
│  Workflows  │  │  Vector DB  │  │ API Gateway │
└─────────────┘  └─────────────┘  └─────────────┘
```

## Modding & Customization

### Extension Services

Each service under `extensions/services/` IS the mod. Drop in a directory, run `ods enable <service>`, and it appears in compose, CLI, dashboard, and health checks.

```
extensions/services/
  my-service/
    manifest.yaml      # Service metadata, aliases, category
    compose.yaml       # Docker Compose fragment (auto-merged)
```

```bash
ods enable my-service    # Enable an extension
ods disable my-service   # Disable it
ods list                 # See all services and status
```

Full guide: [docs/EXTENSIONS.md](docs/EXTENSIONS.md)

### Installer Architecture

The installer is modular — 41 library modules, a shared service registry, and
14 ordered phase files. The architecture doc also maps the generated config writers
that have to stay in sync across Linux, macOS, Windows, bootstrap upgrades, and
host-agent model activation.
Want to add a hardware tier, swap the theme, or skip a phase? Start with the
module that owns that behavior, then check the generated-config writer map
before shipping.

```
installers/lib/       # Pure function libraries (colors, GPU detection, tier mapping)
installers/phases/    # Sequential install steps (01-preflight through 13-summary)
install-core.sh       # Orchestrator that sources the libraries and runs the phases
```

Every file has a standardized header: Purpose, Expects, Provides, Modder notes.

Full guide with copy-paste recipes: [docs/INSTALLER-ARCHITECTURE.md](docs/INSTALLER-ARCHITECTURE.md)

## Configuration

The installer generates `.env` automatically. Key settings:

```bash
# NVIDIA
LLM_MODEL=qwen3.5-27b                     # Example catalog-selected model
CTX_SIZE=32768                             # Context window
MODEL_PROFILE=qwen                         # qwen, gemma4, or auto
OLLAMA_PORT=11434                          # Host API port for llama-server

# AMD Strix Halo
LLM_MODEL=qwen3.6-35b-a3b                 # Catalog-selected; varies by RAM/arch
CTX_SIZE=131072                            # Context window
GPU_BACKEND=amd                            # Set automatically by installer

# Advanced llama-server tuning
LLAMA_ARG_FLASH_ATTN=auto                  # auto, on, or off
LLAMA_ARG_CACHE_TYPE_K=f16                 # f16 or q8_0
LLAMA_ARG_CACHE_TYPE_V=f16                 # f16 or q8_0
# LLAMA_ARG_N_CPU_MOE=25                   # Optional MoE-only CPU expert offload
# LLAMA_SPEC_TYPE=none                     # Turn off the NVIDIA/CPU ngram-mod default
# LLAMA_ARG_SPEC_TYPE=draft-mtp            # Optional MTP speculative decoding
# LLAMA_ARG_SPEC_DRAFT_N_MAX=3             # Optional MTP draft token cap
```

## ods-cli

The `ods` CLI is the primary management tool. It's installed automatically at `~/ods/ods-cli` and can be symlinked to your PATH.

```bash
# Service management
ods status              # Health checks + GPU status
ods list                # Show all services and their state
ods logs <service>      # Tail logs (accepts aliases: llm, stt, tts)
ods restart [service]   # Restart one or all services
ods start / stop        # Start or stop the stack

# LLM mode switching
ods mode                # Show current mode (local/cloud/hybrid)
ods mode cloud          # Switch to cloud APIs via LiteLLM
ods mode local          # Switch to local llama-server
ods mode hybrid         # Local primary, cloud fallback

# Model management (local mode)
ods model current       # Show active model
ods model list          # List available tiers
ods model swap T3       # Switch to a different tier

# Extensions
ods enable n8n          # Enable an extension
ods disable whisper     # Disable an extension

# Configuration
ods config show         # View .env (secrets masked)
ods config edit         # Open .env in editor
ods preset save <name>  # Snapshot current config
ods preset load <name>  # Restore a saved preset
```

Full mode-switching documentation: [docs/MODE-SWITCH.md](docs/MODE-SWITCH.md)
Model discovery, verified Hugging Face imports, switching, and recovery: [docs/MODEL-MANAGEMENT.md](docs/MODEL-MANAGEMENT.md)

## Showcase & Demos

```bash
# Interactive showcase (requires running services)
./scripts/showcase.sh

# Offline demo mode (no GPU/services needed)
./scripts/demo-offline.sh

# Run integration tests
./tests/integration-test.sh
```

## Useful Commands

```bash
# ods-cli handles compose flags automatically (works on AMD and NVIDIA)
ods status                     # Check all services
ods list                       # See available services and status
ods logs llm                   # Watch llama-server logs (alias: llm)
ods logs stt                   # Watch Whisper logs (alias: stt)
ods restart whisper            # Restart a service
ods enable n8n                 # Enable an extension
ods disable comfyui            # Disable an extension
ods stop                       # Stop everything
ods start                      # Start everything

# Management scripts
./scripts/llm-cold-storage.sh --status   # Check model hot/cold storage
ods mode                               # Show current mode
```

## Comparison

ODS builds on tools you may already use rather than replacing them. It installs them, wires them
together, and gives you one place to run them.

| If you use… | It gives you | ODS adds |
|---|---|---|
| **Ollama, llama.cpp or LM Studio** | Local model serving | Hardware-aware model choice, the surrounding services, and a dashboard to manage them |
| **Open WebUI** | A chat UI with RAG, voice and image-generation integrations | Installs and configures it next to local inference, speech, search and image services, and manages sign-in and network exposure |
| **LocalAI** | An OpenAI-compatible API with text, audio and image backends | A dashboard, the Portal agent, workflows, and a catalog of add-on apps around local inference |
| **n8n's self-hosted AI starter kit** | Workflow automation with a local model | Workflows as one part of a managed local AI server |

ODS's own focus is the layer around those tools: detecting your hardware (NVIDIA, AMD Strix Halo,
Apple Silicon, with CPU and cloud fallbacks; Intel Arc by manual `--tier`), choosing and verifying
models, lifecycle commands (install, update, backup, uninstall), and an extension catalog.

---

## Troubleshooting FAQ

**llama-server won't start / OOM errors**
- Reduce `CTX_SIZE` in `.env` (try 4096)
- Use a smaller model: `./install.sh --tier 1`

**"Model not found" on first boot**
- First launch downloads the model (10-30 min depending on size)
- Watch progress: `ods logs llm`

**Open WebUI shows "Connection error"**
- llama-server is still loading. On Linux Docker installs, wait for the host health check to pass: `curl localhost:11434/health`
- On macOS native Metal and with the Windows AMD `llama-server.exe`, use `curl localhost:8080/health`
- From another container on the ODS network, use `http://llama-server:8080/health`

**Port already in use**
- Change ports in `.env` (e.g., `WEBUI_PORT=3001`)
- Or stop the conflicting service: `sudo lsof -i :3000`

**Docker permission denied**
- Add yourself to the docker group: `sudo usermod -aG docker $USER`
- Log out and back in for it to take effect

**WSL: GPU not detected**
- Install NVIDIA drivers on Windows (not inside WSL)
- Verify with `nvidia-smi` inside WSL
- Ensure Docker Desktop has WSL integration enabled

**AMD Strix Halo: llama-server won't start**
- Check GGUF model exists: `ls -lh data/models/*.gguf`
- Watch logs: `ods logs llama-server`
- Verify the GPU render node: `ls /dev/dri/renderD128`. The ROCm image (`AMD_INFERENCE_BACKEND=rocm`) also needs `/dev/kfd`
- Leave `HSA_OVERRIDE_GFX_VERSION` unset unless the installer set it: the default Vulkan image ignores it, and the ROCm image is built for Strix Halo (gfx1151)

**AMD: "missing tensor" errors**
- Use upstream llama.cpp GGUF files (from `unsloth/` on HuggingFace)
- Ollama's GGUF format has incompatible tensor naming for qwen3next architecture
- Do NOT use Ollama blob files with llama-server

---

## Documentation

- [docs/README.md](docs/README.md) — **Full documentation index** (start here)
- [BUILD-ON-ODS-SERVER.md](docs/BUILD-ON-ODS-SERVER.md) — Forking, custom editions, extension templates, and downstream validation
- [QUICKSTART.md](QUICKSTART.md) — Detailed setup guide
- [HEADLESS-SETUP.md](docs/HEADLESS-SETUP.md) — QR onboarding, first-boot setup, AP mode, mDNS, and local agent access
- [MODEL-MANAGEMENT.md](docs/MODEL-MANAGEMENT.md) — Curated and Hugging Face GGUF discovery, verified imports, switching, and recovery
- [HARDWARE-GUIDE.md](docs/HARDWARE-GUIDE.md) — What to buy
- [EXTENSIONS.md](docs/EXTENSIONS.md) — Add services, manifests, dashboard plugins
- [INSTALLER-ARCHITECTURE.md](docs/INSTALLER-ARCHITECTURE.md) — Modding the installer
- [INTEGRATION-GUIDE.md](docs/INTEGRATION-GUIDE.md) — Connect your apps
- [SECURITY.md](SECURITY.md) — Security best practices
- [CHANGELOG.md](CHANGELOG.md) — Version history

## Acknowledgments

ODS exists because of the incredible people, projects, and communities that make open-source AI possible. We are grateful to every contributor, maintainer, and tinkerer whose work powers this stack.

Thanks to [lhl](https://github.com/lhl) for [strix-halo-testing](https://github.com/lhl/strix-halo-testing) — the foundational Strix Halo AI research and rocWMMA performance work that the broader community builds on.

### Projects that make ODS possible

*   [llama.cpp (ggerganov)](https://github.com/ggml-org/llama.cpp) — LLM inference engine
*   [Qwen (Alibaba Cloud)](https://github.com/QwenLM/Qwen) — Default language models
*   [Open WebUI](https://github.com/open-webui/open-webui) — Chat interface
*   [ComfyUI](https://github.com/comfyanonymous/ComfyUI) — Image generation engine
*   [SDXL Lightning (ByteDance)](https://huggingface.co/ByteDance/SDXL-Lightning) — Image generation model
*   [AMD ROCm](https://github.com/ROCm/ROCm) — GPU compute platform
*   [Strix Halo Testing (lhl)](https://github.com/lhl/strix-halo-testing) — Foundational Strix Halo AI research and rocWMMA optimizations
*   [n8n](https://github.com/n8n-io/n8n) — Workflow automation
*   [Qdrant](https://github.com/qdrant/qdrant) — Vector database
*   [SearXNG](https://github.com/searxng/searxng) — Privacy-respecting search
*   [Perplexica](https://github.com/ItzCrazyKns/Perplexica) — AI-powered search
*   [LiteLLM](https://github.com/BerriAI/litellm) — LLM API gateway
*   [Kokoro FastAPI (remsky)](https://github.com/remsky/Kokoro-FastAPI) — Text-to-speech
*   [Speaches](https://github.com/speaches-ai/speaches) — Speech-to-text
*   [Strix Halo Home Lab](https://strixhalo-homelab.d7.wtf/) — Community knowledge base

### Community Contributors

For the full contributor list with detailed credits, see the [Wall of Heroes](../README.md#wall-of-heroes) in the root README.

If we missed anyone, [open an issue](https://github.com/Osmantic/ODS/issues). We want to get this right.

---

## License

ODS code is Apache-2.0 except the bundled Pixel source, which has a separate
ODS-only use and distribution grant. See [Licensing](LICENSING.md),
[LICENSE](LICENSE), and [Pixel's license](vendor/pixel/LICENSE.md).

---

*Built by [The Collective](https://github.com/Osmantic/ODS) — Android-17, Todd, and friends*
