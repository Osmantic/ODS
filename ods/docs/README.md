# ODS Documentation Index

This is the maintained map for ODS, the Osmantic Deployment System. It is for
operators, contributors, and reviewers. Links from this directory use `../` for
the `ods/` product root and bare filenames
for other docs in this directory. The GitHub landing README lives two levels up
at [`../../README.md`](../../README.md).

**FAQ:** `../FAQ.md` is the installation and usage FAQ at the product root;
`FAQ.md` in this directory is the hardware and requirements FAQ.

## Start Here By Job

Use this table as the "you are here" map. ODS has many deep-dive
docs because the project covers install, compose, model routing, agents,
security, and release validation. Most contributors only need the row that
matches the work in front of them.

| I want to... | Read this first | Then use |
|--------------|-----------------|----------|
| Install the default path | [../QUICKSTART.md](../QUICKSTART.md) | [INSTALLER_TRUST.md](INSTALLER_TRUST.md), [SUPPORT-MATRIX.md](SUPPORT-MATRIX.md), [POST-INSTALL-CHECKLIST.md](POST-INSTALL-CHECKLIST.md) |
| Install on Windows | [WINDOWS-QUICKSTART.md](WINDOWS-QUICKSTART.md) | [WINDOWS-INSTALL-WALKTHROUGH.md](WINDOWS-INSTALL-WALKTHROUGH.md), [WINDOWS-WSL2-GPU-GUIDE.md](WINDOWS-WSL2-GPU-GUIDE.md) |
| Install on Apple Silicon | [MACOS-QUICKSTART.md](MACOS-QUICKSTART.md) | [MODEL-MANAGEMENT.md](MODEL-MANAGEMENT.md), [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |
| Debug a broken install | [ODS-DOCTOR.md](ODS-DOCTOR.md) | [INSTALL-TROUBLESHOOTING.md](INSTALL-TROUBLESHOOTING.md), [SUPPORT-BUNDLE.md](SUPPORT-BUNDLE.md) |
| Change installer behavior | [INSTALLER-ARCHITECTURE.md](INSTALLER-ARCHITECTURE.md) | [BACKEND-CONTRACT.md](BACKEND-CONTRACT.md), [PREFLIGHT-ENGINE.md](PREFLIGHT-ENGINE.md) |
| Change model routing | [MODEL-MANAGEMENT.md](MODEL-MANAGEMENT.md) | [MODE-SWITCH.md](MODE-SWITCH.md), [ENGINE-PROVIDER-MODES.md](ENGINE-PROVIDER-MODES.md), [BACKEND-CONTRACT.md](BACKEND-CONTRACT.md) |
| Add or harden a service | [EXTENSIONS.md](EXTENSIONS.md) | [../extensions/CATALOG.md](../extensions/CATALOG.md), [../extensions/schema/README.md](../extensions/schema/README.md) |
| Add an app that uses the local LLM | [SWAP-SAFE-EXTENSIONS.md](SWAP-SAFE-EXTENSIONS.md) | [EXTENSIONS.md](EXTENSIONS.md), [MODEL-MANAGEMENT.md](MODEL-MANAGEMENT.md), [RELEASE_VALIDATION.md](RELEASE_VALIDATION.md) |
| Build a custom edition or fork | [FORKABILITY.md](FORKABILITY.md) | [RELEASE_CHANNELS.md](RELEASE_CHANNELS.md), [BUILD-ON-ODS-SERVER.md](BUILD-ON-ODS-SERVER.md), [OFFLINE_AND_MIRRORING.md](OFFLINE_AND_MIRRORING.md), [VALIDATION_REPRODUCIBILITY.md](VALIDATION_REPRODUCIBILITY.md) |
| Review a PR | [../CONTRIBUTING.md](../CONTRIBUTING.md) | [HIGH_RISK_CHANGE_MAP.md](HIGH_RISK_CHANGE_MAP.md), [TESTING.md](TESTING.md), [RELEASE_VALIDATION.md](RELEASE_VALIDATION.md), [VALIDATION-MATRIX.md](VALIDATION-MATRIX.md) |
| Maintain a release or fork | [MAINTAINER_RUNBOOK.md](MAINTAINER_RUNBOOK.md) | [HIGH_RISK_CHANGE_MAP.md](HIGH_RISK_CHANGE_MAP.md), [INSTALLER_PHASE_CONTRACTS.md](INSTALLER_PHASE_CONTRACTS.md), [COMPOSE_RESOLVER_CONTRACTS.md](COMPOSE_RESOLVER_CONTRACTS.md), [BRANCH_HYGIENE.md](BRANCH_HYGIENE.md) |
| Review automation guardrails | [AI_WORKFLOW_GUARDRAILS.md](AI_WORKFLOW_GUARDRAILS.md) | [../CONTRIBUTING.md](../CONTRIBUTING.md), [HIGH_RISK_CHANGE_MAP.md](HIGH_RISK_CHANGE_MAP.md) |

## Choosing Validation

The shortest useful rule is: docs-only changes get docs checks; operational
changes get the validation surface they can affect.

| Change type | Start with | Escalate when |
|-------------|------------|---------------|
| Docs, comments, examples | `git diff --check`, markdown/link sanity | The docs change makes or changes a support claim |
| UI-only dashboard work | Dashboard tests, lint, and build | It changes setup, auth, service control, or model workflows |
| Service manifest or extension metadata | Extension audit and catalog validation | It changes compose, ports, health, dependencies, or defaults |
| Installer, compose, CLI, auth, proxy, model routing | Focused tests plus release-grade validation | Always consider this operational code |
| Dependency/runtime wiring | Package tests and service smoke | The package affects installer, dashboard-api, services, or container startup |

Use [HIGH_RISK_CHANGE_MAP.md](HIGH_RISK_CHANGE_MAP.md) for the full policy and
[RELEASE_VALIDATION.md](RELEASE_VALIDATION.md) for the User Green release gate.

## Document Status

Some docs are canonical contracts; others are operator guides, implementation
deep dives, or historical/planned notes. When docs overlap, prefer the
canonical source and treat older recipes as context.

| Status | Meaning | Examples |
|--------|---------|----------|
| Canonical contract | Defines behavior reviewers should enforce | [HIGH_RISK_CHANGE_MAP.md](HIGH_RISK_CHANGE_MAP.md), [INSTALLER_PHASE_CONTRACTS.md](INSTALLER_PHASE_CONTRACTS.md), [COMPOSE_RESOLVER_CONTRACTS.md](COMPOSE_RESOLVER_CONTRACTS.md), [ENGINE-PROVIDER-MODES.md](ENGINE-PROVIDER-MODES.md), [RELEASE_VALIDATION.md](RELEASE_VALIDATION.md) |
| Operator guide | Helps users install, operate, or troubleshoot | [../QUICKSTART.md](../QUICKSTART.md), [ODS-DOCTOR.md](ODS-DOCTOR.md), [TROUBLESHOOTING.md](TROUBLESHOOTING.md), [POST-INSTALL-CHECKLIST.md](POST-INSTALL-CHECKLIST.md) |
| Maintainer runbook | Explains how to preserve or release the project | [MAINTAINER_RUNBOOK.md](MAINTAINER_RUNBOOK.md), [FORKABILITY.md](FORKABILITY.md), [OFFLINE_AND_MIRRORING.md](OFFLINE_AND_MIRRORING.md), [VALIDATION_REPRODUCIBILITY.md](VALIDATION_REPRODUCIBILITY.md) |
| Deep dive | Explains a subsystem or design area | [INSTALLER-ARCHITECTURE.md](INSTALLER-ARCHITECTURE.md), [BACKEND-CONTRACT.md](BACKEND-CONTRACT.md), [ODS_CLI_DECOMPOSITION.md](ODS_CLI_DECOMPOSITION.md) |
| Historical or planned | Records a past migration, launch checklist, or future direction | [PROFILES.md](PROFILES.md), [OSS-LAUNCH-CHECKLIST.md](OSS-LAUNCH-CHECKLIST.md), [SERVICE_MANIFEST_V2_PLAN.md](SERVICE_MANIFEST_V2_PLAN.md) |

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
  The default install directory is `$env:USERPROFILE\ods` unless
  `ODS_HOME` is set.
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
| [HOW-ODS-SERVER-WORKS.md](HOW-ODS-SERVER-WORKS.md) | **Everyone** | **The friendly guide — what ODS is, why it exists, how every piece fits together, and how to make it your own. No technical background required.** |
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
| [BUILD-ON-ODS-SERVER.md](BUILD-ON-ODS-SERVER.md) | Downstream builders | Forking, custom editions, source-of-truth map, extension compatibility, and validation checklist |
| [FORKABILITY.md](FORKABILITY.md) | Downstream builders / fork operators | Fork posture, independent operation, safe extension points, and upstream relationship |
| [RELEASE_CHANNELS.md](RELEASE_CHANNELS.md) | Downstream builders / operators | When to track `main`, pin a tag, pin a commit, or operate a downstream fork |
| [OFFLINE_AND_MIRRORING.md](OFFLINE_AND_MIRRORING.md) | Fork operators / appliance builders | Pinning, mirroring, and preserving release artifacts for offline or independent operation |
| [VALIDATION_REPRODUCIBILITY.md](VALIDATION_REPRODUCIBILITY.md) | Fork operators / release reviewers | How to reproduce upstream validation layers on local hardware and record receipts |
| [EXTENSIONS.md](EXTENSIONS.md) | Builders | Add Docker services, manifests, dashboard plugins |
| [SWAP-SAFE-EXTENSIONS.md](SWAP-SAFE-EXTENSIONS.md) | Builders / reviewers | Make LLM-consuming extensions model-swap-safe with the gateway alias and `service.llm` contract |
| [../extensions/templates/README.md](../extensions/templates/README.md) | Builders | Starter manifest, compose, GPU overlay, and dashboard plugin templates |
| [../extensions/CATALOG.md](../extensions/CATALOG.md) | Builders / reviewers | Current bundled service manifest catalog |
| [SERVICE_MANIFEST_V2_PLAN.md](SERVICE_MANIFEST_V2_PLAN.md) | Maintainers / extension reviewers | Non-breaking plan for future manifest schema evolution |
| [INSTALLER-ARCHITECTURE.md](INSTALLER-ARCHITECTURE.md) | Modders | Installer module map, mod recipes, header convention |
| [ODS_CLI_DECOMPOSITION.md](ODS_CLI_DECOMPOSITION.md) | Maintainers / CLI contributors | Behavior-preserving plan for splitting the large Bash operator CLI without a risky rewrite |
| [INTEGRATION-GUIDE.md](INTEGRATION-GUIDE.md) | Developers | Connect apps via OpenAI SDK, LangChain, n8n |
| [BACKEND-CONTRACT.md](BACKEND-CONTRACT.md) | Developers | Backend runtime contract JSON schema |
| [ENGINE-PROVIDER-MODES.md](ENGINE-PROVIDER-MODES.md) | Maintainers / backend reviewers | Provider mode contract for local, cloud, hybrid, and Lemonade-backed installs |
| [INSTALLER_PHASE_CONTRACTS.md](INSTALLER_PHASE_CONTRACTS.md) | Maintainers / installer reviewers | Phase ownership, inputs, outputs, idempotency, and validation expectations |
| [COMPOSE_RESOLVER_CONTRACTS.md](COMPOSE_RESOLVER_CONTRACTS.md) | Maintainers / backend reviewers | Compose layer rules for services, hardware overlays, modes, dependencies, and ports |
| [HERMES.md](HERMES.md) | Developers / operators | Default Hermes Agent packaging, security posture, and operations |
| [OAUTH_PROVIDER_SETUP.md](OAUTH_PROVIDER_SETUP.md) | Operators / maintainers | OAuth provider registry, private credential bundles, and BYOC setup |
| [OPENCLAW-INTEGRATION.md](OPENCLAW-INTEGRATION.md) | Developers | Deprecated OpenClaw setup and migration reference |

## Hardware & Configuration

| Doc | Audience | Description |
|-----|----------|-------------|
| [HARDWARE-GUIDE.md](HARDWARE-GUIDE.md) | Buyers | GPU buying advice, tier recommendations |
| [HARDWARE-CLASSES.md](HARDWARE-CLASSES.md) | Developers | GPU-to-tier classification logic |
| [SUPPORT-MATRIX.md](SUPPORT-MATRIX.md) | Operators | Platform/GPU support status |
| [MODEL-MANAGEMENT.md](MODEL-MANAGEMENT.md) | Operators | Curated and Hugging Face GGUF discovery, verified imports, switching, and recovery |
| [MODEL-SWITCHBOARD.md](MODEL-SWITCHBOARD.md) | Contributors | Model Switchboard architecture, PR series plan, state/route contracts, and rollout gates |
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
| [ODS-DOCTOR.md](ODS-DOCTOR.md) | Operators | Diagnostic tool usage |
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
| [MAINTAINER_RUNBOOK.md](MAINTAINER_RUNBOOK.md) | Maintainers / fork operators | Release, rollback, validation, and operator continuity runbook |
| [AI_WORKFLOW_GUARDRAILS.md](AI_WORKFLOW_GUARDRAILS.md) | Maintainers / reviewers | Safety model for AI-assisted GitHub workflows, protected paths, and human review boundaries |
| [BRANCH_HYGIENE.md](BRANCH_HYGIENE.md) | Maintainers | Branch naming, stale branch dry-run audits, and cleanup policy |
| [../SECURITY.md](../SECURITY.md) | Everyone | Security guide and disclosure |
| [../../SECURITY_AUDIT.md](../../SECURITY_AUDIT.md) | Maintainers / reviewers | Historical security audit with current remediation status and receipts |
| [../CHANGELOG.md](../CHANGELOG.md) | Everyone | Version history |
| [COMPOSABILITY-EXECUTION-BOARD.md](COMPOSABILITY-EXECUTION-BOARD.md) | Maintainers | Internal project tracking |
| [OSS-LAUNCH-CHECKLIST.md](OSS-LAUNCH-CHECKLIST.md) | Maintainers | Open-source launch tasks |
