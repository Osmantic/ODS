# Dream Server Documentation Index

Links from this directory use `../` for the repo root (e.g. `../README.md`, `../QUICKSTART.md`) and bare filenames for other docs in this directory (e.g. `EXTENSIONS.md`, `TROUBLESHOOTING.md`). **FAQ:** `../FAQ.md` is the installation and usage FAQ at repo root; `FAQ.md` in this directory is the hardware and requirements FAQ.

## Getting Started

| Doc | Audience | Description |
|-----|----------|-------------|
| [HOW-DREAM-SERVER-WORKS.md](HOW-DREAM-SERVER-WORKS.md) | **Everyone** | **The friendly guide — what Dream Server is, why it exists, how every piece fits together, and how to make it your own. No technical background required.** |
| [../README.md](../README.md) | Everyone | Project overview, quickstart, architecture |
| [../QUICKSTART.md](../QUICKSTART.md) | Operators | Step-by-step first install |
| [HEADLESS-SETUP.md](HEADLESS-SETUP.md) | Operators / hardware builders | Hardware-neutral QR onboarding, first-boot setup, AP mode, mDNS, and local-agent access map |
| [../EDGE-QUICKSTART.md](../EDGE-QUICKSTART.md) | Operators | Edge devices (planned — do not follow yet; use cloud mode for CPU-only today) |
| [../.env.example](../.env.example) | Operators | All environment variables with defaults |

## Building & Extending

| Doc | Audience | Description |
|-----|----------|-------------|
| [EXTENSIONS.md](EXTENSIONS.md) | Builders | Add Docker services, manifests, dashboard plugins |
| [INSTALLER-ARCHITECTURE.md](INSTALLER-ARCHITECTURE.md) | Modders | Installer module map, mod recipes, header convention |
| [INTEGRATION-GUIDE.md](INTEGRATION-GUIDE.md) | Developers | Connect apps via OpenAI SDK, LangChain, n8n |
| [BACKEND-CONTRACT.md](BACKEND-CONTRACT.md) | Developers | Backend runtime contract JSON schema |
| [OPENCLAW-INTEGRATION.md](OPENCLAW-INTEGRATION.md) | Developers | OpenClaw agent framework setup |

## Hardware & Configuration

| Doc | Audience | Description |
|-----|----------|-------------|
| [HARDWARE-GUIDE.md](HARDWARE-GUIDE.md) | Buyers | GPU buying advice, tier recommendations |
| [HARDWARE-CLASSES.md](HARDWARE-CLASSES.md) | Developers | GPU-to-tier classification logic |
| [SUPPORT-MATRIX.md](SUPPORT-MATRIX.md) | Operators | Platform/GPU support status |
| [MODEL-MANAGEMENT.md](MODEL-MANAGEMENT.md) | Operators | Dashboard model downloads, switching, and manual GGUF workflows |
| [CAPABILITY-PROFILE.md](CAPABILITY-PROFILE.md) | Developers | Machine capability profiling schema |
| [MULTI-USER-SETUP.md](MULTI-USER-SETUP.md) | Operators | Expose and tune one install for multiple users |
| [PROFILES.md](PROFILES.md) | Reference | Docker Compose profiles (historical reference) |
| [MODE-SWITCH.md](MODE-SWITCH.md) | Operators | Cloud/local/hybrid deployment modes (planned) |
| [VLLM-SETUP.md](VLLM-SETUP.md) | Operators | Optional vLLM setup notes for high-concurrency NVIDIA inference |

## Troubleshooting

| Doc | Audience | Description |
|-----|----------|-------------|
| [../FAQ.md](../FAQ.md) | Everyone | Installation and usage FAQ |
| [FAQ.md](FAQ.md) | Everyone | Hardware and requirements FAQ |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Operators | Common issues and fixes |
| [INSTALL-TROUBLESHOOTING.md](INSTALL-TROUBLESHOOTING.md) | Operators | Installer-specific issues |
| [DREAM-DOCTOR.md](DREAM-DOCTOR.md) | Operators | Diagnostic tool usage |
| [PREFLIGHT-ENGINE.md](PREFLIGHT-ENGINE.md) | Developers | Preflight validation system |

## macOS

| Doc | Audience | Description |
|-----|----------|-------------|
| [MACOS-QUICKSTART.md](MACOS-QUICKSTART.md) | Operators | macOS Apple Silicon install guide |

## Windows

| Doc | Audience | Description |
|-----|----------|-------------|
| [WINDOWS-QUICKSTART.md](WINDOWS-QUICKSTART.md) | Operators | Windows install guide |
| [WINDOWS-INSTALL-WALKTHROUGH.md](WINDOWS-INSTALL-WALKTHROUGH.md) | Operators | Detailed Windows walkthrough |
| [WINDOWS-TROUBLESHOOTING-GUIDE.md](WINDOWS-TROUBLESHOOTING-GUIDE.md) | Operators | Windows-specific issues |
| [WSL2-GPU-PASSTHROUGH.md](WSL2-GPU-PASSTHROUGH.md) | Operators | WSL2 GPU setup |
| [WSL2-GPU-TROUBLESHOOTING.md](WSL2-GPU-TROUBLESHOOTING.md) | Operators | WSL2 GPU issues |
| [WINDOWS-WSL2-GPU-GUIDE.md](WINDOWS-WSL2-GPU-GUIDE.md) | Operators | Combined WSL2 GPU guide |
| [DOCKER-DESKTOP-OPTIMIZATION.md](DOCKER-DESKTOP-OPTIMIZATION.md) | Operators | Docker Desktop tuning |

## Operations

| Doc | Audience | Description |
|-----|----------|-------------|
| [M1-OFFLINE-MODE.md](M1-OFFLINE-MODE.md) | Operators | Air-gapped operation guide |
| [SETUP-CARD.md](SETUP-CARD.md) | Operators / hardware builders | Generate printable QR setup cards for headless devices |
| [POST-INSTALL-CHECKLIST.md](POST-INSTALL-CHECKLIST.md) | Operators | Post-install verification |
| [KNOWN-GOOD-VERSIONS.md](KNOWN-GOOD-VERSIONS.md) | Operators | Tested image/version combos |
| [PLATFORM-TRUTH-TABLE.md](PLATFORM-TRUTH-TABLE.md) | Developers | Platform feature matrix |

## Project

| Doc | Audience | Description |
|-----|----------|-------------|
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Contributors | How to contribute |
| [../SECURITY.md](../SECURITY.md) | Everyone | Security guide and disclosure |
| [../CHANGELOG.md](../CHANGELOG.md) | Everyone | Version history |
| [COMPOSABILITY-EXECUTION-BOARD.md](COMPOSABILITY-EXECUTION-BOARD.md) | Maintainers | Internal project tracking |
| [OSS-LAUNCH-CHECKLIST.md](OSS-LAUNCH-CHECKLIST.md) | Maintainers | Open-source launch tasks |
