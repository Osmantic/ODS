# ODS

**Osmantic Deployment System**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](../LICENSE)
[![Docker](https://img.shields.io/badge/Docker-Required-2496ED?logo=docker)](https://docs.docker.com/get-docker/)
[![NVIDIA](https://img.shields.io/badge/NVIDIA-GPU%20Accelerated-76B900?logo=nvidia)](https://developer.nvidia.com/cuda-toolkit)
[![AMD](https://img.shields.io/badge/AMD-Strix%20Halo%20ROCm-ED1C24?logo=amd)](https://rocm.docs.amd.com/)
[![n8n](https://img.shields.io/badge/n8n-Workflows-FF6D5A?logo=n8n)](https://n8n.io)

**Your turnkey local AI stack.** Buy hardware. Run installer. AI running.

---

## Platform Support

> | Platform | Status |
> |----------|--------|
> | **Linux** (NVIDIA + AMD + Intel Arc) | **Supported** — install and run today |
> | **macOS** (Apple Silicon) | **Supported** — install and run today |
> | **Windows** (NVIDIA + AMD) | **Supported** — install and run today |
>
> All three platforms are fully supported with one-command installers. See [`docs/SUPPORT-MATRIX.md`](docs/SUPPORT-MATRIX.md) for detailed tier status.

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

## 5-Minute Quickstart (Linux)

> **Prerequisites:** `curl` and `jq` must be installed. The installer will auto-install `jq` if missing, but `curl` is required to fetch the installer itself.

```bash
# One-line install (Linux — NVIDIA, AMD, Intel Arc, or CPU/cloud fallback)
curl -fsSL https://install.osmantic.com/ods.sh | bash
```

The hosted endpoint proxies the current bootstrap from repository `main`.
Reviewed merges reach it automatically after edge-cache refresh. `ODS_REF` selects a compatible repository checkout. See
[Installer Trust](docs/INSTALLER_TRUST.md) to inspect the script or install a
stable release or audited commit manually.

Or manually:

```bash
git clone https://github.com/Osmantic/ODS.git
cd ODS
./install.sh
```

The installer auto-detects your GPU, picks the right model, generates secure passwords, and starts everything. Open **http://localhost:3000** and start chatting.

On Linux Docker installs, llama-server is exposed to the host on **http://localhost:11434** (`OLLAMA_PORT`) and runs on `8080` inside Docker. Use `llama-server:8080` only from other containers on the ODS network. macOS native Metal and Windows native/Lemonade paths use **http://localhost:8080** unless overridden.

On Linux AMD hosts already running Lemonade SDK, install ODS around it with
`./install.sh --use-existing-lemonade` so ODS manages the app stack while
Lemonade keeps owning inference and model storage. The installer auto-detects
common Lemonade ports and the first served model, then verifies a real completion
through LiteLLM before declaring success. See
[docs/LEMONADE-SDK-COMPAT.md](docs/LEMONADE-SDK-COMPAT.md). Existing Lemonade
mode only reuses Lemonade for LLM inference; Full Stack still enables
ODS-managed Whisper, Kokoro, and ComfyUI unless you pass `--no-voice` and/or
`--no-comfyui` or choose alternate ports where supported.

### Instant Start (Bootstrap Mode)

By default, ODS uses **bootstrap mode** for instant gratification:

1. Starts immediately with a tiny 1.5B model (downloads in <1 minute)
2. You can start chatting within **2 minutes** of running the installer
3. The full model downloads in the background
4. Use the Dashboard **Models** page to download and load larger catalog models

No more staring at download bars. Start playing immediately.

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

### Windows (NVIDIA + AMD)

> **Prerequisite:** Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) with WSL2 backend and make sure it is running before you start.

```powershell
.\install.ps1   # Auto-detects GPU, launches all services via Docker Desktop + WSL2
```

Windows installs keep the cloned repo separate from the runtime directory. The
installer writes `.env`, models, logs, and compose state to
`$env:USERPROFILE\ods` by default (or `$env:ODS_HOME`). After
installing, run `.\ods.ps1` or manual `docker compose` commands from that
runtime directory, not from the source checkout.

See [`docs/WINDOWS-QUICKSTART.md`](docs/WINDOWS-QUICKSTART.md) for details.

---

## What's Included

| Component | Purpose | Port | Backend |
|-----------|---------|------|---------|
| **llama-server** | LLM inference engine | Linux Docker: 11434 host / 8080 container; native macOS/Windows: 8080 host | Core GPU backend |
| **Open WebUI** | Beautiful chat interface | 3000 | Core |
| **Dashboard** | System status, GPU metrics, service health | 3001 | Core |
| **Dashboard API** | Backend API for dashboard | 3002 | Core |
| **LiteLLM** | Multi-model API gateway | 4000 | Recommended |
| **Token Spy** | Token usage monitor | 3005 | Recommended |
| **SearXNG** | Self-hosted web search | 8888 | Recommended |
| **Hermes Agent** | Default local-first autonomous/browser agent | 9120 via auth proxy; 9119 internal | Default agent |
| **OpenClaw** | Deprecated legacy autonomous agent, opt-in during migration | 7860 | Deprecated optional |
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

## Hardware Tiers

The installer **automatically detects your GPU**, assigns a hardware tier, then uses the versioned catalog selector to choose the best installable GGUF for the detected memory envelope. Linux and macOS call `scripts/select-model.py`; Windows uses the PowerShell selector in `installers/windows/lib/tier-map.ps1`. Both read `config/model-library.json`, and the final choice is written to `.env` as `LLM_MODEL`, `GGUF_FILE`, `MAX_CONTEXT`, and `MODEL_RECOMMENDATION_*`.

`MODEL_PROFILE=qwen` is the default non-Gemma catalog profile, so the effective model can be Qwen, Phi, or DeepSeek depending on fit. `MODEL_PROFILE=gemma4` and `MODEL_PROFILE=auto` are also supported where the tier map has Gemma 4 GGUFs available. When Hermes is enabled, installers enforce a 64K minimum context for the active local model, then preserve the model selector's full-model context.

Large-context tiers still use 128K where the selected tier/model supports it.

The examples below are current catalog-selector outputs for common hardware envelopes. Exact installs can differ with detected VRAM/RAM, host architecture, existing downloads, or explicit profile overrides. Throughput still needs a local benchmark after first launch.

### AMD Strix Halo (Unified Memory)

| Tier / envelope | Current default catalog pick | Context | Example hardware |
|------|--------------|---------|-----------------|
| SH_COMPACT / 64GB unified RAM | qwen3.6-35b-a3b | 128K | Ryzen AI MAX+ 395 (64GB) |
| SH_LARGE / 96GB unified RAM | deepseek-r1-distill-llama-70b | 32K | Ryzen AI MAX+ 395 (96GB) |
| SH_LARGE / 124GB unified RAM | qwen3.6-35b-a3b | 128K | Ryzen AI MAX+ 395 (128GB class) |

Unified-memory hosts are routed away from qwen3-coder-next when that model would otherwise be selected, because current repo policy documents correctness issues on those backends. Bootstrap mode uses `qwen3.5-2b` for instant startup; the full model downloads in the background via GGUF from HuggingFace.

**Inference backend:** selected by the platform installer and support matrix. Linux AMD paths use ROCm-capable containers; Windows Strix Halo uses the Windows-specific accelerated path.

### NVIDIA (Discrete GPU)

| Tier / envelope | Current default catalog pick | Context | Example GPUs |
|------|--------------|---------|--------------|
| 0 / 8GB CPU fallback | qwen3.5-2b | 8K | Low-RAM CPU-only |
| 1 / 8GB discrete VRAM | qwen3.5-9b | 32K | RTX 4060, RTX 3060 12GB |
| 2 / 12GB discrete VRAM | phi-4 | 16K | RTX 4070-class cards |
| 3 / 24GB discrete VRAM | qwen3.5-27b | 32K | RTX 4090, A6000 |
| 4 / 48GB discrete VRAM | deepseek-r1-distill-llama-70b | 32K | A6000 Ada, L40S |
| NV_ULTRA / 90GB+ amd64 discrete VRAM | qwen3-coder-next | 128K | Multi-GPU A100/H100 |
| NV_ULTRA / 90GB+ arm64 unified memory | qwen3.6-35b-a3b | 128K | DGX Spark / GB10-class hosts |

### Apple Silicon (Unified Memory, Metal)

| Tier / envelope | Current default catalog pick | Context | Example hardware |
|------|--------------|---------|-----------------|
| 0 / 8GB unified RAM | phi-4-mini | 128K | M1/M2 base (8GB) |
| 1 / 16GB unified RAM | qwen3.5-9b | 32K | M4 Mac Mini (16GB) |
| 2 / 32GB unified RAM | phi-4 | 16K | M4 Pro Mac Mini, M3 Max MacBook Pro |
| 3 / 48GB unified RAM | qwen3.5-27b | 32K | M4 Pro (48GB), M2 Max (48GB) |
| 4 / 64GB+ unified RAM | qwen3.6-35b-a3b | 128K | M2 Ultra Mac Studio, M4 Max (64GB+) |

### Intel Arc (Linux, SYCL)

| Tier / envelope | Current default catalog pick | Context | Example hardware |
|------|--------------|---------|------------------|
| ARC_LITE / 6GB discrete VRAM | phi-4-mini | 128K | Arc A380 |
| ARC_LITE / 8GB discrete VRAM | qwen3.5-9b | 32K | Arc A750 |
| ARC / 16GB discrete VRAM | phi-4 | 16K | Arc A770 16GB, newer Arc GPUs |

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
│ Hermes Agent    │            │    Dashboard    │
│ (default agent) │            │ (Status :3001)  │
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

The installer is modular — 19 library modules, a shared service registry, and
13 ordered phases. The architecture doc also maps the generated config writers
that have to stay in sync across Linux, macOS, Windows, bootstrap upgrades, and
host-agent model activation.
Want to add a hardware tier, swap the theme, or skip a phase? Start with the
module that owns that behavior, then check the generated-config writer map
before shipping.

```
installers/lib/       # Pure function libraries (colors, GPU detection, tier mapping)
installers/phases/    # Sequential install steps (01-preflight through 13-summary)
install-core.sh       # Thin orchestrator (~150 lines)
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
./scripts/session-cleanup.sh             # Clean up bloated agent sessions
./scripts/llm-cold-storage.sh --status   # Check model hot/cold storage
ods mode                               # Show current mode
```

## Comparison

| Feature | ODS | Ollama + WebUI | LocalAI |
|---------|:---:|:---:|:---:|
| Full-stack one-command install | **LLM + agent + workflows + RAG** | LLM + chat only | LLM only |
| Hardware auto-detect + model selection | **NVIDIA + AMD Strix Halo + Apple Silicon + Intel Arc + CPU/cloud fallback** | No | No |
| AMD APU / unified memory support | **Platform-specific accelerated backend selected by installer** | Partial (Vulkan) | No |
| Inference engine | **llama-server** (all GPUs) | llama.cpp | llama.cpp |
| Autonomous AI agent | **Hermes Agent default; OpenClaw legacy opt-in** | No | No |
| Workflow automation | **n8n (400+ integrations)** | No | No |
| LLM usage monitoring | **Open WebUI built-in** | No | No |
| Multi-GPU | **Yes** (NVIDIA) | Partial | Partial |

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
- On macOS native Metal and Windows native/Lemonade paths, use `curl localhost:8080/health`
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
- Watch logs: `docker compose -f docker-compose.base.yml -f docker-compose.amd.yml logs -f llama-server`
- Verify GPU devices: `ls /dev/kfd /dev/dri/renderD128`
- Ensure ROCm env: `HSA_OVERRIDE_GFX_VERSION=11.5.1` must be set

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

Apache 2.0 — Use it, modify it, sell it. Just don't blame us.

---

*Built by [The Collective](https://github.com/Osmantic/ODS) — Android-17, Todd, and friends*
