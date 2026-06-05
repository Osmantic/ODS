# Dream Server Validation Matrix

Last updated: 2026-05-25

This page describes the layered coverage used to validate Dream Server between
changes. It is intentionally sanitized: it publishes hardware classes,
operating systems, GPU paths, and test phases without private hostnames, LAN
addresses, usernames, or filesystem paths.

## Layered Test Surface

Dream Server uses four standing validation layers:

| Layer | Where it runs | Coverage | What it proves |
|---|---|---|---|
| CI matrix | GitHub Actions containers | Installer, shell, Python, PowerShell, dashboard, environment, and distro smoke checks | Fast pull-request safety for syntax, parsing, and cheap regressions |
| Zero-prereq bootstrap lab | Release harness distro containers | Bare Ubuntu, Debian, Fedora, Rocky, Arch, and openSUSE images with no assumed Git, jq, Python, Docker, or Compose | The public `curl` bootstrap path can provision its own prerequisites on clean Linux systems |
| Fleet distro lab | Private lab Docker containers + Incus VMs | 10 container distros plus VMs for Ubuntu, Fedora, Rocky, Arch, and openSUSE | Fast distro breadth plus real systemd, network, Docker daemon, Compose, and installer dry-run with Docker enabled |
| Real hardware fleet | Private local machines | Linux NVIDIA, Linux AMD, Linux ARM NVIDIA, macOS Apple Silicon, and optional Windows laptop targets | Fresh installs, GPU runtime, dashboard, Hermes, UI, lifecycle, Dream Talk, and agent capabilities |

Containers are breadth, not user-experience proof. Incus VMs add systemd,
kernel, and Docker-daemon realism, but they still do not prove GPU runtime.
Physical fleet machines remain the release gate for accelerator and product
behavior.

## Hardware Surface

| Test surface | OS family | Architecture | Accelerator path | Memory class | Fleet role |
|---|---|---:|---|---:|---|
| Linux NVIDIA workstation | Ubuntu 24.04 | x86_64 | High-memory CUDA GPUs | 90 GB+ VRAM per GPU | Primary CUDA install, dashboard, UI, and capability target |
| Linux AMD unified-memory workstation | Ubuntu 24.04 | x86_64 | AMD Strix Halo / ROCm-Lemonade path | 120 GB+ unified | Primary AMD install/runtime validation target |
| Linux NVIDIA unified-memory appliance | NVIDIA Ubuntu derivative | aarch64 | Grace Blackwell / CUDA path | 120 GB+ unified | ARM Linux + NVIDIA appliance validation target |
| macOS constrained Apple Silicon | macOS | arm64 | Native Metal inference + Docker services | 16 GB unified | Smoke gate and tight-memory macOS validation target |
| macOS high-memory Apple Silicon | macOS | arm64 | Native Metal inference + Docker services | 120 GB+ unified | Large-model macOS validation target |
| Windows hybrid GPU laptop | Windows 11 + Docker Desktop/WSL2 | x86_64 | NVIDIA mobile GPU plus Intel integrated GPU | 32 GB+ system RAM | Windows installer, Docker Desktop, WSL2, and mobile-GPU validation target when enabled for a run |

This standing hardware fleet is the repeatable release surface for GPU and
product behavior: it can run in parallel whenever installer, bootstrap,
dashboard, agent, model, or extension code changes. The CI matrix, zero-prereq
bootstrap lab, and distro lab add repeatable distro evidence between hardware
fleet runs. Community and volunteer testers add broader coverage on other GPUs,
distros, operating-system versions, storage layouts, and network environments,
but those reports are complementary evidence rather than the always-on release
gate.

## Fleet Phases

The private fleet harness runs these phases and records structured artifacts for
each host where the phase is applicable.

| Phase | What it proves | Normal cadence |
|---|---|---|
| Zero-prereq bootstrap | The public installer can bootstrap clean distro containers without preinstalled developer tools or Docker | Every release-grade run |
| Regression replay | Previously fixed fleet bugs have not returned | Every full fleet run |
| Smoke gate | A constrained Apple Silicon target can fresh-install and pass core health before the larger fleet starts | Every full fleet run |
| Preflight | OS, RAM, disk, Docker, firewall, port conflicts, prior install state | Every install run |
| Fresh install | The public bootstrap path can nuke prior state and install non-interactively | Every full fleet run |
| Core verify | Dashboard API, dashboard UI, llama-server models/chat, and Hermes proxy are reachable | Every post-install run |
| Cloud-mode contracts | Cloud and hybrid modes do not accidentally require local llama-server and still render required configs | Every release-grade run |
| Dashboard API flows | Model listing, model download/switch, and extension install state transitions | Every post-install run |
| Hermes auth/chat | Magic-link session auth, gated Hermes access, and seed echo through the agent path | Every post-install run |
| Browser UI | Dashboard navigation, model/extension surfaces, and Open WebUI model proxy behavior | Default UI target every run; scheduled wider UI sweeps |
| Capability probes | Chat coherence, web search, file write/read, code execution, skills list, loaded-model identity, context, and Dream Talk/owner-portal surfaces where enabled | Every post-install run, with LLM probes deferred while bootstrap is still active |
| Lifecycle | Idempotent reinstall, `dream restart`, and `dream doctor` after state changes | Every release-grade run; optional for lighter development runs |
| Release confidence report | The run is summarized into product, capability, lifecycle, and user-facing gates | Every release-grade run |

`--phase all` is intended for a faster full-product development sweep. The
private release-grade path adds zero-prereq bootstrap and lifecycle gates so a
green release run means more than "fresh install worked once."

Run the release-grade fleet after operational code changes: installer phases,
bootstrap, compose stack generation, service wiring, dashboard/API behavior,
Hermes, model routing, GPU detection, lifecycle commands, or anything else that
can affect a user's install or running stack. Docs-only and cosmetic changes can
usually rely on CI and focused documentation checks.

## Release Confidence Gates

Release-grade runs render a machine-readable confidence summary plus a human
report. The top-level gates are:

| Gate | Pass signal |
|---|---|
| Zero-prereq bootstrap | Bare distro containers can fetch the public installer and provision prerequisites |
| Install Green | Every enabled hardware host completed a fresh public-path install |
| Product Green | Core services, cloud contracts, dashboard flows, Hermes auth/chat, and UI checks passed |
| Capability Green | Full-model capability probes passed, or were explicitly deferred/skipped for documented reasons |
| Lifecycle Green | Idempotent reinstall, restart, and doctor checks passed on enabled hosts |
| User Green | The combined release-readiness gate: zero-prereq, install, product, capability, and lifecycle evidence are all clean or explicitly accounted for |

This is why a green release fleet pass is stronger than ordinary service
health. It exercises the user-visible setup path, first-run behavior,
post-install actions, real agent work, and lifecycle recovery after state has
changed.

## Evidence Receipts

Release notes should cite sanitized evidence from the current candidate, not a
private run directory. A useful receipt includes:

- the Dream Server commit, tag, or release candidate;
- the run date;
- sanitized hardware classes covered;
- distro lab breadth;
- regression replay result;
- install, verify, dashboard, Hermes, UI, capability, and lifecycle summaries;
- skipped, deferred, blocked-by-environment, or not-run phases;
- any known gaps that should not be read as supported behavior.

Private artifacts keep detailed logs, JSON events, and host-specific evidence
inside the lab. Public docs should quote the sanitized summary only, especially
when a run includes local hostnames, LAN addresses, usernames, or filesystem
paths.

Treat Windows evidence as release-relevant only when the Windows target produces
preflight, install, verify, dashboard, and UI artifacts for the candidate being
claimed. A supported platform can have installer and code support even when it
is not included in every default private release-fleet run; release notes should
say which hardware classes actually ran.

## What This Proves

- Installer OS and package-manager logic is exercised across the major Linux
  package-manager families.
- The public `curl` bootstrap path is tested from clean Linux images that do
  not assume developer tools or Docker are already present.
- Systemd, network, Docker daemon, Docker Compose, and installer dry-run
  behavior are exercised in disposable Incus VMs for the major Linux families.
- The installer is repeatedly exercised on real machines, not only CI
  containers and VMs.
- The release path covers heterogeneous GPU vendors, memory sizes, operating
  systems, and CPU architectures.
- The harness records environment state before install so firewall, Docker,
  disk, DNS, and port issues can be separated from product bugs.
- The user-facing path is tested beyond service liveness: dashboard actions,
  model switching, Hermes auth, agent capabilities, Dream Talk surfaces, and
  regression fixtures are part of the gate.
- Lifecycle behavior is part of release-grade confidence: reinstall, restart,
  and `dream doctor` must recover cleanly after state has changed.

## What This Does Not Claim

- Every Linux distribution is exhaustively installed on real hardware for every
  change. CI containers and the distro lab cover broad distro logic and
  systemd/Docker VM behavior; the physical fleet covers the high-value hardware
  paths.
- The Incus VM matrix is not GPU validation. GPU runtime claims require real
  NVIDIA, AMD, Intel, or Apple hardware evidence.
- OS and distro rotation is periodic because reprovisioning real machines is
  intentionally slower than running the standing fleet. Release notes should
  call out any rotated distro or OS image that was included for that candidate.
- Intel Arc is still experimental unless a release cites a successful Arc fleet
  run for that release candidate.
- Dream Talk, LAN, AP-mode, packaged appliance handoff, router, Wi-Fi, mDNS,
  and client-device behavior require target-mode validation because home
  networks vary.
- A fresh fleet pass is not a long-term soak test. Bench, thermal, and
  overnight stability runs are separate evidence.
- A green fleet pass is a strong release signal, not a promise that every
  unsupported driver, storage layout, firewall, network, or optional hardware
  combination will work perfectly.

## Release Readiness Receipt

Before a release is described as ready, the release notes should cite:

- the Dream Server version and matching Git tag or release;
- the fleet run date and sanitized hardware classes covered;
- regression replay result;
- zero-prereq bootstrap and distro-lab result summary;
- install/verify/dashboard/Hermes/UI/capability/lifecycle result summary;
- any skipped, deferred, blocked-by-environment, or not-run phases;
- known gaps that should not be read as supported behavior.

The version signal should be internally consistent before publication:
`manifest.json`, the changelog section, the Git tag/release, and any release
notes should all name the same version. If a candidate has not been tagged yet,
describe it as unreleased or release-candidate evidence rather than as a shipped
stable release.
