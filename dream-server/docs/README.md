# Dream Server Documentation Index

This is the maintained map for operators, contributors, and reviewers. Links from
this directory use `../` for the `dream-server/` product root and bare filenames
for other docs in this directory. The GitHub landing README lives two levels up
at [`../../README.md`](../../README.md).

**FAQ:** `../FAQ.md` is the installation and usage FAQ at the product root;
`FAQ.md` in this directory is the hardware and requirements FAQ.

## Start Here By Job

| I want to... | Read this first | Then use |
|--------------|-----------------|----------|
| Install the default path | [../QUICKSTART.md](../QUICKSTART.md) | [INSTALLER_TRUST.md](INSTALLER_TRUST.md), [SUPPORT-MATRIX.md](SUPPORT-MATRIX.md), [POST-INSTALL-CHECKLIST.md](POST-INSTALL-CHECKLIST.md) |
| Install on Windows | [WINDOWS-QUICKSTART.md](WINDOWS-QUICKSTART.md) | [WINDOWS-INSTALL-WALKTHROUGH.md](WINDOWS-INSTALL-WALKTHROUGH.md), [WINDOWS-WSL2-GPU-GUIDE.md](WINDOWS-WSL2-GPU-GUIDE.md) |
| Install on Apple Silicon | [MACOS-QUICKSTART.md](MACOS-QUICKSTART.md) | [MODEL-MANAGEMENT.md](MODEL-MANAGEMENT.md), [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |
| Debug a broken install | [DREAM-DOCTOR.md](DREAM-DOCTOR.md) | [INSTALL-TROUBLESHOOTING.md](INSTALL-TROUBLESHOOTING.md), [SUPPORT-BUNDLE.md](SUPPORT-BUNDLE.md) |
| Change installer behavior | [INSTALLER-ARCHITECTURE.md](INSTALLER-ARCHITECTURE.md) | [BACKEND-CONTRACT.md](BACKEND-CONTRACT.md), [PREFLIGHT-ENGINE.md](PREFLIGHT-ENGINE.md) |
| Change model routing | [MODEL-MANAGEMENT.md](MODEL-MANAGEMENT.md) | [MODE-SWITCH.md](MODE-SWITCH.md), [BACKEND-CONTRACT.md](BACKEND-CONTRACT.md) |
| Add or harden a service | [EXTENSIONS.md](EXTENSIONS.md) | [../extensions/CATALOG.md](../extensions/CATALOG.md), [../extensions/schema/README.md](../extensions/schema/README.md) |
| Build a custom edition or fork | [FORKABILITY.md](FORKABILITY.md) | [BUILD-ON-DREAM-SERVER.md](BUILD-ON-DREAM-SERVER.md), [OFFLINE_AND_MIRRORING.md](OFFLINE_AND_MIRRORING.md), [VALIDATION_REPRODUCIBILITY.md](VALIDATION_REPRODUCIBILITY.md) |
| Review a PR | [../CONTRIBUTING.md](../CONTRIBUTING.md) | [HIGH_RISK_CHANGE_MAP.md](HIGH_RISK_CHANGE_MAP.md), [TESTING.md](TESTING.md), [RELEASE_VALIDATION.md](RELEASE_VALIDATION.md), [VALIDATION-MATRIX.md](VALIDATION-MATRIX.md) |
| Maintain a release or fork | [MAINTAINER_RUNBOOK.md](MAINTAINER_RUNBOOK.md) | [HIGH_RISK_CHANGE_MAP.md](HIGH_RISK_CHANGE_MAP.md), [INSTALLER_PHASE_CONTRACTS.md](INSTALLER_PHASE_CONTRACTS.md), [COMPOSE_RESOLVER_CONTRACTS.md](COMPOSE_RESOLVER_CONTRACTS.md) |

## Current Truths

- The golden paths are Linux NVIDIA, Windows with Docker Desktop + WSL2 for
  NVIDIA/AMD, and Apple Silicon. Linux AMD Strix Halo is actively supported;
  Intel Arc is present but still experimental.
- The default agent path is Hermes Agent plus `hermes-proxy`. OpenClaw remains
  available for compatibility, but it is deprecated and no longer enabled by
  default.
- Linux Docker installs expose llama-server on host `OLLAMA_PORT=11434` by
  default while containers use `llama-server:8080`. macOS native Metal and
  Windows native/Lemonade paths use host port `8080` unless overridden.
- Windows installs should run from a normal user PowerShell, not Administrator.
  The default install directory is `$env:USERPROFILE\dream-server` unless
  `DREAM_HOME` is set.
- Bundled service truth lives in `extensions/services/*/manifest.yaml`.
  Core host-facing port defaults are tracked in `config/ports.json`; per-service
  manifest defaults live with each service. The dashboard extension library
  catalog is generated into `config/extensions-catalog.json`.
- Generated runtime config has several writers. If you change `.env`,
  OpenCode, Perplexica, Hermes, or LiteLLM/Lemonade behavior, update the Linux,
  macOS, Windows, bootstrap-upgrade, and host-agent paths together.

## Getting Started

| Doc | Audience | Description |
|-----|----------|-------------|
| [HOW-DREAM-SERVER-WORKS.md](HOW-DREAM-SERVER-WORKS.md) | **Everyone** | **The friendly guide — what Dream Server is, why it exists, how every piece fits together, and how to make it your own. No technical background required.** |
| [../../README.md](../../README.md) | Everyone | GitHub landing page and public project overview |
| [../README.md](../README.md) | Everyone | Product README, quickstart, architecture, and operator overview |
| [../QUICKSTART.md](../QUICKSTART.md) | Operators | Step-by-step first install |
| [INSTALLER_TRUST.md](INSTALLER_TRUST.md) | Operators / reviewers | Inspect-first install paths, release ref pinning, and current provenance limits |
| [HEADLESS-SETUP.md](HEADLESS-SETUP.md) | Operators / hardware builders | Hardware-neutral QR onboarding, first-boot setup, AP mode, mDNS, and local-agent access map |
| [../EDGE-QUICKSTART.md](../EDGE-QUICKSTART.md) | Operators | Edge devices (planned — do not follow yet; use cloud mode for CPU-only today) |
| [../.env.example](../.env.example) | Operators | All environment variables with defaults |

## Building & Extending

| Doc | Audience | Description |
|-----|----------|-------------|
| [BUILD-ON-DREAM-SERVER.md](BUILD-ON-DREAM-SERVER.md) | Downstream builders | Forking, custom editions, source-of-truth map, extension compatibility, and validation checklist |
| [FORKABILITY.md](FORKABILITY.md) | Downstream builders / fork operators | Fork posture, independent operation, safe extension points, and upstream relationship |
| [OFFLINE_AND_MIRRORING.md](OFFLINE_AND_MIRRORING.md) | Fork operators / appliance builders | Pinning, mirroring, and preserving release artifacts for offline or independent operation |
| [VALIDATION_REPRODUCIBILITY.md](VALIDATION_REPRODUCIBILITY.md) | Fork operators / release reviewers | How to reproduce upstream validation layers on local hardware and record receipts |
| [EXTENSIONS.md](EXTENSIONS.md) | Builders | Add Docker services, manifests, dashboard plugins |
| [../extensions/templates/README.md](../extensions/templates/README.md) | Builders | Starter manifest, compose, GPU overlay, and dashboard plugin templates |
| [../extensions/CATALOG.md](../extensions/CATALOG.md) | Builders / reviewers | Current bundled service manifest catalog |
| [INSTALLER-ARCHITECTURE.md](INSTALLER-ARCHITECTURE.md) | Modders | Installer module map, mod recipes, header convention |
| [INTEGRATION-GUIDE.md](INTEGRATION-GUIDE.md) | Developers | Connect apps via OpenAI SDK, LangChain, n8n |
| [BACKEND-CONTRACT.md](BACKEND-CONTRACT.md) | Developers | Backend runtime contract JSON schema |
| [INSTALLER_PHASE_CONTRACTS.md](INSTALLER_PHASE_CONTRACTS.md) | Maintainers / installer reviewers | Phase ownership, inputs, outputs, idempotency, and validation expectations |
| [COMPOSE_RESOLVER_CONTRACTS.md](COMPOSE_RESOLVER_CONTRACTS.md) | Maintainers / backend reviewers | Compose layer rules for services, hardware overlays, modes, dependencies, and ports |
| [HERMES.md](HERMES.md) | Developers / operators | Default Hermes Agent packaging, security posture, and operations |
| [OPENCLAW-INTEGRATION.md](OPENCLAW-INTEGRATION.md) | Developers | Deprecated OpenClaw setup and migration reference |

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
| [SUPPORT-BUNDLE.md](SUPPORT-BUNDLE.md) | Operators | What to collect before asking for help |
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
| [RELEASE_VALIDATION.md](RELEASE_VALIDATION.md) | Operators / release reviewers | User Green gates and when operational changes require release-grade fleet validation |
| [VALIDATION-MATRIX.md](VALIDATION-MATRIX.md) | Operators / release reviewers | Sanitized CI, distro lab, and real-hardware fleet release-readiness evidence |
| [HIGH_RISK_CHANGE_MAP.md](HIGH_RISK_CHANGE_MAP.md) | Contributors / maintainers | Risk levels and required validation by changed surface |

## Project

| Doc | Audience | Description |
|-----|----------|-------------|
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Contributors | How to contribute |
| [MAINTAINER_RUNBOOK.md](MAINTAINER_RUNBOOK.md) | Maintainers / fork operators | Release, rollback, validation, and handoff runbook |
| [../SECURITY.md](../SECURITY.md) | Everyone | Security guide and disclosure |
| [../../SECURITY_AUDIT.md](../../SECURITY_AUDIT.md) | Maintainers / reviewers | Historical security audit with current remediation status and receipts |
| [../CHANGELOG.md](../CHANGELOG.md) | Everyone | Version history |
| [COMPOSABILITY-EXECUTION-BOARD.md](COMPOSABILITY-EXECUTION-BOARD.md) | Maintainers | Internal project tracking |
| [OSS-LAUNCH-CHECKLIST.md](OSS-LAUNCH-CHECKLIST.md) | Maintainers | Open-source launch tasks |
