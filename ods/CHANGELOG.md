# Changelog

All notable changes to ODS will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed
- Dashboard Hermes readiness now accepts either llama-server or LiteLLM and
  requires the complete authenticated runtime chain. Hermes Single Sign-On now
  opens owner and support access management instead of duplicating the Agent
  runtime link.

## [2.5.3] - 2026-05-26

### Fixed
- Owner-card readiness now notices `ods-proxy` after `ods enable
  ods-proxy` and `ods start ods-proxy` without requiring a manual
  `dashboard-api` restart.

### Validation
- Fleet test run on 2026-05-26 at commit `cff3b21` passed regressions,
  zero-prereq bootstrap, installs, verify, cloud-mode, dashboard, Hermes, UI,
  lifecycle, and distro lab validation across Linux NVIDIA, AMD Strix Halo,
  Linux ARM NVIDIA, and Apple Silicon targets.
- The new `ods-proxy-owner-card-readiness-1474` regression fixture passed on
  Strix Halo from both already-enabled and disabled states, proving owner-card
  status returns `ready: true` without restarting `dashboard-api`.
- Capability reruns confirmed initial AMD Strix Halo and high-memory Apple
  Silicon failures were model/timing flakes; full-model capability probes passed
  on those targets while Linux NVIDIA and Linux ARM NVIDIA targets correctly
  deferred on bootstrap models.
- Distro lab passed 10/10 Docker lanes and 5/5 Incus VM lanes.

## [2.5.2] - 2026-05-26

### Fixed
- Dashboard nginx now re-resolves the `dashboard-api` service through Docker
  DNS at request time so lifecycle recreation cannot leave `/api/*` and ODS
  Talk routes pinned to a stale container IP.
- Discrete NVIDIA GPUs with less than 4GB VRAM now route to the CPU/Tier 0
  fallback by default instead of entering a green install with a crash-looping
  CUDA `llama-server`.

### Validation
- Fleet test run on 2026-05-26 at commit `c1df395` passed User Green: true
  fresh install, product, full-model capabilities, lifecycle, and UI validation
  across Linux NVIDIA, AMD Strix Halo, Linux ARM NVIDIA, and Apple Silicon
  targets.
- Full-model capability probes passed on all 4 enabled hosts, including chat,
  search, files, code, 76 Hermes skills, ODS Talk SSE streaming, session
  pooling, SOUL.md context, and install-context grounding.
- Distro lab passed 10/10 Docker lanes and 5/5 Incus VM lanes, and all 14
  prior regression fixtures stayed green.

## [2.5.1] - 2026-05-26

### Added
- ODS Talk owner-portal work for mobile use: local owner-card routing,
  streamed SSE replies, live status frames, TTS streaming, paperclip image/file
  attachments, and install-context grounding so the agent can describe the
  services actually running on a node.
- OAuth browser-redirect passthrough and provider-readiness metadata so the
  agent and dashboard can guide provider setup without guessing.
- Evidence-based `ods doctor` install and inference diagnostics, including
  local/cloud routing checks and clearer remediation messages.
- External AMD Lemonade SDK runtime support and an experimental AMD GAIA recipe
  for operators testing alternate AMD paths.
- Forkability, installer trust, release-channel, AI-contribution, branch
  hygiene, and CLI-roadmap documentation for downstream operators.

### Changed
- Moved long contributor credits out of the README and tightened README
  positioning so first-time operators see the product path faster.
- Expanded release validation entrypoints, validation gates, static contracts,
  and distro-lab locking so fleet, Docker, and Incus runs are less likely to
  contend with each other on the same host.
- Updated dashboard developer dependencies and grouped Dependabot updates after
  audit review.

### Fixed
- Bootstrap full-model downloads now preserve partial `.part` files, retry with
  resume support, keep failed status counters populated, cap progress display at
  100%, and recover cleanly on the next `ods start`, `ods restart`, or
  reinstall.
- Hermes local-provider calls now set a longer request timeout for slow
  time-to-first-token backends, and slash-worker guardrails prevent repeated
  agent sessions from accumulating runaway workers.
- Linux cloud installs no longer launch or health-gate on local `llama-server`;
  the compose resolver selects a cloud overlay, skips local-mode dependency
  overlays, and keeps Hermes SOUL persona generation outside the local-model
  path.
- Lifecycle, reinstall, and bootstrap-model paths were hardened across compose
  health waits, delayed port reuse, model-swap container recreation, stale cloud
  compose-cache invalidation, bundled service CPU limits, and fallback model
  serving when compose flags are missing.
- Installer portability fixes for Fedora/RHEL, openSUSE bootstrap detection,
  Python prerequisite setup, PATH-installed OpenCode, macOS launchd services,
  Windows compose working directories, and Docker-cloud install paths.
- Dashboard feature-card and LAN web guidance now point users at the intended
  proxy surfaces instead of raw API ports or misplaced homepage banners.
- Extension/security regressions fixed for trusted `extra_hosts`, Gaia data
  ownership, Hermes data ownership on reinstall, and inherited file descriptors
  in bootstrap upgrade workers.

### Security
- Pinned remaining GitHub Actions, added a root security policy/repo map, and
  strengthened desktop installer guardrails and installer trust documentation.
- Hardened OAuth pending-state handling, dashboard feature-card links, network
  exposure checks, and static audit contracts.

### Validation
- Fleet test run 11 on 2026-05-26 passed true fresh install, lifecycle, product,
  and core capability validation on Linux NVIDIA, AMD Strix Halo, Linux ARM
  NVIDIA, and Apple Silicon targets after Docker images, volumes, build cache,
  and stale model files were removed.
- Full target-model core capabilities passed on all 4 hardware platforms; ODS
  Talk capability probes passed where the Talk surface was enabled, with
  unavailable Talk surfaces correctly skipped.
- Distro lab passed 10/10 Docker lanes and 5/5 Incus VM lanes.
- Session total: 11 fleet runs, 35+ commits, 7 issues filed, 6 resolved, 4
  harness improvements, and zero product regressions.

## [2.5.0] - 2026-05-21

### Added
- Multi-distro release validation covering Ubuntu 24.04/22.04, Debian 12,
  Linux Mint 21.3, Fedora 41, Rocky Linux 9, Arch, Manjaro, CachyOS, and
  openSUSE Tumbleweed in CI/container form.
- Private Incus VM distro lab for real systemd, network, Docker daemon, Docker
  Compose, and installer dry-run coverage on Ubuntu 24.04, Fedora 42, Rocky 9,
  Arch current, and openSUSE Tumbleweed.
- Sanitized validation matrix documenting the layered CI, distro lab, and
  real-hardware fleet surface, tested phases, release-readiness receipt, and
  current evidence boundaries.
- AMD runtime diagnostics endpoint (`/api/gpu/amd-runtime`) reports Lemonade vs
  llama-server, host vs container, accelerator backend, and health from
  explicit installer state.
- Explicit AMD inference env contract (`AMD_INFERENCE_RUNTIME`,
  `AMD_INFERENCE_BACKEND`, `AMD_INFERENCE_LOCATION`, `AMD_INFERENCE_PORT`) for
  Linux, Windows, and WSL/Docker Desktop installs.
- AMD runtime capability metadata (`AMD_INFERENCE_SUPPORTED_BACKENDS`,
  `AMD_INFERENCE_RUNTIME_MODE`, `AMD_INFERENCE_MANAGED`) for dashboard
  diagnostics and `ods doctor`.
- Release evidence and golden-path contracts for generated config, update
  rollback behavior, and downstream builder validation.

### Changed
- Linked public support, testing, and platform-claim docs to the validation
  matrix so release claims point at layered evidence instead of informal
  maintainer memory.
- Updated ODS Proxy and Hermes Proxy to `caddy:2.11.3-alpine`.
- Centralized AMD Lemonade runtime metadata in `config/backends/amd.json` and
  aligned the Linux Docker image pin to
  `ghcr.io/lemonade-sdk/lemonade-server:v10.2.0`.
- Hardened installer/runtime defaults for Hermes, OpenCode, Perplexica,
  bootstrap model swaps, update flows, and extension gating.

### Fixed
- Rocky/RHEL-family Docker installation now falls back to Docker's CentOS/RHEL
  repository when distro packages are unavailable.
- DNF package resolution now avoids `curl` vs `curl-minimal` conflicts on
  Fedora/RHEL-style systems.
- Windows AMD installs now pass deterministic runtime state into dashboard-api
  instead of requiring the container to infer host-side Lemonade vs Vulkan
  fallback.
- Perplexica, LiteLLM, OpenCode, bootstrap-upgrade, uninstall, macOS logging,
  and model-selection regressions fixed across the 2.5.0 cycle.

### Security
- Documented the retired LiveKit credential exposure as resolved so public audit
  readers do not mistake retired leaked values for active secrets.
- Added or expanded release contracts for dependency pinning, network exposure,
  support bundles, and secret scanning.

### Validation
- Full fleet pass on 2026-05-21 for the v2.5.0 release candidate.
- Hardware fleet: Linux NVIDIA, AMD Strix Halo, Linux ARM NVIDIA, constrained
  Apple Silicon, and high-memory Apple Silicon targets all passed install, 7/7
  verify, Hermes seeded echo, UI checks, and applicable capability probes.
- Regressions: 9/9 fixtures green, 0 bugs detected, 0 PRs opened.
- Distro lab: Docker matrix passed 10/10 distros; Incus VM matrix passed 5/5
  VMs with real systemd + Docker and clean installer dry-runs.
- Known follow-up: concurrent distro-lab and hardware-fleet installs on the
  same host can create I/O contention. Prefer serialization or a future
  `--parallel-limit` flag when running both surfaces together.

## [2.4.0] - 2026-03-24

### Added
- Native AMD Lemonade inference backend with NPU + ROCm + Vulkan acceleration
- LiteLLM model aliasing for AMD (friendly model names resolve to Lemonade internal IDs)
- AMD/Lemonade contract test suite (17 tests in `tests/contracts/test-amd-lemonade-contracts.sh`)
- Lemonade Docker image pinned to v10.0.0 with libatomic1 fix (`Dockerfile.amd`)
- Host-systemd service support in dashboard health checks (OpenCode no longer grayed out)
- `ODS_MODE=lemonade` for AMD installs — routes all services through LiteLLM proxy
- Bootstrap model aliasing — both tier and bootstrap model names resolve in LiteLLM
- NPU detection on Windows (Win32_PnPEntity) and Linux (sysfs/lspci)

### Changed
- AMD backend upgraded from generic Vulkan llama-server to native Lemonade Server
- LiteLLM runs as default inference proxy on AMD installs
- Lemonade image pinned to v10.0.0 (no longer `:latest`)
- LiteLLM auth disabled for localhost-only AMD installs (all ports bind 127.0.0.1)
- OpenCode config always synced on reinstall (stale API keys and URLs updated)

### Fixed
- APE healthcheck replaced curl (missing in slim image) with python3 urllib
- Windows installer surfaces docker compose config errors on failure instead of just exit code
- Windows installer passes `--env-file .env` to docker compose for reliable variable loading
- Dashboard no longer grays out host-systemd services unreachable from Docker
- `.env.schema.json` updated for `ODS_MODE=lemonade`, `TARGET_API_KEY`, `LLM_BACKEND`, `LLM_API_BASE_PATH`
- Lemonade entrypoint uses absolute path (`/opt/lemonade/lemonade-server`)
- Service health endpoint override for Lemonade (`/api/v1/health` vs `/health`)
- Perplexica, Privacy Shield, OpenClaw, Open WebUI API paths corrected for Lemonade (`/api/v1`)
- OpenCode config filename (`config.json` copy), LiteLLM routing, and small_model fallback

## [2.0.0-strix-halo] - 2026-03-04

### Added
- AMD Strix Halo support with ROCm 7.2 and unified memory tiers (SH_LARGE, SH_COMPACT)
- NVIDIA ultra tier (NV_ULTRA) for 90GB+ multi-GPU configurations
- Qwen3 Coder Next (80B MoE) model support for high-memory systems
- Product landing page README with screenshots and YouTube demo
- Dashboard screenshots, installer GIF, and download sequence images
- Architecture Decision Record for Docker image tag pinning
- 55 pytest unit tests for dashboard-api (GPU, helpers, config, agent monitor, security)
- CI workflow for dashboard-api tests

### Changed
- README rewritten as product landing page (feature highlights, comparison table, screenshots)
- CONTRIBUTING.md updated from pre-ODS branding to "ODS"
- Repository About section updated with new description, website, and topics

### Fixed
- Timing attack vulnerability in privacy-shield API key comparison (now uses `secrets.compare_digest`)
- `HTTPBearer(auto_error=False)` in privacy-shield silently passing `None` instead of returning 401
- Dependency version bounds added to privacy-shield and token-spy requirements.txt

## [2.0.0] - 2026-03-03

### Added
- Documentation index (`docs/README.md`) for navigating 30+ doc files
- `.env.example` with all required and optional variables documented
- `docker-compose.override.yml` auto-include for custom service extensions
- Real shell function tests for `resolve_tier_config()` (replaces tautological Python tests)
- Dry-run reporting for phases 06, 07, 09, 10, 12
- `Makefile` with `lint`, `test`, `smoke`, `gate` targets
- ShellCheck integration in CI
- `CHANGELOG.md`, `CODE_OF_CONDUCT.md`, issue/PR templates

### Changed
- Modular installer: 2591-line monolith split into 6 libraries + 13 phases
- All services now core in `docker-compose.base.yml` (profiles removed)
- Models switched from AWQ to GGUF Q4_K_M quantization

### Fixed
- Tier error message now auto-updates when new tiers are added
- Phase 12 (health) no longer crashes in dry-run mode
- n8n timezone default changed from `America/New_York` to `UTC`
- Stale variable names in INTEGRATION-GUIDE.md
- Embeddings port in INTEGRATION-GUIDE.md (9103 → 8090)
- Purged all stale `--profile` references across codebase (12+ files)
- Purged all stale `docker-compose.yml` references in docs
- AWQ references in QUICKSTART.md updated to GGUF Q4_K_M
- `make lint` no longer silently swallows errors
- Makefile now uses `find` to discover all .sh files instead of hardcoded globs

### Removed
- Token Spy (service, docs, installer refs, systemd units, dashboard-api integration)
- `docker-compose.strix-halo.yml` (deprecated, merged into base + amd overlay)
- Tautological Python test suite (`test_installer.py`)
- `asyncpg` dependency from dashboard-api (was only used by Token Spy)

## [0.3.0-dev] - 2025-05-01

Initial development release with modular installer architecture.
