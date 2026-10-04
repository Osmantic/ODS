<div align="center">

# ODS V3 Pre-Release

**Osmantic Deployment System**

**Public testing and refinement ahead of the official V3 launch.**

<p align="center">
  <a href="https://osmantic.com" target="_blank" rel="noopener noreferrer">
    <img src="ods/docs/images/osmantic-lockup.png" alt="Osmantic" width="800">
  </a>
</p>

**Turn your PC, Mac, or Linux box into a private AI server.**

AI server and homelab setup is rapidly becoming a solved problem.
It should feel that way for everyone.

[![License: Apache 2.0 + Pixel ODS-only](https://img.shields.io/badge/License-Apache%202.0%20%2B%20Pixel%20ODS--only-blue.svg)](ods/LICENSING.md)
[![GitHub Stars](https://img.shields.io/github/stars/Osmantic/ODS)](https://github.com/Osmantic/ODS/stargazers)
[![ODS V3 Pre-Release](https://img.shields.io/badge/ODS-V3%20Pre--Release-orange)](ods/docs/RELEASE_NOTES_3.0.0.md)
[![Release](https://img.shields.io/badge/release-v3.0.0-blue)](https://github.com/Osmantic/ODS/releases/tag/v3.0.0)

[![Watch the demo](https://img.shields.io/badge/Demo-Watch%20on%20YouTube-red?logo=youtube)](https://youtu.be/nO8xFNHX-HA)

</div>

ODS installs and wires together everything you need to run AI locally, so you do not have to assemble Ollama, Open WebUI, n8n, ComfyUI, and privacy tools by hand:

- **Local model inference** — run open models on your own hardware
- **ChatGPT-style web UI** — talk to your models from any browser
- **Control dashboard** — manage models, services, setup, GPU status, and extensions from one place
- **Voice, agents, and workflows** — build automations that can listen, speak, call tools, and get work done
- **RAG and search** — connect local documents, private search, and retrieval workflows
- **Image generation** — run local image tools without sending prompts to a hosted API
- **Privacy and ops** — keep service auth, secrets, observability, and diagnostics in one local stack

No cloud required. No subscriptions required. Inference, chat history and your files stay on your machine, and ODS collects no telemetry. By default it goes online only to download models and container images, to check GitHub for ODS releases, and to run web searches the Portal agent makes for you; [the FAQ](ods/FAQ.md#is-my-data-private) explains each one and how to turn it off. Cloud and hybrid API modes are optional when you want them.

> **Status: V3 pre-release.** The installers below install the current `main`
> branch, which receives fixes continuously and is not a signed release; the
> [verified installer preview](ods/docs/VERIFIED_INSTALL_PREVIEW.md) stays
> separate until a signed release passes end-to-end testing.
> Existing installations pick up code fixes by re-running the installer
> ([how](ods/SECURITY.md#updating-an-existing-installation)); `ods update` refreshes
> container images only. `v3.0.0` is the latest published source release; check the
> [security advisories](https://github.com/Osmantic/ODS/security/advisories) before
> pinning it. Known limits and validation
> status: [V3 release notes](ods/docs/RELEASE_NOTES_3.0.0.md),
> [Release Validation](ods/docs/RELEASE_VALIDATION.md),
> [Release Channels](ods/docs/RELEASE_CHANNELS.md) and
> [Installer Trust](ods/docs/INSTALLER_TRUST.md).

The repository root holds this README, the installers, the security policy and
CI workflows; `ods/` is the product: services, installer phases, compose files,
dashboard, CLI, tests and operator docs.

## Get Started

Choose your system, copy the block, run it in a normal terminal. ODS installs the stack, picks a model for your hardware, starts the services, and gives you the local web UI.

**Linux or macOS**

```bash
curl -fsSL https://install.osmantic.com/ods.sh | bash
```

**Windows PowerShell** — guided Ubuntu/WSL2 setup with Pixel/Portal

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

Linux and macOS: Docker must be installed and running.

Windows: open a **normal PowerShell window** (not "Run as administrator"), paste the block, and answer the prompts. Nothing else needs to be installed first. The installer:

1. Checks free disk space (40 GB) and that hardware virtualization is on.
2. Offers to enable WSL2 and install Docker Desktop with winget. Windows asks for administrator permission, then **one restart**; setup continues by itself after you sign in again.
3. Offers to download Ubuntu 24.04 and asks you, in PowerShell, for a new Ubuntu username and password.
4. Starts Docker Desktop and checks that it is connected to Ubuntu. If not, it shows the one setting to turn on in Docker Desktop and continues as soon as it works.
5. Installs ODS inside Ubuntu with **`--pixel --no-hermes --no-openclaw`**. When Ubuntu asks for your `[sudo] password`, type the Ubuntu password; nothing appears while you type.
6. Verifies Pixel and Portal, then opens Portal in your browser and adds an **ODS Portal** shortcut to your desktop.

Each step asks before changing anything and stops with instructions if it cannot finish; rerun the same command after fixing it. There is no fallback to Hermes or the native Windows installer. On NVIDIA machines, update the Windows driver to 570 or newer first. To use an existing distribution, add `-Distro <name>` (names from `wsl -l -v`).

Existing native Windows installations are not automatically migrated or deleted; see [Windows Quickstart](ods/docs/WINDOWS-QUICKSTART.md#existing-native-windows-installations) before switching.

If another device runs your ODS model gateway and this Windows PC only needs
image generation, use the [standalone ComfyUI installer](ods/docs/WINDOWS-COMFYUI-STANDALONE.md).
It keeps its Docker project and data separate from a full ODS installation.

The hosted Linux/macOS endpoint proxies the current bootstrap from repository `main`.
Reviewed merges reach it automatically after edge-cache refresh. `ODS_REF` selects a compatible repository checkout. See
[Installer Trust](ods/docs/INSTALLER_TRUST.md) to inspect the script or install
a stable release or audited commit manually.

Windows users should not run the `curl ... | bash` command from PowerShell. The PowerShell block above downloads the public ODS source ZIP and delegates installation to Ubuntu/WSL2. For more detail, see the [Windows Quickstart](ods/docs/WINDOWS-QUICKSTART.md).

After the installer completes successfully, Portal opens at **http://localhost:3001/pixel** (the Windows installer opens it for you and prints the exact URL). **http://localhost:3000** is Open WebUI, a separate interface. Verify that Portal is available and send a message; a loaded dashboard alone does not prove Pixel is ready. If installation fails or Portal is degraded, follow the [Windows Quickstart checks](ods/docs/WINDOWS-QUICKSTART.md#verify-portalpixel) before proceeding.

WSL GPU access must be checked separately. NVIDIA needs a supported Windows driver and GPU access inside WSL/Docker. On AMD, Windows setup runs Lemonade through an ODS task bound to the selected WSL installation. Once that ownership is verified, Dashboard **Models** supports compatible GGUF downloads (including Hugging Face), activation, context changes, and unload/resume. An independently configured Lemonade service remains externally managed. Older ODS tasks without the installation binding require an installer rerun; see the [Windows Quickstart](ods/docs/WINDOWS-QUICKSTART.md#manage-amd-models-from-portal) and [WSL2 GPU guide](ods/docs/WINDOWS-WSL2-GPU-GUIDE.md).

For Linux, macOS, or the recommended Windows/WSL installation, uninstall from the matching Linux/macOS terminal (open Ubuntu on Windows):

```bash
cd ~/ods
./ods-uninstall.sh --force
```

For a **native Windows** installation only:

```powershell
$installDir = "$env:USERPROFILE\ods"
cd $installDir
.\ods.ps1 uninstall --force
```

Windows recovery note: if the runtime folder is partial and `.\ods.ps1` is missing, run the same command from a source checkout as `.\ods\installers\windows\ods.ps1 uninstall --force`. It verifies the containers' Compose installation directory before removing resources. A shared `ods` project name does not authorize removing another Windows or WSL installation. Unattached volumes with no verifiable owner are preserved, with an error naming the resource; `--force` does not bypass this check.

> **API endpoint:** Linux Docker installs expose llama-server on **http://localhost:11434** by default (`OLLAMA_PORT`) while containers use `llama-server:8080`. macOS native Metal and Windows native/Lemonade paths use **http://localhost:8080** unless overridden. Open WebUI stays on **http://localhost:3000**.

> **No GPU?** ODS also runs in cloud mode — same full stack, powered by OpenAI/Anthropic/Together APIs instead of local inference:
> ```bash
> ./install.sh --cloud
> ```

> **Port conflicts?** Every port is configurable via environment variables. See [`.env.example`](ods/.env.example) for the full list, or override at install time:
> ```bash
> WEBUI_PORT=9090 ./install.sh
> ```

**New here?** Read the [Friendly Guide](ods/docs/HOW-ODS-SERVER-WORKS.md) or [listen to the audio version](https://open.spotify.com/episode/40MvqJ41bC8cEgvUyOyE3K) — a complete walkthrough of what ODS is, how it works, and how to make it your own. No technical background needed.

---

## At A Glance

| Question | Answer |
|----------|--------|
| **What is it?** | A local AI server stack for your own hardware, with a one-command Linux/macOS installer and a PowerShell installer for Windows. |
| **Who is it for?** | People who want private AI at home, in a lab, or on a workstation without hand-wiring a dozen services. |
| **What do I get?** | Local inference, Open WebUI chat, a control dashboard, voice, agents, workflows, RAG, search, image generation, privacy tools, observability, and developer tools. |
| **What does it run on?** | Linux, Windows with WSL2/Docker Desktop, and macOS Apple Silicon. |
| **Is cloud required?** | No. Local mode is the default; cloud and hybrid API modes are optional. |

| If you know... | ODS adds... |
|----------------|----------------------|
| **Ollama / llama.cpp** | The surrounding server stack: chat, dashboard, voice, RAG, workflows, agents, privacy, and service management. |
| **Open WebUI** | A full installer and control plane around Open WebUI, plus pre-wired local services. |
| **AnythingLLM** | Broader local AI appliance behavior beyond RAG: inference, chat, voice, workflows, image generation, and ops. |
| **n8n self-hosted AI starter kits** | Workflow automation as one part of a larger private AI server. |

---

> **Current Platform Support**
>
> | Platform | Status |
> |----------|--------|
> | **Linux** (NVIDIA + AMD Strix Halo) | **Supported** — see the hardware and distro limits in the support matrix |
> | **Linux + Intel Arc** (SYCL) | **Experimental / Tier C** — validation is hardware-specific |
> | **Windows** (NVIDIA + AMD) | **Supported** — install and run today |
> | **macOS** (Apple Silicon) | **Supported** — install and run today |
>
> **Linux distros:** CI smoke-tests package-manager detection and installer prerequisites on Ubuntu 26.04/24.04/22.04, Debian 12, Linux Mint 21.3, Fedora 41, Rocky Linux 9, Arch Linux, Manjaro, CachyOS and openSUSE Tumbleweed. Broader install coverage runs in the maintainers' distro lab and hardware fleet described in the [Validation Matrix](ods/docs/VALIDATION-MATRIX.md). On openSUSE, install Docker before running ODS. [Open an issue](https://github.com/Osmantic/ODS/issues) if your distro doesn't work.
>
> **Release validation:** Operational changes run through a release-grade gate
> that covers zero-prereq bootstrap, clean installs, product behavior,
> full-model capabilities, lifecycle recovery, and User Green. See
> [Release Validation](ods/docs/RELEASE_VALIDATION.md) and the
> [Validation Matrix](ods/docs/VALIDATION-MATRIX.md).
>
> **Windows:** Requires Docker Desktop with WSL2 backend. NVIDIA GPUs use Docker GPU passthrough; AMD Strix Halo runs through the platform-specific accelerated path documented in the Windows installer and support matrix.
>
> **macOS:** Requires Apple Silicon (M1+) and Docker Desktop. llama-server uses native Metal acceleration; Portal's gateway and managed host helpers also run natively. The UI, ingress, sandbox and supporting services run in Docker. See the [macOS Quickstart](ods/docs/MACOS-QUICKSTART.md).
>
> See the [Support Matrix](ods/docs/SUPPORT-MATRIX.md) for supported
> platform claims and the [Validation Matrix](ods/docs/VALIDATION-MATRIX.md)
> for the layered test surface used to test those claims.

---

## Why ODS?

A handful of companies control the vast majority of global AI traffic — and with it, your data, your costs, and your uptime. Every query you send to a centralized provider is business intelligence you don’t own, running on infrastructure you don’t control, priced on terms you can’t negotiate.

If AI is becoming critical infrastructure, it shouldn’t be rented. Self-hosting local AI should be a sovereign human right, not a career choice.

Because running your own AI shouldn't require a CS degree and a weekend of debugging CUDA drivers. Right now, setting up local AI means stitching together a dozen projects, writing Docker configs from scratch, and praying everything talks to each other. Most people give up and go back to paying OpenAI.

We built ODS so you don't have to.

- **One command** — detects your GPU, picks the right model, generates credentials, launches everything
- **Chat early** — bootstrap mode starts a small model as soon as the core services are up, while your full model downloads in the background
- **Pre-wired services** — the default Core install gives you local inference, the dashboard and a chat UI (the Portal agent on supported hosts, Open WebUI elsewhere); add voice, workflows, search, RAG, image generation, observability and developer tools from the installer menu or the Extensions page, already wired to each other
- **Fully moddable** — every service is an extension. Drop in a folder, run `ods enable`, done

<details>
<summary><b>Manual install (Linux)</b></summary>

```bash
git clone https://github.com/Osmantic/ODS.git
cd ODS/ods
./install.sh
```

</details>

<details>
<summary><b>Windows (PowerShell)</b></summary>

The installer prepares WSL2, Ubuntu and [Docker Desktop](https://www.docker.com/products/docker-desktop/) when they are missing; nothing needs to be installed first.

Open a normal **PowerShell** session (not "Run as administrator") and run:

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

> The `Set-ExecutionPolicy` command allows the installer script to run in the current session. It does not change your system-wide policy.
> Running as Administrator is not recommended for the installer because user-level paths such as `.opencode`, `data/`, and `.env` can be created with admin-owned permissions.

This command guides WSL/Ubuntu preparation and checks systemd and Docker integration before installing Pixel. See [Windows Quickstart](ods/docs/WINDOWS-QUICKSTART.md). The runtime is normally `~/ods` inside Ubuntu; manage it there with `ods status` (or `./ods-cli status` from `~/ods`). Open the Portal dashboard at the URL printed by the installer (normally http://localhost:3001). Native Windows `ods.ps1` commands do not manage this Linux runtime.

</details>

<details>
<summary><b>macOS (Apple Silicon)</b></summary>

Requires Apple Silicon (M1+) and [Docker Desktop](https://www.docker.com/products/docker-desktop/).
**Install Docker Desktop first and make sure it is running before you start.**

```bash
git clone https://github.com/Osmantic/ODS.git ~/src/ODS
cd ~/src/ODS/ods
./install.sh
```

Clone outside your home folder's `ods` path: macOS disks are case-insensitive, so a clone at `~/ODS` is the same folder as the default install directory `~/ods`.

The installer detects your chip, picks the right model for your unified memory, launches llama-server natively with Metal acceleration, and starts all other services in Docker. Manage with `./ods-macos.sh status`.

See the [macOS Quickstart](ods/docs/MACOS-QUICKSTART.md) for details.

</details>

---

## What's In The Box

### Chat & Inference
- **Open WebUI** — full-featured chat interface with conversation history, web search, document upload, and [30+ languages](https://docs.openwebui.com)
- **llama-server** — high-performance LLM inference with continuous batching, auto-selected for your GPU; Linux Docker host API defaults to `localhost:11434`, native macOS/Windows paths use `localhost:8080`, and container API runs on `8080`
- **LiteLLM** — API gateway supporting local/cloud/hybrid modes
- **TEI Embeddings** — text embedding service for RAG and search workflows

### Voice
- **Whisper** — speech-to-text
- **Kokoro** — text-to-speech

### Agents & Automation
- **Portal** — bundled core conversational assistant on Apple Silicon macOS and qualified Ubuntu 24.04/26.04 or Debian 12 systemd hosts, including qualified WSL2 installations through the Linux installer. No private repository access or separate license flag is required; available in the Dashboard and through a compatible Open WebUI model route. The native PowerShell installer does not install the Portal host runtime.
- **Hermes Agent** — independent general-purpose agent, available alongside Portal; includes memory, skills, and a proxy with optional owner-card gating; direct access by default
- **OpenClaw** — deprecated legacy autonomous agent, still opt-in during the migration window
- **n8n** — workflow automation with 400+ integrations (Slack, email, databases, APIs)
- **APE** — Agent Policy Engine for auditing and governing autonomous tool calls
- **OpenCode** — browser-based AI coding assistant wired to the local stack
- **Memory Shepherd** — host/systemd helper for agent memory lifecycle management

### Knowledge & Search
- **Qdrant** — vector database for retrieval-augmented generation (RAG)
- **SearXNG** — self-hosted web search (no tracking)
- **Perplexica** — deep research engine
- **Brave Search** — optional paid Brave Search API integration

### Creative
- **ComfyUI** — node-based image generation

### Privacy & Ops
- **Privacy Shield** — PII scrubbing proxy for API calls
- **Dashboard** — real-time GPU metrics, service health, model management
- **Dashboard API** — service health, setup, status, metrics, and management API behind the dashboard
- **Token Spy** — token usage monitor for local and proxied LLM traffic
- **Langfuse** — optional LLM observability and tracing

---

## Hardware Auto-Detection

The installer detects your GPU and first assigns a deterministic hardware tier. Linux and macOS then run the versioned catalog selector (`ods/scripts/select-model.py`), while Windows uses the PowerShell catalog selector in `ods/installers/windows/lib/tier-map.ps1`; both read `ods/config/model-library.json` to choose the best installable GGUF for the detected memory envelope. The final choice is written to `.env` as `LLM_MODEL`, `GGUF_FILE`, `MAX_CONTEXT`, and `MODEL_RECOMMENDATION_*`.

`MODEL_PROFILE=qwen` is the default catalog profile. `MODEL_PROFILE=gemma4` forces Gemma 4 where available, and `MODEL_PROFILE=auto` uses Gemma 4 on NVIDIA, AMD Strix Halo, Apple Silicon and Intel Arc tiers. Override the model family with `MODEL_PROFILE=gemma4 ./install.sh`. You can switch models at any time after installing (see [Switching Models](#switching-models)).

The tables below are what the catalog selector picks today for common hardware with the default profile and a 64K context floor (`python ods/scripts/select-model.py --profile qwen --installable-only --min-context 65536 …`). Exact picks can differ with detected VRAM/RAM, host architecture and existing downloads. Measure throughput on your own machine after the first launch.

### NVIDIA

| Memory | Default pick | Context | Example hardware |
|--------|--------------|---------|------------------|
| 8–16 GB VRAM | Qwen3.5 9B (Q4_K_M) | 64K | RTX 4060, RTX 3060 12GB, RTX 4070-class |
| 24–32 GB VRAM | Qwen3.5 27B (Q4_K_M) | 64K | RTX 4090, RTX 5090 |
| 48 GB VRAM | Qwen3.6 35B-A3B (UD-Q4_K_M) | 128K | RTX 6000 Ada, L40S |
| 96 GB+ VRAM (x86_64) | Qwen3 Coder Next (Q4_K_M) | 128K | RTX PRO 6000, multi-GPU |
| 128 GB unified (arm64) | Qwen3.6 35B-A3B (UD-Q4_K_M) | 128K | DGX Spark / GB10 |

### AMD Strix Halo (Unified Memory)

| Memory | Default pick | Context | Hardware |
|--------|--------------|---------|----------|
| 64–128 GB unified | Qwen3.6 35B-A3B (UD-Q4_K_M) | 128K | Ryzen AI MAX+ 395 |

The selector routes unified-memory hosts away from Qwen3 Coder Next because of documented correctness issues on those backends.

### Apple Silicon (Unified Memory, Metal)

| Memory | Default pick | Context | Example hardware |
|--------|--------------|---------|------------------|
| 8 GB | NVIDIA Nemotron3 Nano 4B (Q4_K_M) | 64K | M1/M2 base |
| 16–36 GB | Qwen3.5 9B (Q4_K_M) | 64K | M4 Mac mini, M3 Pro |
| 48 GB+ | Qwen3.6 35B-A3B (UD-Q4_K_M) | 128K | M4 Pro/Max, Mac Studio |

### CPU only

| System RAM | Default pick | Context |
|------------|--------------|---------|
| 8–12 GB | Qwen3.5 2B (Q4_K_M) | 64K |
| 16–20 GB | Qwen3.5 4B (Q4_K_M) | 64K |
| 24 GB+ | Qwen3.5 9B (Q4_K_M) | 64K |

### Intel Arc (Linux, SYCL)

Intel Arc is experimental (Tier C). Hardware detection does not identify Arc GPUs yet, so pass the tier yourself: `./install.sh --tier ARC_LITE` for 6–8 GB cards or `--tier ARC` for 16 GB cards. See the [Intel Arc guide](ods/docs/INTEL-ARC-GUIDE.md).

---

## Bootstrap Mode

ODS uses bootstrap mode by default so you can start before a large model finishes downloading:

1. The installer downloads Qwen3.5 2B (about 1.3 GB) and starts it.
2. You can chat as soon as the core services are up.
3. The full model for your hardware downloads in the background.
4. When the download is verified, ODS restarts the model server on the full model. Requests in flight during that restart need a retry.

The bootstrap model starts with a 64K context window so agents work during the first session. After the swap, ODS restores the full model's context target.

Skip bootstrap: `./install.sh --no-bootstrap`

---

## Switching Models

The installer picks a model for your hardware, but you can switch anytime. The recommended way is **Dashboard → Models**: it lists models that fit your hardware, downloads them with checksum verification, and loads them. If a model fails to start, ODS restores the previous one.

From the command line:

```bash
ods model current              # What's running now?
```

Already have a GGUF you want to use? Drop the single `.gguf` file in
`data/models/`, then open Dashboard → Models and load the local entry. For
headless maintenance you can instead set `GGUF_FILE` and `LLM_MODEL` in `.env`
and restart the model server:

```bash
ods restart llm
```

A manual `.env` change has no automatic rollback. If the model fails to load,
set the previous values back and restart again.

---

## Extensibility

ODS is designed to be modded. Every service is an extension — a folder with a `manifest.yaml` and a `compose.yaml`. The dashboard, CLI, health checks, and compose stack all discover extensions automatically.

```
extensions/services/
  my-service/
    manifest.yaml      # Metadata: name, port, health endpoint, GPU backends
    compose.yaml       # Docker Compose fragment (auto-merged into the stack)
```

```bash
ods enable my-service     # Enable it
ods disable my-service    # Disable it
ods list                  # See everything
```

The installer itself is modular — 41 library modules, a shared service registry, and 14 ordered phase files. Want to add a hardware tier, swap a default model, or skip a phase? Start with the installer architecture map so you update the Linux, macOS, Windows, upgrade, and host-agent writers together.

[Full extension guide](ods/docs/EXTENSIONS.md) | [Installer architecture](ods/docs/INSTALLER-ARCHITECTURE.md)

---

## ods-cli

The `ods` CLI manages your entire stack:

```bash
ods status                # Health checks + GPU status
ods list                  # All services and their state
ods logs llm              # Tail logs (aliases: llm, stt, tts)
ods restart [service]     # Restart one or all services
ods start / stop          # Start or stop the stack

ods mode cloud            # Switch to cloud APIs via LiteLLM
ods mode local            # Switch back to local inference
ods mode hybrid           # Local primary, cloud fallback

ods model swap T3         # Switch to a different hardware tier
ods enable n8n            # Enable an extension
ods disable whisper       # Disable one

ods config show           # View .env (secrets masked)
ods preset save gaming    # Snapshot current config
ods preset load gaming    # Restore it
```

---

## How It Compares

ODS builds on tools you may already use rather than replacing them. It installs them, wires them together, and gives you one place to run them.

| If you use… | It gives you | ODS adds |
|---|---|---|
| **Ollama, llama.cpp or LM Studio** | Local model serving | Hardware-aware model choice, the surrounding services, and a dashboard to manage them |
| **Open WebUI** | A chat UI with RAG, voice and image-generation integrations | Installs and configures it next to local inference, speech, search and image services, and manages sign-in and network exposure |
| **LocalAI** | An OpenAI-compatible API with text, audio and image backends | A dashboard, the Portal agent, workflows, and a catalog of add-on apps around local inference |
| **n8n's self-hosted AI starter kit** | Workflow automation with a local model | Workflows as one part of a managed local AI server |

ODS's own focus is the layer around those tools: detecting your hardware, choosing and verifying models, lifecycle commands (install, update, backup, uninstall), and an extension catalog.

---

## Documentation

| | |
|---|---|
| [Quickstart](ods/QUICKSTART.md) | Step-by-step install guide with troubleshooting |
| [Docs Index](ods/docs/README.md) | Maintained map for operators, contributors, and reviewers |
| [Portal runtime](ods/docs/PIXEL.md) | Eligibility, licensing boundary, architecture, install, security, tools, rollback, and qualification |
| [Licensing](ods/LICENSING.md) | Apache-2.0 ODS code, Pixel's ODS-only grant, and third-party notices |
| [Build On ODS](ods/docs/BUILD-ON-ODS-SERVER.md) | Forking, custom editions, extension templates, and downstream validation |
| [Forkability](ods/docs/FORKABILITY.md) | How to fork, audit, customize, and independently operate ODS |
| [Maintainer Runbook](ods/docs/MAINTAINER_RUNBOOK.md) | Release, rollback, validation, and operator continuity guidance for maintainers and forks |
| [High-Risk Change Map](ods/docs/HIGH_RISK_CHANGE_MAP.md) | Which changes require focused checks, fleet validation, or release-grade gates |
| [Headless Setup](ods/docs/HEADLESS-SETUP.md) | QR onboarding, first-boot setup, AP mode, mDNS, and local agent access |
| [Support Matrix](ods/docs/SUPPORT-MATRIX.md) | Current platform and GPU support status |
| [Release Validation](ods/docs/RELEASE_VALIDATION.md) | User Green gates and the release-grade fleet/distro validation policy |
| [V3 Release Notes](ods/docs/RELEASE_NOTES_3.0.0.md) | Published V3 source identity and qualification boundaries |
| [2.6.0 Release Notes](ods/docs/RELEASE_NOTES_2.6.0.md) | Historical 2.6 release notes, validation receipt, and known validation boundaries |
| [Validation Matrix](ods/docs/VALIDATION-MATRIX.md) | Sanitized CI, distro lab, and real-hardware fleet release-readiness evidence |
| [Validation Reproducibility](ods/docs/VALIDATION_REPRODUCIBILITY.md) | How forks and operators can reproduce the validation story on their own hardware |
| [Offline And Mirroring](ods/docs/OFFLINE_AND_MIRRORING.md) | Pinning, mirroring, and preserving release artifacts for independent operation |
| [Installer Trust](ods/docs/INSTALLER_TRUST.md) | Inspect-first install paths, ref pinning, and current provenance limits |
| [Model Management](ods/docs/MODEL-MANAGEMENT.md) | Curated and Hugging Face GGUF discovery, verified imports, switching, and recovery |
| [Hardware Guide](ods/docs/HARDWARE-GUIDE.md) | What to buy, tier recommendations |
| [FAQ](ods/FAQ.md) | Common questions and configuration |
| [Extensions](ods/docs/EXTENSIONS.md) | How to add custom services |
| [Installer Architecture](ods/docs/INSTALLER-ARCHITECTURE.md) | Modular installer deep dive |
| [Installer Phase Contracts](ods/docs/INSTALLER_PHASE_CONTRACTS.md) | Phase ownership, idempotency, failure modes, and validation expectations |
| [Compose Resolver Contracts](ods/docs/COMPOSE_RESOLVER_CONTRACTS.md) | Rules for compose layers, extensions, backends, ports, and mode overlays |
| [Changelog](ods/CHANGELOG.md) | Version history and release notes |
| [Contributing](CONTRIBUTING.md) | How to contribute |

---

## Contributors And Recognition

ODS is built by a growing group of contributors across installers, GPU support, dashboard, security, extensions, docs, and release validation. The README keeps the product overview focused; the long-form credits, upstream acknowledgements, and contributor history live in [CONTRIBUTORS.md](CONTRIBUTORS.md).

ODS has been recognized by the local AI and developer community, including AMD Featured Developer recognition, selection as a May 2026 AMD Lemonade Developer Challenge winner, and a feature at [(Co)nnect: Philly's AI Ecosystem Summit](https://luma.com/xdwih64h) at Pennovation Works.

---

## License

ODS code is Apache-2.0 except the bundled Pixel source, which has a separate
ODS-only use and distribution grant. See [Licensing](ods/LICENSING.md),
[LICENSE](LICENSE), and [Pixel's license](ods/vendor/pixel/LICENSE.md).

---

<div align="center">

*Built by [Osmantic](https://github.com/Osmantic) and the growing resistance that refuses to rent what should be owned.*

</div>
