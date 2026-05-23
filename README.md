<div align="center">

# Dream Server

### Own your AI. One person, one dream, one machine at a time.

A handful of companies control the vast majority of global AI traffic — and with it, your data, your costs, and your uptime. Every query you send to a centralized provider is business intelligence you don’t own, running on infrastructure you don’t control, priced on terms you can’t negotiate.

If AI is becoming critical infrastructure, it shouldn’t be rented. Self-hosting local AI should be a sovereign human right, not a career choice.

**Dream Server is the exit.** A local-first AI stack — LLM inference, chat, voice, agents, workflows, RAG, image generation, and privacy tools — deployed on your hardware with a single command. No cloud required. No subscriptions required. No one watching. Cloud and hybrid API modes are optional when you choose them.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/Light-Heart-Labs/DreamServer)](https://github.com/Light-Heart-Labs/DreamServer/stargazers)
[![Release](https://img.shields.io/github/v/release/Light-Heart-Labs/DreamServer)](https://github.com/Light-Heart-Labs/DreamServer/releases)

![Dream Server Dashboard](dream-server/docs/images/dashboard.png)

[![Watch the demo](https://img.shields.io/badge/Demo-Watch%20on%20YouTube-red?logo=youtube)](https://youtu.be/nO8xFNHX-HA)

**New here?** Read the [Friendly Guide](dream-server/docs/HOW-DREAM-SERVER-WORKS.md) or [listen to the audio version](https://open.spotify.com/episode/40MvqJ41bC8cEgvUyOyE3K) — a complete walkthrough of what Dream Server is, how it works, and how to make it your own. No technical background needed.

</div>

---

> **Current Platform Support**
>
> | Platform | Status |
> |----------|--------|
> | **Linux** (NVIDIA + AMD + Intel Arc) | **Supported** — install and run today |
> | **Windows** (NVIDIA + AMD) | **Supported** — install and run today |
> | **macOS** (Apple Silicon) | **Supported** — install and run today |
>
> **Tested Linux distros:** Ubuntu 24.04/22.04, Debian 12, Linux Mint 21.3, Fedora 41+, Rocky Linux 9, Arch Linux, Manjaro, CachyOS, and openSUSE Tumbleweed. Other distros using apt, dnf, pacman, or zypper should also work — [open an issue](https://github.com/Light-Heart-Labs/DreamServer/issues) if yours doesn't.
>
> **Testing surface:** CI and tower2 Docker containers cover broad distro installer logic; tower2 Incus VMs cover systemd + Docker daemon behavior on Ubuntu, Fedora/Rocky, Arch, and openSUSE; the real hardware fleet remains the release gate for NVIDIA/AMD/Apple GPU runtime, dashboard, Hermes, UI, and capability validation.
>
> **Windows:** Requires Docker Desktop with WSL2 backend. NVIDIA GPUs use Docker GPU passthrough; AMD Strix Halo runs through the platform-specific accelerated path documented in the Windows installer and support matrix.
>
> **macOS:** Requires Apple Silicon (M1+) and Docker Desktop. llama-server runs natively with Metal GPU acceleration; all other services run in Docker.
>
> See the [Support Matrix](dream-server/docs/SUPPORT-MATRIX.md) for supported
> platform claims and the [Validation Matrix](dream-server/docs/VALIDATION-MATRIX.md)
> for the layered test surface used to test those claims.

---

## Why Dream Server?

Because running your own AI shouldn't require a CS degree and a weekend of debugging CUDA drivers. Right now, setting up local AI means stitching together a dozen projects, writing Docker configs from scratch, and praying everything talks to each other. Most people give up and go back to paying OpenAI.

We built Dream Server so you don't have to.

- **One command** — detects your GPU, picks the right model, generates credentials, launches everything
- **Chatting in under 2 minutes** — bootstrap mode gives you a working model instantly while your full model downloads in the background
- **Full service stack, pre-wired** — chat, agents, voice, workflows, search, RAG, image generation, privacy tools, observability, and developer tools. All talking to each other out of the box
- **Fully moddable** — every service is an extension. Drop in a folder, run `dream enable`, done

```bash
curl -fsSL https://raw.githubusercontent.com/Light-Heart-Labs/DreamServer/main/dream-server/get-dream-server.sh | bash
```

Open **http://localhost:3000** and start chatting.

> **API endpoint:** Linux Docker installs expose llama-server on **http://localhost:11434** by default (`OLLAMA_PORT`) while containers use `llama-server:8080`. macOS native Metal and Windows native/Lemonade paths use **http://localhost:8080** unless overridden. Open WebUI stays on **http://localhost:3000**.

> **No GPU?** Dream Server also runs in cloud mode — same full stack, powered by OpenAI/Anthropic/Together APIs instead of local inference:
> ```bash
> ./install.sh --cloud
> ```

> **Port conflicts?** Every port is configurable via environment variables. See [`.env.example`](dream-server/.env.example) for the full list, or override at install time:
> ```bash
> WEBUI_PORT=9090 ./install.sh
> ```

<div align="center">

![Dream Server Installer](dream-server/docs/images/installer-splash.gif)

*The DREAMGATE installer handles everything — GPU detection, model selection, service orchestration.*

</div>

<details>
<summary><b>Manual install (Linux)</b></summary>

```bash
git clone https://github.com/Light-Heart-Labs/DreamServer.git
cd DreamServer/dream-server
./install.sh
```

</details>

<details>
<summary><b>Windows (PowerShell)</b></summary>

Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/) with WSL2 backend enabled.
**Install Docker Desktop first and make sure it is running before you start.**

Open a normal **PowerShell** session and run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
git clone https://github.com/Light-Heart-Labs/DreamServer.git
cd DreamServer
.\install.ps1
```

> The `Set-ExecutionPolicy` command allows the installer script to run in the current session. It does not change your system-wide policy.
> Running as Administrator is not recommended for the installer because user-level paths such as `.opencode`, `data/`, and `.env` can be created with admin-owned permissions.

The installer detects your GPU, picks the right model, generates credentials, starts all services, and creates a Desktop shortcut to the Dashboard. Manage with `.\dream-server\installers\windows\dream.ps1 status`.

</details>

<details>
<summary><b>macOS (Apple Silicon)</b></summary>

Requires Apple Silicon (M1+) and [Docker Desktop](https://www.docker.com/products/docker-desktop/).
**Install Docker Desktop first and make sure it is running before you start.**

```bash
git clone https://github.com/Light-Heart-Labs/DreamServer.git
cd DreamServer/dream-server
./install.sh
```

The installer detects your chip, picks the right model for your unified memory, launches llama-server natively with Metal acceleration, and starts all other services in Docker. Manage with `./dream-macos.sh status`.

See the [macOS Quickstart](dream-server/docs/MACOS-QUICKSTART.md) for details.

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
- **Hermes Agent** — default local-first autonomous/browser agent with memory, skills, and a magic-link-gated proxy
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

The installer detects your GPU and first assigns a deterministic hardware tier. Linux and macOS then run the versioned catalog selector (`dream-server/scripts/select-model.py`), while Windows uses the PowerShell catalog selector in `dream-server/installers/windows/lib/tier-map.ps1`; both read `dream-server/config/model-library.json` to choose the best installable GGUF for the detected memory envelope. The final choice is written to `.env` as `LLM_MODEL`, `GGUF_FILE`, `MAX_CONTEXT`, and `MODEL_RECOMMENDATION_*`.

`MODEL_PROFILE=qwen` is the default non-Gemma catalog profile, so the effective pick can be Qwen, Phi, or DeepSeek depending on what fits best. `MODEL_PROFILE=gemma4` forces Gemma 4 where available, and `MODEL_PROFILE=auto` uses Gemma 4 on NVIDIA, Apple Silicon, and Intel Arc tiers. Override tier selection with `./install.sh --tier 3`; override the model family with `MODEL_PROFILE=gemma4 ./install.sh` or `MODEL_PROFILE=auto ./install.sh`.

When Hermes is enabled, which is the default agent path, installers keep the first-run bootstrap model at a 64K context floor and promote the full local model context to 128K where the selected model supports it. That avoids Hermes's hard 64K minimum while preserving the under-2-minute first chat experience. The examples below are current catalog-selector outputs for common hardware envelopes; exact installs can differ with detected VRAM/RAM, host architecture, existing downloads, or explicit profile overrides. Throughput still needs a local benchmark after first launch.

### NVIDIA

| Tier / envelope | Current default catalog pick | Context | Example hardware |
|------|--------------|---------|--------------|
| 0 / 8 GB CPU fallback | Qwen3.5 2B (Q4_K_M) | 8K | Low-RAM CPU-only |
| 1 / 8 GB discrete VRAM | Qwen3.5 9B (Q4_K_M) | 32K | RTX 4060, RTX 3060 12GB |
| 2 / 12 GB discrete VRAM | Phi-4 14B (Q4_K_M) | 16K | RTX 4070-class cards |
| 3 / 24 GB discrete VRAM | Qwen3.5 27B (Q4_K_M) | 32K | RTX 4090, A6000 |
| 4 / 48 GB discrete VRAM | DeepSeek R1 Distill Llama 70B (Q4_K_M) | 32K | A6000 Ada, L40S |
| NV_ULTRA / 90+ GB amd64 discrete VRAM | Qwen3 Coder Next (Q4_K_M) | 128K | Multi-GPU A100/H100 |
| NV_ULTRA / 90+ GB arm64 unified memory | Qwen3.6 35B-A3B (UD-Q4_K_M) | 128K | DGX Spark / GB10-class hosts |

### AMD Strix Halo (Unified Memory)

| Tier / envelope | Current default catalog pick | Context | Hardware |
|------|--------------|---------|----------|
| SH_COMPACT / 64 GB unified RAM | Qwen3.6 35B-A3B (UD-Q4_K_M) | 128K | Ryzen AI MAX+ 395 (64GB) |
| SH_LARGE / 96 GB unified RAM | DeepSeek R1 Distill Llama 70B (Q4_K_M) | 32K | Ryzen AI MAX+ 395 (96GB) |
| SH_LARGE / 124 GB unified RAM | Qwen3.6 35B-A3B (UD-Q4_K_M) | 128K | Ryzen AI MAX+ 395 (128GB class) |

The selector routes unified-memory hosts away from Qwen3 Coder Next when that model would otherwise be selected, because current repo policy documents correctness issues on those backends.

### Apple Silicon (Unified Memory, Metal)

| Tier / envelope | Current default catalog pick | Context | Example hardware |
|------|--------------|---------|-----------------|
| 0 / 8 GB unified RAM | Phi-4 Mini (Q4_K_M) | 128K | M1/M2 base (8GB) |
| 1 / 16 GB unified RAM | Qwen3.5 9B (Q4_K_M) | 32K | M4 Mac Mini (16GB) |
| 2 / 32 GB unified RAM | Phi-4 14B (Q4_K_M) | 16K | M4 Pro Mac Mini, M3 Max MacBook Pro |
| 3 / 48 GB unified RAM | Qwen3.5 27B (Q4_K_M) | 32K | M4 Pro (48GB), M2 Max (48GB) |
| 4 / 64+ GB unified RAM | Qwen3.6 35B-A3B (UD-Q4_K_M) | 128K | M2 Ultra Mac Studio, M4 Max (64GB+) |

### Intel Arc (Linux, SYCL)

| Tier / envelope | Current default catalog pick | Context | Example hardware |
|------|--------------|---------|------------------|
| ARC_LITE / 6 GB discrete VRAM | Phi-4 Mini (Q4_K_M) | 128K | Arc A380 |
| ARC_LITE / 8 GB discrete VRAM | Qwen3.5 9B (Q4_K_M) | 32K | Arc A750 |
| ARC / 16 GB discrete VRAM | Phi-4 14B (Q4_K_M) | 16K | Arc A770 16GB, newer Arc GPUs |

Gemma 4 profile tiers remain in the installer tier maps: E2B on entry hardware, E4B on midrange hardware, 26B-A4B on pro hardware, and 31B on large/ultra hardware.

---

## Bootstrap Mode

No waiting for large downloads. Dream Server uses bootstrap mode by default:

1. Downloads a tiny 1.5B model in under a minute
2. You start chatting immediately
3. The full model downloads in the background
4. Hot-swap to the full model when it's ready — zero downtime

<div align="center">

![Installer downloading modules](dream-server/docs/images/installer-download.png)

*The installer pulls all services in parallel. Downloads are resume-capable — interrupted downloads pick up where they left off.*

</div>

The bootstrap model starts with a 64K context window so Hermes can work during the first session. After the background download finishes, Dream Server swaps to the full model and restores the Hermes/full-model context target.

Skip bootstrap: `./install.sh --no-bootstrap`

---

## Switching Models

The installer picks a model for your hardware, but you can switch anytime:

```bash
dream model current              # What's running now?
dream model list                 # Show all available tiers
dream model swap T3              # Switch to a different tier
```

If the new model isn't downloaded yet, pre-fetch it first:

```bash
./scripts/pre-download.sh --tier 3    # Download before switching
dream model swap T3                    # Then swap (restarts llama-server)
```

Already have a GGUF you want to use? Drop it in `data/models/`, update `GGUF_FILE` and `LLM_MODEL` in `.env`, and restart with the CLI:

```bash
dream restart llm
```

Or restart the container directly from the installed `dream-server` directory:

```bash
docker compose restart llama-server
```

Rollback is automatic — if a new model fails to load, Dream Server reverts to your previous model.

---

## Extensibility

Dream Server is designed to be modded. Every service is an extension — a folder with a `manifest.yaml` and a `compose.yaml`. The dashboard, CLI, health checks, and compose stack all discover extensions automatically.

```
extensions/services/
  my-service/
    manifest.yaml      # Metadata: name, port, health endpoint, GPU backends
    compose.yaml       # Docker Compose fragment (auto-merged into the stack)
```

```bash
dream enable my-service     # Enable it
dream disable my-service    # Disable it
dream list                  # See everything
```

The installer itself is modular — 19 library modules, a shared service registry, and 13 ordered phases. Want to add a hardware tier, swap a default model, or skip a phase? Start with the installer architecture map so you update the Linux, macOS, Windows, upgrade, and host-agent writers together.

[Full extension guide](dream-server/docs/EXTENSIONS.md) | [Installer architecture](dream-server/docs/INSTALLER-ARCHITECTURE.md)

---

## dream-cli

The `dream` CLI manages your entire stack:

```bash
dream status                # Health checks + GPU status
dream list                  # All services and their state
dream logs llm              # Tail logs (aliases: llm, stt, tts)
dream restart [service]     # Restart one or all services
dream start / stop          # Start or stop the stack

dream mode cloud            # Switch to cloud APIs via LiteLLM
dream mode local            # Switch back to local inference
dream mode hybrid           # Local primary, cloud fallback

dream model swap T3         # Switch to a different hardware tier
dream enable n8n            # Enable an extension
dream disable whisper       # Disable one

dream config show           # View .env (secrets masked)
dream preset save gaming    # Snapshot current config
dream preset load gaming    # Restore it
```

---

## How It Compares

Other tools get you part of the way. Dream Server gets you the whole way.

| | Dream Server | Ollama + Open WebUI | LocalAI |
|---|:---:|:---:|:---:|
| **Scope** | Full AI stack — inference to agents to workflows | LLM + chat | LLM only |
| One-command install | Everything, auto-configured | LLM + chat only | LLM only |
| Hardware auto-detect + model selection | NVIDIA + AMD Strix Halo + Apple Silicon + Intel Arc + CPU/cloud fallback | No | No |
| AMD APU unified memory support | Platform-specific accelerated backend, selected by installer | Partial (Vulkan) | No |
| Autonomous AI agents | Hermes Agent default; OpenClaw legacy opt-in | No | No |
| Workflow automation | n8n (400+ integrations) | No | No |
| Voice (STT + TTS) | Whisper + Kokoro | No | No |
| Image generation | ComfyUI | No | No |
| RAG pipeline | Qdrant + embeddings | No | No |
| Extension system | Manifest-based, hot-pluggable | No | No |
| Multi-GPU | Yes (NVIDIA) | Partial | Partial |

---

## Documentation

| | |
|---|---|
| [Quickstart](dream-server/QUICKSTART.md) | Step-by-step install guide with troubleshooting |
| [Docs Index](dream-server/docs/README.md) | Maintained map for operators, contributors, and reviewers |
| [Build On Dream Server](dream-server/docs/BUILD-ON-DREAM-SERVER.md) | Forking, custom editions, extension templates, and downstream validation |
| [Headless Setup](dream-server/docs/HEADLESS-SETUP.md) | QR onboarding, first-boot setup, AP mode, mDNS, and local agent access |
| [Support Matrix](dream-server/docs/SUPPORT-MATRIX.md) | Current platform and GPU support status |
| [Validation Matrix](dream-server/docs/VALIDATION-MATRIX.md) | Sanitized CI, distro lab, and real-hardware fleet release-readiness evidence |
| [Model Management](dream-server/docs/MODEL-MANAGEMENT.md) | Dashboard model downloads, switching, and manual GGUF workflows |
| [Hardware Guide](dream-server/docs/HARDWARE-GUIDE.md) | What to buy, tier recommendations |
| [FAQ](dream-server/FAQ.md) | Common questions and configuration |
| [Extensions](dream-server/docs/EXTENSIONS.md) | How to add custom services |
| [Installer Architecture](dream-server/docs/INSTALLER-ARCHITECTURE.md) | Modular installer deep dive |
| [Changelog](dream-server/CHANGELOG.md) | Version history and release notes |
| [Contributing](CONTRIBUTING.md) | How to contribute |

---

## Wall of Heroes

Dream Server exists because people chose to build instead of wait. Every contributor here is part of something bigger than code — a growing resistance against the idea that AI should be rented, gated, and controlled by the few. These are the founders of the sovereign AI movement, proving that one person, one machine, and one dream is enough.

### Recognition

Dream Server has been recognized by the local AI and developer community, including AMD Featured Developer recognition, selection as a May 2026 AMD Lemonade Developer Challenge winner, and a feature at [(Co)nnect: Philly's AI Ecosystem Summit](https://luma.com/xdwih64h) at Pennovation Works.

Thanks to [lhl](https://github.com/lhl) for [strix-halo-testing](https://github.com/lhl/strix-halo-testing) — the foundational Strix Halo AI research and rocWMMA performance work that the broader community builds on.

[Tony363 (Tony Siu)](https://github.com/Tony363), a former [Google University Research lead](https://technical.ly/workforce/tony-siu-code-and-coffee-how-i-got-here/) and founder of [Code & Coffee Philadelphia](https://www.meetup.com/coffee-code-philly/), has been one of the biggest reasons Dream Server reached broader awareness in the Philly AI ecosystem. Code & Coffee is now a 4,000+ developer community, and Tony's support helped bring Dream Server into that world, including multiple Pennovation Works features. More than any title, he cares deeply about local AI, empowerment for the masses, and seeing this project succeed. We are also eternally grateful to [Ahmad Osman](https://ahmadosman.com/about/) for [featuring and publicly supporting Dream Server](https://x.com/TheAhmadOsman/status/2055344995041771776?s=20); after his endorsement, the project grew from roughly 500 to nearly 1,500 GitHub stars in four days, bringing a wave of visibility, adoption, and encouragement we will not forget.

### Projects that make Dream Server possible

*   [llama.cpp (ggerganov)](https://github.com/ggml-org/llama.cpp) — LLM inference engine
*   [Qwen (Alibaba Cloud)](https://github.com/QwenLM/Qwen) — Default language models
*   [Open WebUI](https://github.com/open-webui/open-webui) — Chat interface
*   [ComfyUI](https://github.com/comfyanonymous/ComfyUI) — Image generation engine
*   [FLUX.1 (Black Forest Labs)](https://github.com/black-forest-labs/flux) — Image generation model
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

### The Resistance

*   [Yasin Bursali (yasinBursali)](https://github.com/yasinBursali) — DreamServer's most prolific contributor across 370+ merged PRs spanning nearly every layer of the stack. Built the extensions lifecycle system, compose security scanner, host-agent install/runtime flow, dashboard extension management, and broad extension-library hardening. Carried major portability, reliability, and safety work across macOS, Windows/WSL2, Apple Silicon, AMD/Lemonade, dream-cli, installers, dashboard-api, Privacy Shield, observability, model activation, and release-contract tests. His work turned DreamServer from a service bundle into a safer, extensible, heavily tested local AI platform.
*   [Youness Yachouti (y-coffee-dev)](https://github.com/y-coffee-dev) — Designed and built the full-stack multi-GPU system: NVIDIA topology detection, topology-aware service placement, docker-compose.multigpu.yml generation, dashboard GPU Monitor, and five `dream gpu` CLI subcommands with completions. Also fixed installer custom-mode opt-outs, added curl retries, longer llama-server startup grace, and phase-state markers that make long installs easier to resume and diagnose. Tested on real multi-GPU hardware including 4x RTX 4060 Ti, 4x RTX 4080, and 8x RTX 5060 Ti configurations.
*   [SSignall (Android Dev)](https://github.com/SSignall) — Added early ARM64 / NVIDIA Grace Blackwell support for DGX Spark-class systems: llama.cpp architecture mismatch detection, GB10/GB200 unified-memory fallback, docker-network service URL injection for diagnostics, setup wizard resilience when diagnostics fail, and QR helper fixes so terminal onboarding points at the actual dashboard URL.
*   [Tony363 (Tony Siu)](https://github.com/Tony363) — Raised dashboard-api test coverage to 95% with 3,500+ lines of tests across 14 files including comprehensive endpoint coverage for setup, privacy, workflows, updates, agents, and GPU monitoring, plus 7 BATS shell test suites covering logging, constants, path-utils, bootstrap-model, nvidia-topo, ui, and background-tasks. Added comprehensive architecture overview documentation (ARCHITECTURE.md) with Mermaid diagrams for service topology, installer pipeline, and compose layering. Fixed the pre-existing ThemeProvider CI failure that was blocking every PR frontend check. Reported the PyYAML import crash on Manjaro/Arch (resolve-compose-stack.sh) with clear root cause analysis. Drives developer outreach and ecosystem growth as head of Coffee and Code Philadelphia. Earlier work: hardened service-registry.sh against shell injection, improved PII scrubber with Luhn check, fixed token-spy settings persistence with atomic writes, fixed SSH command injection in session-manager.sh, narrowed broad exception catches across dashboard-api, and authored CLAUDE.md with project instructions and design philosophy. Built three AI-powered GitHub Actions workflows: consolidated code review with fork detection and protected file enforcement, label-gated issue-to-PR automation with 4-job pipeline (validate/implement/guardrails/create-pr) and prompt injection hardening (anti-injection preamble, 4000-char truncation, tool restrictions, secret scanning), and nightly AI scanners for code review/docs/autonomous scanning with budget caps and manual-only triggers. Fixed unified APU name fallback in GPU detection for Strix Halo. Prototyped a full Rust/Axum rewrite of the dashboard-api with 285 tests, constant-time auth middleware, 3-crate workspace, and ~25MB Docker image (work-in-progress — extension security features being ported). Fixed pipefail-safe hostname fallback in installer phase 13 for Arch/Manjaro compatibility
*   [latentcollapse (Matt C)](https://github.com/latentcollapse) — Security audit and hardening: OpenClaw localhost binding fix, multi-GPU VRAM detection, AMD dashboard hardening, and the Agent Policy Engine (APE) extension
*   [Nolan Makatche (nolanmak)](https://github.com/nolanmak) — Hardened Dream Server's agent and privacy surfaces across three security-focused PRs: added persistent APE windowed governance with human-approval escalation, restart-safe state, one-shot approval grants, circuit breaker protection, warmup handling, and a 44-test contract suite ([#1272](https://github.com/Light-Heart-Labs/DreamServer/pull/1272)); made OpenClaw device auth secure by default with explicit opt-in for dangerous disablement, removed the always-LAN unauthenticated gateway path, protected LAN-bound token exposure, and added device-auth regression coverage ([#1273](https://github.com/Light-Heart-Labs/DreamServer/pull/1273)); and rebuilt Privacy Shield response handling around streaming-safe PII restoration, binary/gzip passthrough, oversized-response fallback, authenticated WebSocket passthrough, and 50 regression tests covering SSE, token boundaries, non-UTF-8 bodies, and auth gates ([#1275](https://github.com/Light-Heart-Labs/DreamServer/pull/1275)).
*   [Igor Lins e Silva (igorls)](https://github.com/igorls) — Stability audit fixing 9 infrastructure bugs: dynamic compose discovery in backup/restore/update scripts, Token Spy persistent storage and connection pool hardening, dotglob rollback fix, systemd auto-resume service correction, removed auth gate from preflight ports endpoint for setup wizard compatibility, added ESLint flat config for the dashboard, cleaned up unused imports and linting across the Python codebase, and resolved CI failures across dashboard and smoke tests
*   [Nino Skopac (NinoSkopac)](https://github.com/NinoSkopac) — Token Spy dashboard improvements: shared metric normalization with parity tests, budget and active session tracking, configurable secure CORS replacing wildcard origins, and DB backend compatibility shim for sidecar migration
*   [Glexy (fullstackdev0110)](https://github.com/fullstackdev0110) — Hardened the installer and operations layer: safe `.env` loading across scripts, removal of unsafe `eval`, dream-cli status/config/mode commands, extension manifest compatibility validation, CPU-only support, Linux portability docs, structured Linux preflight diagnostics, extension runtime checks in install summary and CI, Windows compose failure reports, broader PowerShell lint coverage, and secret-redaction warnings in diagnostic output.
*   [bugman-007](https://github.com/bugman-007) — Parallelized health checks in dream status for 5–10× speedup using async gather with proper timeout handling, benchmark and test scripts, integrated backup/restore commands into dream-cli, added preset import/export with path traversal protection and archive validation, added preset diff command for comparing configurations with secret masking, quarantined broken edge quickstart instructions replacing them with supported cloud mode path, added SHA256 integrity manifests and verification for backups, added restore safety prompts requiring backup ID confirmation, added backup/restore round-trip integration test, added preset compatibility validation before load, added service registry tests to CI, added Python type checking with mypy, added disk space preflight checks to backup/restore with portable size estimation, and added session-level caching to compose flags resolution for performance, expanded dashboard-api test coverage for privacy, updates, and workflow endpoints, added structured logging to agent monitor replacing silent exception swallowing, added bash completion for dream-cli with dynamic backup ID resolution, added automatic pre-update backup with rollback command and health verification, fixed gitleaks CI to use OSS CLI instead of paid license action, added disk space preflight checks to backup/restore, and replaced disabled VAD patch with AST-based Python patcher for safe Whisper voice activity detection
*   [norfrt6-lab](https://github.com/norfrt6-lab) — Replaced 12+ silent exception-swallowing patterns with specific exception types and proper logging, added cross-platform system metrics (macOS/Windows) for uptime, CPU, and RAM, plus Apple Silicon GPU detection via sysctl/vm_stat
*   [boffin-dmytro](https://github.com/boffin-dmytro) — Hardened downloads, installers, and diagnostics: SHA256 GGUF verification, corrupt-file re-downloads, retrying network/Docker pulls, timeout hardening, port/Ollama preflight checks, compose validation, cross-platform path utilities, healthcheck resilience, manifest validation, doc-link CI, restore checksum verification, redacted support bundles, dashboard-api settings refactors, and setup/test coverage.

*   [takutakutakkun0420-hue](https://github.com/takutakutakkun0420-hue) — Added log rotation to all base services preventing unbounded disk growth, and added open-webui startup dependency on llama-server health ensuring the UI never shows a broken state

*   [reo0603](https://github.com/reo0603) — Fixed Makefile paths after dashboard-api move and heredoc quoting bug in session-manager.sh SSH command, narrowed broad exception catches to specific types across dashboard-api, parallelized health checks for 17× faster execution, added compose.local.yaml for dashboard/open-webui/privacy-shield service dependencies, added .dockerignore files to all custom Dockerfiles reducing build context, fixed H2C smuggling vector in nginx proxy and added wss:// for HTTPS in voice agent, added comprehensive extension integration and hardware compatibility test suites, and hardened secret management with .gitignore patterns for key/pem/credential files and SQL identifier validation in token-spy
*   [Arifuzzaman Joy (Arifuzzamanjoy)](https://github.com/Arifuzzamanjoy) — Pinned yq and docker-compose versions in the bootstrap Dockerfile replacing floating `/latest/` tags with reproducible ARG-based version pins, added Draft7Validator compatibility for jsonschema 3.x on Ubuntu 22.04/24.04, added compatibility blocks (dream_min version bounds) to 25 extension library manifests, added missing gpu_backends to 8 extension manifests, added cpu and none to the gpu_backends schema enum enabling CPU-only service declarations, fixed gpu_backends on 13+ extension manifests resolving schema validation failures, added missing required fields (icon, category, requirements, priority) to localai features, fixed env_vars schema compliance (name to key) in bark and rvc manifests, corrected privacy-shield service ID to match schema pattern, and fixed typo in baserow manifest tags
*   [nt1412](https://github.com/nt1412) — Wired dashboard-api agent metrics to Token Spy with background metrics collection, added TOKEN_SPY_URL/TOKEN_SPY_API_KEY env vars, fixed missing key_management.py in privacy-shield Dockerfile, and added ui_path to dashboard sidebar links so extension services open at their correct UI page
*   [evereq](https://github.com/evereq) — Relocated docs/images for cleaner monorepo root
*   [championVisionAI](https://github.com/championVisionAI) — Added Alpine Linux (apk) and Void Linux (xbps) package manager support to the installer abstraction layer, hardened hardware detection with JSON output escaping and container/WSL2 detection, rewrote healthcheck.py with retries, HEAD-to-GET fallback, status code matching, and structured JSON output, hardened Docker phase with daemon start/retry logic and compose v1/v2 detection, added cross-platform python3/python command resolution with shared detection utility, and hardened env schema validation with robust .env parsing, enum validation, and line-number error reporting, added sim summary validation test suite with 10 test cases covering help, missing files, invalid JSON, and strict mode, hardened hardware detection with JSON output escaping and container/WSL2 detection, hardened healthcheck.py with retries and HEAD-to-GET fallback, hardened Docker phase with daemon start/retry and compose v1/v2 detection, fixed Windows python3/python command resolution, added extension audit workflow with 838-line Python auditor and 'dream audit' CLI command, added duplicate key detection to env validation, added compact JSON output mode and --help flag to hardware detection, and failed env validation on duplicate keys preventing silent config corruption

*   [buddy0323](https://github.com/buddy0323) — Ported Windows installer phases 01-07 to native PowerShell decomposing the monolithic script into focused phase files, added Intel Arc SYCL tier map (ARC/ARC_LITE) with docker-compose.intel.yml overlay, detection logic, tier-map tests, and SHA256 verification, added Intel Arc oneAPI SYCL compose overlay with two-stage llama-sycl Dockerfile, added Intel Arc detection checks (lspci, Level Zero runtime, render nodes, group membership), and authored the Intel Arc support matrix documentation and setup guide
*   [blackeagle273](https://github.com/blackeagle273) — Enhanced macOS installer with idempotent .env and config generation preserving existing secrets across re-installs
*   [eva57gr](https://github.com/eva57gr) — Fixed bash syntax error in Token Spy session-manager.sh SSH heredoc command, and unified port contract across installer, schema, compose, and manifests with canonical ports.json registry
*   [cycloarcane](https://github.com/cycloarcane) — Fixed unbound variable crash by guarding service-registry.sh sourcing in install-core.sh, health-check.sh, and 04-requirements.sh
*   [Rowan (rowanbelanger713)](https://github.com/rowanbelanger713) — Enhanced llama-server with configurable batch-size, threads, and parallel request knobs, added TTL caching and async threading to dashboard-api status endpoints, pooled httpx connections for LiteLLM, lazy-loaded React routes with memoized components, scoped CSS transitions to interactive elements, paused polling on hidden tabs, and split Vite output into vendor/icons chunks for faster loading
*   [gabsprogrammer](https://github.com/gabsprogrammer) — Designed the dashboard's "liquid metal" refresh and kept expanding the operator experience: secure Settings env editor with apply-changes flow, service telemetry and restart controls, integrations topology view, service readiness summaries, compose failure reports, llama-server image tag validation, long-context/MoE tuning knobs, Android Termux and iOS a-Shell paths, Caddy image maintenance, README accuracy fixes, and Windows/installer stability patches.
*   [Octopus (octo-patch)](https://github.com/octo-patch) — Added MiniMax provider support for cloud and hybrid modes through LiteLLM, including `minimax` / `minimax-fast` model aliases, `MINIMAX_API_KEY` env/schema plumbing, Linux/macOS/Windows installer preservation, compose propagation, and user docs.
*   [onyxhat](https://github.com/onyxhat) — Fixed missing variable initialization in installer scripts
If we missed anyone, [open an issue](https://github.com/Light-Heart-Labs/DreamServer/issues). We want to get this right.

---

## License

Apache 2.0 — Use it, modify it, ship it. See [LICENSE](LICENSE).

---

<div align="center">

*Built by [Light Heart Labs](https://github.com/Light-Heart-Labs) and the growing resistance that refuses to rent what should be owned.*

</div>
