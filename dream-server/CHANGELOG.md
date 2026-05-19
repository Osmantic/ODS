# Changelog

All notable changes to Dream Server will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- AMD runtime diagnostics endpoint (`/api/gpu/amd-runtime`) reports Lemonade vs llama-server, host vs container, accelerator backend, and health from explicit installer state.
- Explicit AMD inference env contract (`AMD_INFERENCE_RUNTIME`, `AMD_INFERENCE_BACKEND`, `AMD_INFERENCE_LOCATION`, `AMD_INFERENCE_PORT`) for Linux, Windows, and WSL/Docker Desktop installs.
- AMD runtime capability metadata (`AMD_INFERENCE_SUPPORTED_BACKENDS`, `AMD_INFERENCE_RUNTIME_MODE`, `AMD_INFERENCE_MANAGED`) for dashboard diagnostics and `dream doctor`.

### Changed
- Updated Dream Proxy and Hermes Proxy to `caddy:2.11.3-alpine`.
- Centralized AMD Lemonade runtime metadata in `config/backends/amd.json` and aligned the Linux Docker image pin to `ghcr.io/lemonade-sdk/lemonade-server:v10.2.0`.

### Fixed
- Windows AMD installs now pass deterministic runtime state into dashboard-api instead of requiring the container to infer host-side Lemonade vs Vulkan fallback.

## [2.4.0] - 2026-03-24

### Added
- Native AMD Lemonade inference backend with NPU + ROCm + Vulkan acceleration
- LiteLLM model aliasing for AMD (friendly model names resolve to Lemonade internal IDs)
- AMD/Lemonade contract test suite (17 tests in `tests/contracts/test-amd-lemonade-contracts.sh`)
- Lemonade Docker image pinned to v10.0.0 with libatomic1 fix (`Dockerfile.amd`)
- Host-systemd service support in dashboard health checks (OpenCode no longer grayed out)
- `DREAM_MODE=lemonade` for AMD installs — routes all services through LiteLLM proxy
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
- `.env.schema.json` updated for `DREAM_MODE=lemonade`, `TARGET_API_KEY`, `LLM_BACKEND`, `LLM_API_BASE_PATH`
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
- CONTRIBUTING.md updated from legacy "Lighthouse AI" branding to "Dream Server"
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
