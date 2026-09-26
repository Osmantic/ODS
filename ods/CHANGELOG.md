# Changelog

All notable changes to ODS will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Security
- Perplexica's internal `scrape_url` action is disabled at container start. It
  opened any URL its model named, without address validation, from the
  Perplexica container on the ODS network, and Perplexica offered it in every
  mode, so a request or a search result could steer it to an internal service.
  Asking Perplexica about a specific URL now answers from search results.
- The dashboard asks for sign-in when it is reached from another device: LAN
  mode, ODS proxy (`dashboard.<device>.local`), a reverse proxy or Tailscale
  Serve. Previously its proxy added the admin API key to every request, so
  anyone who could reach it had full control. Browsers on the ODS machine
  itself (`http://localhost`) are unchanged. Sign in once per browser (30 days)
  with a user-chosen password. The frosted sign-in, setup and recovery screens
  match the dashboard. Local owners can defer password setup; remote access
  stays protected. `ods dashboard-login` prints a short-lived, single-use
  recovery link. Password replacement revokes other dashboard sessions and
  unused links; only a salted password hash is stored.
- Chat-only guest invites no longer set the `ods-session` cookie, so they
  cannot open ODS Talk or pass the optional Hermes gate. Owner cards and
  Hermes invites are unchanged.
- Previously issued ODS session cookies are invalidated at upgrade, including
  unexpired chat-only guest cookies. Owners renew through the existing owner
  card or authenticated dashboard flow; default direct Hermes access is unchanged.
- Every llama.cpp image is now pinned by tag and sha256 digest, including the
  tier-map, installer, host-agent and catalog copies. The dependency pin check
  rejects a llama.cpp image without a digest.

### Changed
- Windows: `install.ps1` now installs ODS inside Ubuntu/WSL2 with Pixel
  (`--pixel --no-hermes --no-openclaw`) instead of the native Windows stack.
  It prepares WSL and Ubuntu 24.04 when needed, and stops with instructions,
  before changing anything in Ubuntu, when WSL2, systemd, a non-root user,
  Docker Desktop's WSL integration or, on NVIDIA machines, a Windows driver
  >= 570 with GPU and `nvidia` runtime visible from Ubuntu is missing. An
  existing Ubuntu older than 24.04 is never reused. Success now requires the
  authenticated Portal status API to report the agent available. Existing
  native Windows installs are detected and left untouched; `install.ps1`
  refuses to run beside them. Keep managing them with their own `ods.ps1`, or
  rerun `ods\installers\windows\install-windows.ps1`. AMD machines that used
  the native Lemonade path now get a GPU backend detected inside WSL, CPU, or
  an explicitly configured endpoint. The Linux installer runs on the same
  console (download progress and UTF-8 output stay visible), and warnings WSL
  prints on stderr no longer turn a passing check into a failure.
- Linux on WSL: an NVIDIA driver older than 570 stops with Windows update
  instructions instead of installing `nvidia-driver-*` inside the distro,
  which breaks WSL GPU passthrough.
- Every curated catalog download URL now names a Hugging Face commit instead
  of `resolve/main`, so an upstream rewrite cannot change or remove a catalog
  file. The 48 other re-pinned models download the same bytes: each sha256 was
  checked at the pinned commit. A CI test rejects unpinned catalog URLs. An
  installer rerun still keeps an active model whose `.env` has the old
  `resolve/main` URL when the repo, file path and sha256 match the catalog, and
  writes the pinned URL.
- The Intel Docker image (`server-intel-b9014`), the Apple Docker image
  (`server-b9014`) and fresh native Windows Vulkan installs
  (`llama-b9014-bin-win-vulkan-x64.zip`, SHA-256 now checked before
  extraction) move from llama.cpp b8248 to b9014, the build NVIDIA and CPU
  already use. b8248 ignores `LLAMA_ARG_REASONING` and `LLAMA_ARG_SPEC_TYPE`,
  and rejects `--spec-draft-n-max`, which the Windows launchers pass when
  `LLAMA_ARG_SPEC_DRAFT_N_MAX` is set. Not measured on Intel or native Windows
  hardware.
  - Intel and Apple Docker now honor the reasoning-off default, so Qwen3.5
    stops thinking by default on these backends (it thought on b8248). This
    is intended; set `LLAMA_REASONING=on` to keep thinking.
  - Intel: ODS no longer sets `SYCL_CACHE_PERSISTENT=1` for llama-server; the
    persistent SYCL kernel cache crashes llama-server with the oneAPI 2025.3
    runtime in the b9014 image. Every start now JIT-compiles kernels again
    (~30 s). On hosts with more than one Intel GPU that runtime can crash with
    the default `ONEAPI_DEVICE_SELECTOR=level_zero:gpu`; set
    `ONEAPI_DEVICE_SELECTOR=level_zero:0` in `.env`, which the Intel overlays
    now read and the installer keeps.
  - The installer selects `docker-compose.arc.yml` for Intel, not
    `docker-compose.intel.yml`, so the Intel image change reaches only stacks
    started with the Intel overlay by hand. The Arc local-build path
    (`docker-compose.arc.yml`, `images/llama-sycl`) was already broken and is
    not moved to b9014 by this change: its image copies only the
    `llama-server` binary although llama.cpp builds shared libraries by
    default, the installer never builds it (it starts Compose with
    `--no-build --pull never`) and never rebuilds an existing
    `ods-llama-sycl:local`, and b9014 has not been compiled on its oneAPI
    2025.0.0 base. Only its source defaults changed (tag `b9014`, pinned
    commit).
  - Native Windows: re-running the installer keeps an existing
    `llama-server.exe`, so installs from before this change stay on b8248.
    To move to b9014, delete `<install>\llama-server` and re-run the
    installer. Every Windows launch path now reads the installed binary's
    `--help`: on b9014 it passes `LLAMA_REASONING` as `--reasoning` (b9014
    defaults it to `auto`, and `--reasoning-format none` alone returns the
    reasoning inside the reply); on b8248 it keeps `--reasoning-format` and,
    for `off`, adds `--reasoning-budget 0`, which is what turns thinking off
    there. Before this change, b8248 installs returned Qwen3.5's reasoning
    inside every reply.
- Model selection ranks installable models by a curated priority per memory
  class and checks fit with a memory estimate built from each model's
  attention layout, instead of picking the largest file that fits. Fleet
  hosts keep their models. Off-fleet hardware that received phi-4,
  DeepSeek-R1 or Qwen3-30B-A3B (served past its 40,960-token limit) now gets
  Qwen3.5 9B, Qwen3.5 27B or Qwen3.6 35B-A3B at 64K-128K; Apple 8 GB gets
  Nemotron 3 Nano 4B at 64K, and CPU-only hosts get Q8-KV runtime profiles
  sized for the llama-server container. Each pick serves the 64K context
  Hermes needs where a model fits at 64K; the installers re-check the fit
  before raising a smaller context, and record the served context so a
  Dashboard restore of the installer's pick no longer drops to 32K. A
  Dashboard model switch uses the same context rule as the installer and
  never asks for more than a model's native context, and ODS Talk says up
  front when the context llama-server actually serves is below 64K instead
  of failing in Hermes. An installer rerun keeps a previously active model
  (clamped to its native context) rather than replacing it; when that model
  cannot serve 64K, Talk is shown as unavailable with the reason.
- Gemma 4 26B-A4B, E2B and E4B now run at 64K: their sliding-window layers
  keep the KV cache small, so the context no longer rules them out of Hermes.
- Perplexica now runs upstream release v1.12.2, published under its new name
  Vane (`itzcrazykns1337/vane:slim-v1.12.2`, digest-pinned). The UI shows the
  Vane name; ODS keeps the `perplexica` service, port and volumes, so settings
  and chat history carry over. Speed and Balanced searches now rank SearXNG
  results with the configured embedding model; the default built-in model is
  downloaded from Hugging Face on the first search after each container
  recreate, and offline hosts fall back to unranked results. The slim release
  image has no Chromium, so Quality mode and the `scrape_url` tool cannot read
  pages.
- llama-server on the NVIDIA and CPU images (llama.cpp b9014) now uses lossless
  n-gram speculative decoding (`--spec-type ngram-mod`) unless the model's
  runtime profile sets its own `LLAMA_ARG_SPEC_TYPE`. On an RTX 5090 with
  Qwen3.5-27B, a copy-heavy edit fell from 89.5 s to 13.3 s and a whole-file
  rewrite from 70.1 s to 15.6 s. Novel generation and prefill did not change.
  Set `LLAMA_SPEC_TYPE=none` in `.env` to turn it off. Lemonade, Intel/Arc,
  Apple Docker and native Windows runtimes are unchanged; native macOS is
  covered below.
- Native macOS installs llama.cpp b9014 (Metal, `llama-b9014-bin-macos-arm64.tar.gz`,
  SHA-256 `565aecda…4f22d`) instead of b8210, the same release as the Linux
  images. b8210 turns speculative decoding off for hybrid models such as
  Qwen3.5. On the fleet Mac mini M4 with Qwen3.5-9B, a copy-heavy file edit
  fell from 158.2 s to 41.1 s and a whole-file rewrite from 155.6 s to
  49.0 s, with byte-identical output. Existing installs keep their binary
  until a fresh install or `get-ods.sh --force`.
- Native macOS llama-server now keeps 32 prompt checkpoints per slot
  (`--ctx-checkpoints 32`) unless `LLAMA_ARG_CTX_CHECKPOINTS` is set. On a Mac
  mini M4 with Qwen3.5-9B and b8210, editing a tool result 9 turns back fell
  from 84.3 s to 33.4 s. It also uses `--spec-type ngram-mod` when the
  installed llama-server supports it (b8955+), with the same
  `LLAMA_SPEC_TYPE=none` opt-out as Docker. On runtimes with b9014's
  `--reasoning` switch, `LLAMA_REASONING` (default `off`) is passed as
  `--reasoning`, as Docker does. Without it, b9014 turned Qwen3.5 thinking on
  and put `<think>` blocks in replies.

### Fixed
- Gemma 4 26B-A4B (`gemma4-26b-a4b-q4`) and Gemma 4 31B (`gemma4-31b-q4`)
  download again. ggml-org deleted both Q4_K_M files from its repos on
  2026-07-16, so the catalog and the Gemma-profile tier maps (`NV_ULTRA`,
  `SH_LARGE`, `SH_COMPACT`, tiers 3 and 4) pointed at URLs that return 404.
  Their checksums had been stale since ggml-org replaced the files on
  2026-04-12. Both now use pinned unsloth revisions with exact sha256 and size:
  `gemma-4-26B-A4B-it-UD-Q4_K_M.gguf` (unsloth's Q4_K_M-class quant for this
  model, 16.9 GB) and `gemma-4-31B-it-Q4_K_M.gguf` (18.3 GB). Only
  `MODEL_PROFILE=gemma4` or `auto` installs were affected; the default `qwen`
  profile never selects these models. Upgrade impact on an installer rerun:
  - The 26B file name changed, so an existing 26B install is not preserved.
    The rerun takes the current recommendation, and the old file stays in
    `data/models`.
  - The 31B keeps its file name, but the old file fails the new size check, so
    the rerun takes the current recommendation. If that is the 31B again, the
    installer finds a SHA256 mismatch, deletes the file and downloads 18.3 GB.
  - A 32 GB NVIDIA GPU with `MODEL_PROFILE=gemma4` or `auto` on the Pixel
    default route now gets Gemma 4 31B at 128K context instead of 26B-A4B. The
    corrected file size puts its estimate at 31.07 GB of 31.8 GB; that fit is
    estimated, not measured.
  - Before this release these Gemma reruns already failed for most installs
    (stale checksum, then a 404 on re-download), so this mostly replaces
    reruns that were failing.
- Native macOS launches no longer pass `--spec-draft-n-max` to a llama-server
  that does not know it. Setting `LLAMA_ARG_SPEC_DRAFT_N_MAX` stopped the b8210
  Metal server from starting (its flag is `--draft-max`). Draft flags are now
  spelled for the installed binary, and an unsupported setting stops the
  restart before the running model is stopped.
- `LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS` is now `LLAMA_ARG_CHECKPOINT_EVERY_NT`,
  the name llama.cpp reads. Docker llama-server ignored the old name. Dashboard
  restarts of native macOS inference now read the same checkpoint keys as the
  installer (`LLAMA_ARG_CHECKPOINT_EVERY_NT`,
  `LLAMA_ARG_CHECKPOINT_MIN_SPACING_NT`).
- Cloud mode, hybrid mode's `cloud` route and the CLOUD tier now use Claude
  Sonnet 4.6 (`claude-sonnet-4-6`). The previous default,
  `claude-sonnet-4-5-20250514`, is not an Anthropic model ID: it paired the
  Sonnet 4.5 name with Sonnet 4's date, and neither Anthropic's model list nor
  LiteLLM's model map has it. The `fast` route stays on Claude Haiku 4.5
  (`claude-haiku-4-5-20251001`). A new test fails CI if any `anthropic/claude-*`
  ID in ODS is not on a verified allowlist.
- NVIDIA multi-GPU installs no longer use `--split-mode row` for tensor or
  hybrid GPU assignments; they use `layer`, the mode the fleet runs. llama.cpp
  b9890 removed CUDA row split, so row would stop the model loading once the
  pin moves; the pinned b9014 still accepts it. An existing `.env` keeps its
  value until the GPU assignment is recomputed, for example by
  `ods gpu reassign`. AMD is unchanged.
- Native Windows llama-server passes `LLAMA_ARG_CHECKPOINT_EVERY_NT` only when
  the installed binary's `--help` lists `--checkpoint-every-n-tokens`, the
  check native macOS already makes. llama.cpp b9310 removed the flag, and
  llama-server exits on a flag it does not know.
- Docker llama-server now receives `LLAMA_ARG_CHECKPOINT_MIN_SPACING_NT`
  (`--checkpoint-min-step`, llama.cpp b9310 and later; the pinned b9014 ignores
  it) and the draft KV cache types `LLAMA_ARG_SPEC_DRAFT_CACHE_TYPE_K`/`_V`,
  the names llama.cpp reads. The native-only `LLAMA_ARG_SPEC_DRAFT_TYPE_K`/`_V`
  keys never reached Docker.
- Activating Qwen 3.8 27B now stops with a clear message instead of failing
  to load: the default llama.cpp runtimes cannot load Qwen3.8 GGUFs.
- llama-server no longer mounts `config/llama-server/models.ini`. llama.cpp
  reads a preset file only with `--models-preset`, which ODS does not pass;
  ODS still writes the file.
- Corrections to earlier notes on llama.cpp env names: `LLAMA_ARG_NO_CACHE_PROMPT`
  does work on b9014 (any value disables prompt caching), and
  `LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS` never existed in llama.cpp; the flag's
  env name is `LLAMA_ARG_CHECKPOINT_EVERY_NT`.

## [3.0.0] - 2026-09-24

ODS V3 was published as `v3.0.0` on September 24, 2026. Full fleet
qualification remains incomplete. See [V3 notes](docs/RELEASE_NOTES_3.0.0.md)
for the immutable source commit and acceptance boundaries.

### Added
- Bundled Portal assistant, powered by Pixel, with dashboard conversations,
  streamed activity, managed workspace previews, research tools, and explicit
  extension and host-action approval flows on eligible platforms.
- Public, pinned Pixel source and installation artifacts, with bundle/source
  integrity checks and documentation of the separate ODS-only Pixel license.

### Changed
- ODS runtime, installers, dashboard package, and desktop installer package now
  identify as 3.0.0. Dependency and separately versioned Pixel versions are unchanged.
- Portal shows the advertised runtime model and distinguishes route availability
  from agent qualification. The non-actionable readiness banner was removed
  from chat; removal does not certify the agent or its model.
- Native macOS and qualifying Linux/WSL installations have additional ownership,
  lifecycle, sandbox, and artifact-binding checks. Native Windows continues to
  use its separate agent installation path without the Portal host runtime.

### Fixed
- Native Windows verifies private `.env` access before writing credentials in
  both Windows PowerShell and PowerShell 7; protection failures stop the install.
  Credentials are created with a private ACL and published by replacement, so
  an already-open reader cannot observe new credentials after a reinstall.
- Source update and rollback preserve quoted Compose paths. The source updater
  refuses native Pixel and source-built stacks whose runtime artifacts it cannot
  safely coordinate; ordinary image maintenance remains a separate operation.
- Generic backup and restore refuse unsupported native Pixel state instead of
  silently omitting it. Configuration-only archives explicitly record the
  exclusion. This restriction does not add native backup/recovery support.
  Ordinary Linux installations retain backup, restore and configuration rollback
  when run as root; account-owner receipts determine native state.
- Installer failure guidance preserves source and recovery receipts instead of
  recommending manual directory deletion or promising every retry is safe.
- Pixel retry and compaction handling preserves the current request, task
  activity, and goal plan, and avoids waiting for an impossible terminal retry.
- Workspace operations retain canonical project paths, reject mistaken host
  paths before file access, and verify published preview bytes independently
  from whether the overall user task succeeded.
- Reinstallation and update handling better recognizes owned Compose stacks,
  retires owned native macOS sandboxes, and preserves retired sandbox archives.
- Model streaming closes connections after client disconnects; memory-based
  context limits cover additional native and WSL installation paths.

### Validation boundaries
- V3 source publication does not establish full fleet acceptance. Pixel/Portal
  task quality, full model-switchboard qualification,
  and installed update/rollback/reboot acceptance remain incomplete. See the
  [promotion record](docs/PUBLIC_BETA_PROMOTION_2026-09.md) for evidence and
  known limitations; source and CI passes do not imply full fleet acceptance.

## [2.6.0] - 2026-07-28

### Added
- Remote-provider operations graduated into the product surface: direct and
  SSH egress routes, egress policy contracts, SSH tunnel supervision, peer ODS
  model discovery, remote model load/delete flows, and dashboard status/UI
  integration.
- Model Switchboard support now gives local applications a stable current-model
  route, selected context propagation, model identity validation, rollback-aware
  runtime swaps, and app probes across Open WebUI, Hermes, OpenCode,
  Perplexica, and other LLM consumers.
- GPU reassignment workflows now support verified multi-GPU model swapping on
  NVIDIA and Linux AMD/ROCm paths, including rollback when a reassignment or
  recreation fails.
- Dashboard and setup flows gained stack presets, an animated loading screen,
  better service URL handling, model download progress, first-boot polish, and
  clearer owner/support access paths.
- Linux rootless Docker installs now have subordinate-ID diagnostics and a
  rootless bind-mount ownership repair path for services with container-owned
  data directories.

### Changed
- Stable channel documentation now points at `v2.6.0` and identifies
  `release/2.6.x` as the current patch lane, with `release/2.5.x` retained
  only for critical old-stable continuity fixes.
- Version consistency now also checks the `ods-cli` fallback version so CLI,
  installer, dashboard, manifest, architecture, and changelog authorities move
  together.
- Runtime configuration is more centralized: generated configs, app routes,
  model-router state, switchboard mode, and Lemonade/native adapters share more
  of the same contracts across Linux, macOS, Windows, and Docker paths.
- Extension installation and dashboard service handling were hardened around
  transactional updates, dependency resolution, public service URLs, and
  compose-stack reconciliation.

### Fixed
- Dashboard Hermes readiness now accepts either llama-server or LiteLLM and
  requires the complete authenticated runtime chain. Hermes Single Sign-On now
  opens owner and support access management instead of duplicating the Agent
  runtime link.
- Windows native llama-server launches now carry both the llama.cpp metrics
  endpoint and `LLAMA_REASONING` selection, while the Windows host-agent Python
  resolver handles multiple PATH interpreters and Microsoft Store aliases.
- Intel and Arc compose overlays keep llama.cpp runtime tunables such as batch
  size, threads, parallelism, and metrics instead of losing base-command flags.
- Linux rootless Docker no longer receives host-side ownership repairs that map
  to the wrong namespace; ODS prepares and verifies bind mounts from inside the
  rootless container namespace.
- Token Spy's Postgres SSE cursor now uses a durable timestamp/UUID cursor so
  retention and UUID ordering cannot silently drop events.
- Remote provider direct egress now pins requests to policy-validated resolved
  addresses while preserving Host and TLS SNI, closing DNS-rebinding/TOCTOU
  escape paths.
- macOS writes OpenCode's `config.json` compatibility file alongside
  `opencode.json`, and installer-context parity now checks all platform
  writers.
- Perplexica detects AMD Lemonade runtime state on local installs and selects
  the served model id instead of routing to an unavailable GGUF name.
- Dashboard voice readiness no longer treats absent optional LiveKit as a
  voice-stack failure; installed optional voice services still gate on health.
- Dashboard model-memory estimates now read the catalog `gguf_file` key so
  dashboard activation agrees with installer model selection.
- Uninstall cleanup now scopes fallback container and volume discovery to ODS
  project prefixes, avoiding unrelated Docker resources.
- Windows Lemonade restarts tolerate stale listener/PID references without
  relaxing ownership checks for live unrelated processes.
- Offline model validation, model compatibility, model download host checks,
  imported-model exclusion, and model route foundation fixes reduce invalid
  catalog selections and stale app routes.
- Multiple installer and lifecycle regressions were fixed across macOS platform
  image pulls, Windows env-file replacement, native-port preflight, compose
  failure reporting, Linux TTY input, compose resolver errors, bootstrap resume,
  update dry-run version reading, and restart/doctor recovery paths.

### Security
- Remote-provider egress now validates direct provider DNS resolution and pins
  outbound requests to the approved address while keeping provider TLS identity
  intact.
- OAuth pending-state validation and local backend URL handling were hardened.
- Network exposure, dependency pin, secret-minLength, support-bundle, and
  release-claim contracts were expanded so release gates catch more unsafe
  drift.

### Validation
- Release-prep candidate `07e2a21e` had all PR checks green on 2026-07-28:
  Dashboard, Lint PowerShell, Matrix Smoke, Python Lint, Python Type Check,
  Secret Scan, ShellCheck, Test Linux, Validate .env Schema, and review gates.
  The branch is based on product merge commit `c292e00d`.
- Focused local validation on 2026-07-28 passed Windows parser/resolver,
  llama runtime tunables, metrics, reasoning, env schema, uninstall scoping,
  installer-context parity, rootless doctor, dashboard API regressions
  (`502 passed, 5 skipped`), and Perplexica/remote-provider/token-spy tests
  (`35 passed, 1 skipped`).
- Linux rootless ownership contract passed on Tower2 with
  `25` rootless ownership tests.
- Release-prep fleet validation on 2026-07-28 passed regressions,
  zero-prereq bootstrap, fresh install, verify, cloud-mode, dashboard, Hermes,
  UI policy, full-model capability finalize, lifecycle reinstall/restart, and
  `ods doctor` across Tower2, Strix Halo, Spark, M5 MacBook Pro,
  Windows laptop, and Strixy. The run recorded zero product bugs, zero harness
  limitations, and zero environment notes.
- Strict User Green is not claimed for this candidate: the long six-cycle
  browser model-management matrix was intentionally waived after partial pass
  evidence, and `dgx-gpu01` was excluded because its SSH host key changed and
  was not owner-verified.

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
