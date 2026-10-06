# Changelog

All notable changes to ODS will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed
- `ods status` now respects `GPU_BACKEND`, using the existing AMD and Apple
  GPU reporters instead of choosing NVIDIA tooling merely because it is
  installed. AMD device counting in `ods gpu status` uses DRM sysfs, and
  `ods status --json` no longer queries `nvidia-smi` for non-NVIDIA backends.
  AMD JSON GPU summaries remain `null`.

### Security
- Open WebUI no longer starts for other devices while its built-in
  administrator, `admin@localhost`, still has the password `admin`. Open WebUI
  creates that account while it runs without sign-in, the default for a
  localhost-only install, and the account keeps working after sign-in is
  turned on. So anyone on the network could sign in as administrator once the
  install was exposed. When Open WebUI would be reachable from other devices,
  through `BIND_ADDRESS` or the ODS proxy, its start-up now refuses, changes
  nothing, and explains in its log how to change that password first. The
  ODS proxy now also counts as exposure for the sign-in rule below.
- An imported extension can no longer take the name of a folder ODS keeps
  under `data/` or `config/` (such as `models`, `config-backups` or
  `persona`). Before, an extension's own `./data/<id>` and `./config/<id>`
  binds would then have reached ODS's folder. Both extension validators
  refuse those binds. Purging extension data now refuses those folders and
  any id that no shipped, listed or installed extension owns, as
  `ods purge` already did.
- Open WebUI now starts with sign-in on whenever it is published beyond this
  machine (`BIND_ADDRESS` not loopback), whatever `WEBUI_AUTH` says in `.env`.
  The CLI, `ods.ps1` and the host agent already turn sign-in on in that case.
  The check now also runs inside the container, so paths that skip those
  tools cannot publish Open WebUI on the network without sign-in: the
  Dashboard's update, a rollback, or a plain `docker compose up`.
- Other containers can no longer spend a remote LLM provider's API key
  (GHSA-4rpc-g4mc-jm9c). The remote-provider egress, which adds the
  provider key to outbound requests, accepted unauthenticated requests from
  any container on `ods-network`, including installed extensions. The SSH
  tunnel's forwards were reachable the same way.
  - The egress and the tunnel now run on their own networks. Only LiteLLM and
    dashboard-api can reach them, over an internal network.
  - The egress refuses every request except its status reads unless the
    caller presents the LiteLLM gateway key.
  - The extension policy refuses extensions that join those networks, join a
    network their own file does not declare, or name a container after a core
    service.
  - This only mattered when a remote provider was configured.
- n8n is upgraded from 2.6.4 to 2.41.6. Twelve critical advisories affect
  2.6.4, most of them remote code execution by a signed-in n8n user, and
  2.6.4's owner account went to whoever completed n8n's first-run screen
  first, including another container on `ods-network`.
  - New installs, and installs where nobody had created n8n's owner, now
    get the owner from `N8N_USER`/`N8N_PASS` before n8n starts. An owner
    someone already created stays as it is.
  - n8n's database upgrades cannot be undone, so ODS copies the database
    to `data/n8n/ods-backups/` before a new n8n version first starts.
  - The plaintext `N8N_PASS` no longer reaches n8n's environment; ODS
    passed it as `N8N_DEFAULT_ADMIN_PASSWORD`, which n8n does not read.
- Open WebUI is upgraded from 0.7.2 to 0.11.4, which fixes 101 published
  advisories that affect 0.7.2: 1 critical, 41 high, 54 medium and 5 low. Two
  of the high ones are fixed only in 0.11.4.
  - Open WebUI's settings now come from ODS at every start
    (`ENABLE_PERSISTENT_CONFIG=false`). Since 0.10, Open WebUI otherwise writes
    every setting into its database on its first start and ignores later
    changes, so mode switches, Pixel, key rotation and the Dashboard Settings
    page would have stopped applying. Changes made in Open WebUI's Admin Panel
    > Settings now last until Open WebUI restarts, and settings saved there
    before this release are no longer used.
  - Signup is closed (`ENABLE_SIGNUP=false`). Open WebUI still lets the first
    account sign up on a new install with sign-in on and makes it the
    administrator, who adds other accounts in Admin Panel > Users.
  - Open WebUI's database migrations cannot be undone, so ODS copies
    `webui.db` to `data/open-webui/ods-backups/` before a new Open WebUI
    version first starts and keeps the two newest copies. It refuses to start
    Open WebUI, with the reason in its log, when two accounts have email
    addresses that differ only in case (the upgrade would stop partway
    through), or when the image is older than the version that last used the
    data. The first start after this upgrade migrates the database and can
    take many minutes on a large install.
  - Chats and models without a chosen function-calling mode now use Open
    WebUI's Native tool calling.
- Token Spy's dashboard now shows agent and model names as text
  (GHSA-7jvf-39fc-rwr6). The programs that send requests through Token Spy
  supply those names, and the dashboard inserted them into the page as HTML, so
  a crafted name could run script in the browser of whoever opened the
  dashboard, where the Token Spy API key is held for the session. The reset
  button also no longer places the agent name inside inline JavaScript.
- On native Windows, `.\ods.ps1 start`, `restart` and `update` now turn Open
  WebUI sign-in on whenever `BIND_ADDRESS` publishes Open WebUI beyond this
  machine, as `ods start` and `ods restart` already did on Linux, WSL and macOS
  (GHSA-69cg-cxxf-jc6m). Before, a localhost-only Windows install whose `.env`
  was edited to `BIND_ADDRESS=0.0.0.0` restarted Open WebUI with sign-in off,
  reachable from the network. `ods update` on Linux, WSL and macOS now applies
  the same rule before it recreates the containers.
- Installer, update, health-check and CLI scripts no longer put API keys on a
  command line. Fifteen curl calls passed `Authorization: Bearer <key>` as an
  argument, which any local user can read with `ps` while the call runs. The
  background model upgrade polls repeatedly, so its keys were exposed for
  long stretches. Keys now reach curl through a header file descriptor, or on
  stdin when curl runs inside a container through `docker exec`. The affected
  keys were the LiteLLM key, the host-agent key, the dashboard API key, the
  Lemonade key and an optional `GITHUB_TOKEN`. A CI check now fails on any
  shipped script that puts a credential header on a command line.
- The Portal Full Access confirmation now says what it turns off. It disables
  the sandbox and per-command approval, so commands run directly as the owner
  account, while web search and page fetching stay on. It also notes that
  docker group membership is equivalent to root. It previously said that
  existing operating system restrictions remain.
- Pixel's agent can no longer create or edit command-type scheduled jobs.
  OpenClaw 2026.6.33, which Pixel pins, rejected only the exact payload kind
  `command` and lowercased it afterwards, so a kind such as `Command` became a
  command job that runs in the gateway process outside the sandbox
  (GHSA-8xxh-v4vc-qvm4, fixed upstream in 2026.7.1). The Pixel plugin now
  refuses any agent cron add or update whose payload kind normalizes to
  `command`. Command jobs the owner creates through the CLI are unaffected.
- The host agent now keeps only the newest 20 `.env` backups in
  `data/config-backups/`. Each Dashboard settings save added another full copy
  of every secret, with no limit. Only regular files that match the backup
  name pattern are pruned, and a pruning failure never fails the save.
- Before Pixel's workspace-guidance migration changes `AGENTS.md` or
  `MEMORY.md` in an existing owner workspace, it now keeps the original files
  in a private `.ods-workspace-guidance-backups/` directory beside the
  workspace, outside what the agent reads. If the backup cannot be written,
  nothing is changed. A new CI check fails when owner-private or fleet-specific
  text (maintainer names, test machine names, private workflow terms, personal
  home paths or LAN addresses) would reach the Pixel workspace template or the
  agent and stack templates.
- Pixel 4.3.29 no longer ships the retired owner-private section in its
  workspace template. `AGENTS.md` now carries the same neutral model-routing
  guidance that the installer already wrote into existing workspaces, and the
  matching `MEMORY.md` entry is gone, so a new workspace starts where a migrated
  one ends. Existing installations move to 4.3.29 through the held source
  upgrade; the version changes only so that upgrade accepts the new source. The
  installer migration still repairs workspaces created from older releases, the
  Portal bootstrap filter presents an unmigrated 4.3.28 default as the 4.3.29
  one, and CI fails if the shipped template would ever need the migration again.
- Support bundles now mask credentials by format wherever they appear (provider
  API keys such as OpenAI, Anthropic, Hugging Face, GitHub, Slack and AWS, JWTs
  and PEM private keys), not only under secret-looking key names, and mask every
  secret value from the installation's `.env` wherever a log echoes it. The
  bundle directory and archive are owner-only. Before this change, 28 of 28
  tested credential formats survived in log lines, JSON values and custom
  headers.
- NVIDIA Secure Boot enrollment no longer installs a root systemd unit to
  resume the install after the reboot. That unit ran the user-writable
  `install.sh` as root at every boot. The installer refuses root, so the
  unit failed every time and never removed itself. The reboot screen now prints
  the command that finishes the install, and the installer's preflight removes
  `ods-install-resume.service` left by older versions (not in
  `--preflight-only` or dry-run mode).
- Open WebUI sign-in is now enforced whenever `BIND_ADDRESS` publishes it
  beyond loopback, not only when the ODS proxy is installed. Saving a
  network `BIND_ADDRESS` in Dashboard Settings writes `WEBUI_AUTH=true`, and
  `ods start` / `ods restart` (Linux, WSL and macOS) and the host agent's
  Open WebUI start and recreate paths apply it before the container starts.
  Previously a localhost-only install moved to `0.0.0.0` this way kept Open
  WebUI's single-user mode, whose built-in `admin@localhost` account has the
  password `admin`.
- Vulnerability reports now go through GitHub private vulnerability reporting
  (Security → Report a vulnerability) or `security@osmantic.com`; `SECURITY.md`
  no longer asks reporters to open public issues. The security guide now covers
  the full set of generated secrets, a rotation recipe that works with every
  generated value, the Open WebUI `admin@localhost` account to secure before LAN
  exposure, the trust boundary for local browsers, `ods-network` containers,
  the host agent and Pixel Full Access, and how to apply code fixes to an
  existing installation (`ods update` refreshes images only).
- Native Windows uninstall now verifies each container's Compose installation
  directory before any mutation. A shared `ods` project label cannot authorize
  removing another WSL/Windows installation or unattached volumes of unknown
  origin. Docker listing failures preserve the installation for recovery.
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
- The host agent now accepts only whole, plain extension ids. Its check also
  passed an id that ends in a line break, such as `n8n` followed by a newline,
  which then reached extension folder names and Compose arguments.
- Pixel's provider connection and health probes now require TLS 1.2 or newer.
  Python 3.10 and later already refuse older protocols; the host side also runs
  on Python 3.9, whose default context can still allow them.
- The host agent reads the Hermes model settings in `data/hermes/config.yaml`
  with a linear-time pattern. The previous pattern slowed down polynomially on
  a long line of spaces.

### Changed
- The unsupported Tauri desktop installer under `installer/` is removed. No CI
  built it and no release shipped it, and its installer arguments no longer
  matched the current installers. Its build dependencies carried the
  repository's last four open Dependabot alerts. Install with the commands in
  the README.
- Error responses no longer repeat internal exception text. The dashboard API
  (model state, OAuth, remote-provider status, setup diagnostics, update
  check, usage report, the owner-card check and extension manifest errors),
  model-router, the remote-provider egress and APE now report a fixed
  failure category, such as "not valid JSON" or "Could not reach GitHub". The
  exception detail goes to that service's log. Manifest errors name the
  extension folder instead of the container path.
- Privacy defaults: bundled services no longer phone home. Open WebUI's
  upstream version check, Qdrant usage telemetry, LiteLLM's start-up cost-map
  fetch from GitHub, n8n diagnostics and version notifications, and the
  Whisper Hugging Face client's telemetry are off. The dashboard now honors
  `DISABLE_UPDATE_CHECK=true`, which `--offline` already writes, and skips its
  GitHub release check. The FAQs and offline-mode guide now list exactly what
  ODS contacts by default, including the Portal agent's web search provider,
  and how to turn each off.
- Retired the AI GitHub workflows (`ai-issue-triage`, `claude-review`,
  `issue-to-pr`, `autonomous-code-scanner`, `nightly-code-review`,
  `nightly-docs-update`, `release-notes`). The repository holds no model API
  secrets, so they only produced skipped "green" reviews and failures, and
  `release-notes.yml` let untrusted PR titles steer an agent with release
  write access. A CI contract keeps them out until a reviewed redesign, and
  `docs/AI_WORKFLOW_GUARDRAILS.md` sets the policy for AI-assisted PRs.
- Windows: `install.ps1` now installs ODS inside Ubuntu/WSL2 with Pixel
  (`--pixel --no-hermes`) instead of the native Windows stack.
  It prepares WSL and Ubuntu 24.04 when needed, and stops with instructions,
  before changing anything in Ubuntu, when WSL2, systemd, a non-root user,
  Docker Desktop's WSL integration or, on NVIDIA machines, a Windows driver
  >= 570 with GPU and `nvidia` runtime visible from Ubuntu is missing. An
  existing Ubuntu older than 24.04 is never reused. Success now requires the
  authenticated Portal status API to report the agent available. Existing
  native Windows installs are detected and left untouched; `install.ps1`
  refuses to run beside them. Keep managing them with their own `ods.ps1`, or
  rerun `ods\installers\windows\install-windows.ps1`. On AMD machines the model
  runs on the GPU through llama.cpp's `llama-server.exe` on Windows (see "AMD
  GPUs now run on llama.cpp" below). The Linux installer runs on the same
  console (download progress and UTF-8 output stay visible), and warnings WSL
  prints on stderr no longer turn a passing check into a failure.
- Windows: setup now needs only the pasted PowerShell command. It checks disk
  space and BIOS virtualization first. It installs Docker Desktop with winget
  when missing (one restart shared with WSL) and continues by itself after
  that restart through a one-time per-user `RunOnce` entry. For a new Ubuntu
  it asks for the Linux username and password in PowerShell instead of the
  Ubuntu window. It starts Docker Desktop and, when Docker is not connected to
  the selected Ubuntu, shows the WSL integration setting to turn on and waits
  for it; it never edits Docker's settings or restarts Docker. It finally opens
  Portal and adds an
  **ODS Portal** desktop shortcut. `-NonInteractive` still installs nothing.
  A leftover `ODS-WSL-*` scheduled task from another ODS version is named in
  the error together with the command that removes it.
- Linux on WSL: an NVIDIA driver older than 570 stops with Windows update
  instructions instead of installing `nvidia-driver-*` inside the distro,
  which breaks WSL GPU passthrough.
- Linux on WSL with Docker Desktop: Pixel Edge now binds the runtime bridge
  as `/mnt/wsl/ods-portal-runtime/*`, the distro path Docker Desktop's WSL
  proxy translates, and the installer creates those empty targets before Pixel
  Edge starts. The daemon-side `/mnt/host/wsl/...` path stopped every fresh
  install with "is mounted on / but it is not a shared mount".
- The WSL runtime bridge now stacks on the bind Docker Desktop's WSL proxy
  places on each Pixel Edge bind source; it refused that bind, so every fresh
  WSL install stopped at "Could not install and start the private Pixel
  ingress". It also drops its own stale bind after systemd recreates a runtime
  directory, names the check that refused in its journal, and the installer
  prints that journal when the bridge does not start.
- The installer menu presets (Full Stack, Core Only) no longer override an
  explicit `--hermes` or `--no-hermes`. The Windows Pixel path passes
  `--no-hermes`; choosing Full Stack downloaded and enabled Hermes anyway.
- AMD GPUs now run on llama.cpp. ODS no longer uses Lemonade Server: AMD GPUs
  run upstream llama.cpp's `llama-server` (b9014), like NVIDIA, Apple and CPU
  installs. ODS never uninstalls or reconfigures a Lemonade Server installed
  on your computer. Upgrades keep the model files and, on Linux and in the
  Windows Portal, the selected model and its context;
  [AMD GPUs now run on llama.cpp](docs/MIGRATION-LEMONADE-TO-LLAMACPP.md)
  explains what an upgrade does and how to remove what is left.
  - Linux: the `llama-server` service runs the official
    `ghcr.io/ggml-org/llama.cpp:server-vulkan-b9014` image, pinned by digest,
    with `/dev/dri` and the video and render groups, and needs neither ROCm on
    the host nor an HSA override. `AMD_INFERENCE_BACKEND=rocm` adds
    `docker-compose.amd-rocm.yml` with the `server-rocm-b9014` image (about
    7 GB) and `/dev/kfd`. The installer selects ROCm for Instinct (CDNA)
    cards, which have no Vulkan driver, and sets `HSA_OVERRIDE_GFX_VERSION`
    only for GPUs that image was not built for (gfx1031 to gfx1036 as 10.3.0,
    gfx1103 as 11.0.0). One GPU runs with `LLAMA_ARG_SPLIT_MODE=none`; the AMD
    multi-GPU overlay uses layer split and passes the assigned GPUs as
    `GGML_VK_VISIBLE_DEVICES` and `ROCR_VISIBLE_DEVICES`. An integrated GPU
    next to a discrete one is left out of AMD detection and assignment. ODS no
    longer builds the `ods-lemonade-server` image.
  - Windows (`install.ps1`): with an AMD GPU the model runs on the GPU through
    llama.cpp's `llama-server.exe` (Vulkan) on Windows instead of on the CPU in
    WSL. Setup detects the GPU and its memory in Windows, picks the model as
    the native installer does, downloads the pinned
    `llama-b9014-bin-win-vulkan-x64.zip` into `%LOCALAPPDATA%\ODS\llama.cpp`
    (after asking on a new install; size and SHA-256 are checked before
    extraction, and every file again before each launch), checks it with
    `--version` and `--list-devices`, downloads the model with checksum
    verification, and runs it on 127.0.0.1 with an API key from the sign-in
    scheduled task `ODSLlamaServerRuntime-<SID>`. llama-server moves to 18080
    or 28080 when another program holds 8080. The task proves the model and
    context (`/v1/models`, `/props`) before setup proceeds and at each sign-in,
    keeps its launcher and plan in `%LOCALAPPDATA%\ODS\lemonade\portal-runtime`
    instead of a temporary installer checkout, and cleans up the verified
    process tree after a failed start; separate process ownership records let
    an interrupted cleanup resume without treating a failed launch as ready.
    Re-running setup stops only the verified ODS task and its process tree,
    and keeps the selected model and context, the port and the key. Setup
    passes the route to the Linux installer with the new `--native-llm-url`,
    `--native-llm-model`, `--native-llm-context-size`, `--native-llm-gpu-name`,
    `--native-llm-gpu-vram-mb`, `--native-llm-host-transport` and
    `--native-llm-api-key-env` options; the key travels in an environment
    variable, never on a command line. The hardware scan shows that GPU
    instead of "None". Without a usable Vulkan device a new install stays on
    the CPU and says so.
  - Windows/WSL routing: setup selects `--native-llm-host-transport
    model-router`. The WSL host agent verifies the Windows model through the
    running model-router container belonging to this installation, where
    `host.docker.internal` reaches Windows. This avoids probing WSL's own
    localhost while keeping llama-server bound to Windows loopback. Model
    identity, context and completion checks still decide readiness; this
    transport does not enable LAN access or cloud inference. Other installs
    keep the default `direct` transport. The Dashboard reads the Windows
    model, its context and llama.cpp's counters through the authenticated host
    agent (`/v1/llm/status`). Models and the Portal model selector follow the
    host agent's proof that this installation manages the server; a server it
    does not manage is shown as managed externally, without incorrectly
    reporting that the local runtime is unavailable. The hardware scan no
    longer claims CPU inference immediately after identifying the Windows GPU;
    Linux services retain their detected backend.
  - Windows (native installer): `install-windows.ps1` and `ods.ps1` run the
    same pinned `llama-server.exe` for AMD GPUs. The installer stages and
    checks it before it changes `.env`, so a failed download, checksum, Visual
    C++ runtime, policy block or driver changes nothing. It keeps a verified
    copy at `<install>\llama-server`, which a rerun replaces when it no longer
    matches the pin, keeps the API key file, launch options and log in
    `%LOCALAPPDATA%\ODS\native-runtime`, and starts the model at sign-in
    through the `ODSNativeLlamaRuntime` task (`ods.ps1 native-llm-start`).
    Model switches and the full-model swap after bootstrap relaunch through
    `ods.ps1 native-llm-restart`, which validates the new launch before it
    stops the running model. LiteLLM receives the server's key
    (`LLAMA_SERVER_API_KEY`) from its own environment. Without a usable Vulkan
    device a new install runs the model on the CPU and says so. Whisper keeps
    port 9000 unless a Lemonade router holds it.
  - Every platform: the Docker `llama-server` and the Windows `llama-server.exe`
    serve the GGUF file name as the model id (`--alias`), so `/v1/models`,
    model state, the model router and every consumer use one id. Every
    managed runtime is proven the same way: `/health` (503 while loading), the
    served model id, `/props` model path and context, and a completion. The
    Dashboard samples throughput from llama.cpp's cumulative `/metrics`
    counters on every runtime.
  - ODS Talk sends an image to the active model only when its llama-server
    loaded a vision projector, and otherwise answers 409 with a plain message.
    `ODS_TALK_VISION_MODEL` with `ODS_TALK_VISION_URL` and `ODS_TALK_VISION_KEY`
    still names a separate vision server.
  - A Lemonade Server you run yourself is an external OpenAI-compatible
    server: `--external-llm-url URL --external-llm-provider openai-compatible
    --external-llm-model ID`.
  - Upgrading: rerun the installer on Linux and with the native Windows
    installer, or `install.ps1` for the Portal. `ods update` only refreshes
    images and does not move an install off Lemonade. The installer moves
    Lemonade-era `.env` settings to llama.cpp before anything reads them. The
    Portal stages and checks llama.cpp while Lemonade keeps serving, stops
    only the Lemonade task ODS created, and restores and restarts it if the
    new runtime does not come up. An install that used its own Lemonade moves
    to the generic external route and keeps the server's address, its model
    and an API key you gave it (now in `config/litellm/external-upstream.key`);
    in a git checkout `ods-update.sh update` does this too. The Lemonade
    container's volumes and the `ods-lemonade-server:latest` image stay until
    `ods-uninstall.sh` removes them or you run the `docker volume rm` command
    the upgrade prints. `ods doctor` reports an `.env` that still selects
    Lemonade as `ODS-RUNTIME-LEMONADE-RETIRED`.
- On WSL, the host agent identifies Docker Desktop before choosing its bind
  address. A leftover native `docker0` bridge could have the same gateway IP
  as Docker Desktop and make the agent listen where ODS containers could not
  reach it. Docker Desktop now selects WSL loopback regardless of that stale
  bridge, for GPU and CPU installations alike.
- An explicit `--hermes` or `--no-hermes` now takes precedence in the Custom
  feature menu as well as presets. The Windows Pixel path no longer asks to
  enable an agent that its command line explicitly disabled.
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
  - Native Windows: an installer rerun on an AMD GPU replaces an older
    `llama-server.exe` that does not match the pinned b9014 build (see "AMD
    GPUs now run on llama.cpp"). Every Windows launch path now reads the
    installed binary's `--help`: on b9014 it passes `LLAMA_REASONING` as
    `--reasoning` (b9014 defaults it to `auto`, and `--reasoning-format none`
    alone returns the reasoning inside the reply); on b8248 it keeps
    `--reasoning-format` and, for `off`, adds `--reasoning-budget 0`, which is
    what turns thinking off there. Before this change, b8248 installs returned
    Qwen3.5's reasoning inside every reply.
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
  Set `LLAMA_SPEC_TYPE=none` in `.env` to turn it off. The AMD, Intel/Arc,
  Apple Docker and native Windows runtimes get no such default; native macOS
  is covered below.
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

### Removed
- The legacy OpenClaw extension, deprecated since 2026-05-12, is removed: the
  `ods-openclaw` container (image `ghcr.io/openclaw/openclaw:2026.3.8`, port
  7860), its configuration templates, its session-cleanup script and timer,
  the n8n OpenClaw trigger workflow and the dashboard API's
  `/api/service-tokens` endpoint, which only served the OpenClaw sidebar link.
  The extension still pinned OpenClaw 2026.3.8, an older release than the
  2026.6.33 runtime that Pixel qualifies. Portal (Pixel) and Hermes Agent are
  the supported agents. Pixel's own OpenClaw runtime
  (`openclaw-gateway.service`) is separate and unchanged.
- Installer reruns on Linux, macOS and Windows delete
  `extensions/services/openclaw` from the install directory and remove the
  `ods-openclaw` container when they start the stack. They also delete the
  OpenClaw templates in `config/openclaw` that are unchanged from a shipped
  version. Files the owner changed or added there, and `data/openclaw`, stay
  on disk; the installer names what it kept, and the
  [removal notice](docs/MIGRATION-OPENCLAW-TO-HERMES.md) explains how to
  delete it. On Linux, this cleanup waits while an unfinished Pixel source
  upgrade is pending, so the release that started it can still finish or roll
  it back. The installers no longer re-enable OpenClaw when they find its
  container or data.
- On installs where OpenClaw was the only feature that needed SearXNG or APE,
  an upgrade turns those services off and removes their containers.
- Git checkouts updated with `ods-update.sh update` must run
  `ods disable openclaw` first when OpenClaw is enabled, and move a modified
  `config/openclaw/openclaw.json` out of the checkout (see the removal
  notice). The updater of the previous release restarts the stack with its old
  file list, which fails once the pull deletes the OpenClaw files; running
  `ods-update.sh update` again then finishes the update. From this release on,
  the updater resolves the stack again after the pull and during a rollback.
- `--openclaw` and `--no-openclaw` (Linux and macOS) and `-OpenClaw` (Windows)
  are still accepted, but only print a notice. Installers no longer write
  `OPENCLAW_TOKEN`, `OPENCLAW_PORT` or `HOST_LAN_IP`. An `.env` that still has
  these or the other retired OpenClaw keys keeps validating; the Dashboard
  settings page lists them only when they are present, and clearing one
  removes it. AMD reruns retire the `openclaw-session-cleanup` user timer
  while it still carries the shipped definition, and new AMD installs no
  longer install the memory-shepherd timers that maintained OpenClaw's
  workspace. `ods start` warns when an `ods-openclaw` container is still
  present.
- Token Spy no longer has a poll-frequency setting
  (`poll_interval_minutes`). It only rewrote the OpenClaw session-cleanup
  timer, which was removed with that extension. The Token Spy dashboard no
  longer shows the field, `/api/settings` no longer reports or stores it, and
  a value saved by an earlier version disappears from `settings.json` on the
  next save. `session-manager.sh` still runs on whatever timer or cron job
  you give it.
- Lemonade Server support is removed; AMD GPUs run on llama.cpp (see Changed
  and [AMD GPUs now run on llama.cpp](docs/MIGRATION-LEMONADE-TO-LLAMACPP.md)).
  - Linux: the locally built `ods-lemonade-server` image
    (`extensions/services/llama-server/Dockerfile.amd`,
    `lemonade-entrypoint.sh`), the external-Lemonade overlay
    `docker-compose.lemonade-external.yml`,
    `scripts/select-external-lemonade-model.py` and the Lemonade LiteLLM
    configs (`config/litellm/lemonade.yaml`,
    `config/litellm/strix-halo-config.yaml`) are removed. Upgrades delete the
    copies you never edited, including an unchanged rendered `lemonade.yaml`,
    and name the edited ones they keep (an edited `lemonade.yaml` in the
    install log).
  - Windows: after `llama-server.exe` has proven its model, setup retires the
    tasks ODS ran Lemonade with (the Portal's `ODSLemonadeRuntime-<SID>`, this
    user's legacy `ODSLemonadeRuntime` and the native installer's
    `ODSLemonadeRuntime`, in their direct, 10.7 wrapper and durable launcher
    forms) and the launcher files ODS wrote, keeps logs, and shows a one-time
    notice. When ODS installed Lemonade Server itself, the notice says so;
    uninstall it from Settings > Apps if you do not use it. ODS never runs the
    Lemonade installer, never touches Lemonade's folders, cache, settings or
    registry, never changes a task it did not write, and never stops a
    Lemonade it cannot prove it started. A task that changed or restarted
    after ODS stopped it stays registered, and setup says so.
  - The `lemonade` mode, the `--use-existing-lemonade` and `--lemonade-*`
    installer options and the Lemonade-era `.env` keys (`LEMONADE_*`,
    `LITELLM_LEMONADE_API_KEY`, `LLAMA_CPP_REF`, `AMDGPU_TARGET`, `HSA_XNACK`)
    are retired. For one release the options still parse and map to
    `--external-llm-*` or `--native-llm-*` with a notice, `ODS_MODE=lemonade`
    reads as `local`, and the keys keep validating. Upgrades rewrite or remove
    them; the Dashboard settings page lists a retired key only when it is
    present, and clearing one removes it.
  - **Adopt loaded model** is removed. The Dashboard API's
    `/api/models/external-observation` and `/api/models/external-adopt` and
    the host agent's `/v1/model/external-observation`,
    `/v1/model/external-adopt` and `/v1/runtime/lemonade/ensure` answer 410
    (`external_lemonade_removed`) for one release.
  - MTP memory fits measured through the Lemonade launch stay recorded but no
    longer qualify a model-store activation; `scripts/qualify-mtp.py` has no
    Lemonade launch mode.
  - The external-Lemonade fleet test (`tests/fleet-external-lemonade-e2e.sh`)
    is removed with the route it exercised. A hosted AMD CPU smoke
    (`.github/workflows/amd-cpu-smoke.yml`) renders the AMD stacks and serves
    a tiny model on the pinned Vulkan image.
- The AMD GAIA library recipe is removed. GAIA's local models need Lemonade
  Server, which ODS no longer runs, so the Extensions page no longer offers
  GAIA.
  - An installed GAIA keeps running until you disable it. ODS no longer
    updates it, and once you stop or disable it the Dashboard cannot start it
    again. Upgraded installs are the exception while they keep the old recipe
    in `data/extensions-library/gaia`, which installer reruns never delete.
    The Extensions page does not list that copy, but a direct
    `POST /api/extensions/gaia/install` still installs GAIA from it.
  - To remove GAIA, disable it on the Extensions page, choose Purge Data if
    you no longer need `data/gaia`, then choose Remove. On Linux,
    `ods disable gaia` and `ods purge gaia` do the first two steps, and
    `ods purge` also deletes files the GAIA container owns. Purge before you
    remove it: afterwards ODS no longer knows `gaia`, and `data/gaia` has to
    be deleted by hand.
  - An `.env` that still sets the `GAIA_*` keys keeps validating. The
    Dashboard settings page lists them only when they are present, and
    clearing one removes it.

### Fixed
- On Windows with an AMD GPU, choosing another model, often the first switch
  after setup, could be refused with "This installation cannot change the
  model runtime on the Windows host right now" while the model kept running.
  The Portal's periodic access check counted as a model operation and
  invalidated the host agent's ownership proof. It no longer does, a switch
  that arrives while that check runs waits up to 30 s for it, and every
  check that cannot be verified is now logged with its cause.
- A long chat message with many unclosed quotes and backslashes no longer
  stalls Pixel chat. pixel-edge masks quoted text before it looks for
  workspace directives, and that step took time quadratic in the message
  length; it is now linear. Quoted text that continues past an escaped line
  break also stays masked now.
- Enabling Token Spy, APE, Privacy Shield or Brave Search on an install that
  started without them no longer fails with "uses a local build without a
  verified source recipe". They build their image from their own folder,
  and the dashboard refused any local build that was not one of two
  reviewed Langfuse Dockerfiles. It now accepts
  these four when their folder matches the files this ODS version shipped,
  pinned by digest; a changed, added or removed file, or a link, is still
  refused. Changing one of these folders needs
  `python3 scripts/pin-builtin-build-contexts.py --write`, and CI fails
  until it is run.
- APE or Token Spy enabled after install on Linux with rootful Docker now
  gets its state folder owned by the container's user before the first start,
  as the installer already does for services enabled at install. APE
  restarted in a loop with "Permission denied: '/data/ape/state.json'".
  An APE container that is already restarting must be stopped (disable it)
  before enabling it again.
- An installer rerun or upgrade no longer stops at once with "Voice, RAG
  documents, and ODS proxy currently require Open WebUI" when voice or RAG
  services were added from Extensions while Open WebUI was off, as on a
  Portal chat install. It keeps that selection and says so; ODS Talk uses
  voice without Open WebUI. A new installation, and the ODS proxy, still
  require Open WebUI.
- On Windows, when a native Windows program already listens on port 9000, an
  install without voice now gives Whisper (STT) a free host port (9100, then
  9001), so Whisper added later from the Extensions Library starts. The
  installer used to move Whisper off 9000 only when voice was selected, and
  Docker Desktop then could not publish the port. A rerun (update) moves a
  9000 written by an earlier installer; a port you set yourself is never
  changed.
- Rerunning the installer on Windows no longer moves a working Whisper (STT)
  off port 9000. Docker Desktop serves Whisper's port through a Windows
  listener, which the installer took for another program. It now checks
  whether that listener is this installation's running Whisper.
- When an extension fails to start, its card shows why instead of "Host
  agent failed to start extension", and the host agent logs the same reason.
  A host port another program holds is named with the `.env` setting that
  moves it, such as `WHISPER_PORT`; other errors show the end of Docker's
  output, where its error is, with credentials removed.
- Uninstalling on Windows (WSL) no longer refuses with "Pixel validation
  failed; nothing was changed" after WSL restarts. WSL attaches its disks in a
  different order on each start, so a completed Pixel update's private scratch
  folder came back under a new device number and failed its identity check.
  Uninstall now accepts exactly that: the same folders, still empty and
  root-private. Anything else still stops the uninstall.
- Turning Hermes off and on again from the Extensions Library after an
  installer update no longer leaves it unable to start on Docker Desktop
  ("error mounting ... cli-config.yaml.example ... no such file or
  directory").
  - The host agent's patch of Hermes's configuration template replaced every
    comment and blank line that followed the compression `context_length`
    with another `context_length` line. Its template never matched the
    installer's, so the next start rewrote it.
  - That rewrite replaced the file. Docker Desktop keeps an existing
    container's single-file mount on the file it replaced, so the Hermes
    container could no longer start.
  - The agent now writes the same template as the installer for the same
    model route, so a start after an update changes nothing. When the
    template must change, the agent updates the file in place. It refuses
    when the file is not a regular file that the ODS user owns.
- Rerunning `install.ps1` on Windows (an update) no longer turns off Hermes
  Agent that was added from the Extensions Library. Windows setup passed
  `--no-hermes` on every run, so the rerun disabled Hermes and its proxy and
  Compose removed both containers. Only a new installation (no `.env` yet)
  gets the flag now; a rerun keeps the current choice, and `-NoHermes` turns
  Hermes off explicitly.
- After an update of a Pixel installation, adding Hermes back from the
  Extensions Library no longer fails with "Host agent failed to start
  extension." The Pixel source update runs as root and set only the owner of
  the files it replaced, so they kept root's group, and the host agent could
  not rewrite Hermes's configuration template. Replaced files and new
  directories now get the owner's primary group, as on a new installation.
- Installations that an earlier Pixel source update already left with files
  in group root are repaired by the next installer run. The installer returns
  its owner's files and folders in `bin`, `lib`, `scripts`, `installers`,
  `extensions` and `vendor` from group root to the owner's group, without
  sudo and without following links, and logs how many it changed.
- A non-interactive rerun on a Tier 0 or Tier 1 machine keeps ComfyUI when it
  is already running (for example after adding it from the Extensions
  Library). Its low-memory safety check now applies only when ComfyUI is not
  installed yet, as the interactive "Keep current selection" already did.
- Updating a Pixel installation that has Hermes on no longer rewrites Hermes's
  configuration template while the Pixel source update is still in progress.
  That update finishes only over the exact files it installed, so the change
  could stop the update. The installer now writes Hermes's model route after
  the Pixel update finishes, still before Hermes starts.
- Rerunning the installer (an update) no longer fails with "Embeddings model
  prefetch failed" after Embeddings was added from Extensions. The Embeddings
  service downloads the model itself, as root, so the installer could not
  write into that cache. The installer now leaves a cache owned by the service
  alone; fresh installs still prefetch the model and still stop if that fails.
- Two defects stopped Pixel's held source upgrade, which installs a new Pixel
  release over an existing one on Linux and WSL. No release has used that path
  yet; its first live run found both.
  - In Sandbox mode, the installer proved access on Pixel's raw candidate
    before ODS's runtime settings were back in place. The proof's command
    wrapper is mounted only by those settings, so it always failed. The proof
    now runs after the settings, and again before admission reopens, as
    intended.
  - With Full Access, the installer rewrote the OpenClaw config with its keys
    sorted even when nothing changed. The upgrade compares the exact bytes it
    recorded at the start, so it refused to continue. The config is now left
    alone when the chat endpoint is already enabled.
  - A held upgrade that failed after its point of no return could only resume
    the same candidate. When that candidate failed every time, the machine
    stayed stuck: Portal was paused, another installer was refused, and so was
    uninstall. A corrected installer for the same update can now take it over
    under the same hold, and the update completes normally. The takeover is
    refused unless the installed tree exactly matches the stuck plan and the
    protected coordinator is intact. A Full Access update whose configuration
    bytes changed still needs manual recovery
    (`docs/pixel/SOURCE-UPGRADE-RECOVERY.md`).
- `ods-uninstall.sh` now validates Pixel before it stops the background model
  upgrade or turns off Windows startup. A Pixel refusal used to leave start-up
  at sign-in disabled; now it changes nothing.
- Adding a bundled extension or Open WebUI from Extensions no longer fails on a slow
  link. A first image download (several GB for Hermes Agent or Open WebUI) ran
  inside a 600-second start allowance, and the Dashboard gave up on adding Open
  WebUI after 180 seconds, while the host kept going and the card lost its Add
  button. The Extensions page now downloads the images first, showing elapsed time
  and completed layers on the card, and enables once they are local. A download
  stops only when Docker makes no progress for 15 minutes (or after 6 hours);
  Library installs download the same way.
- Model compatibility verdicts recorded on named test machines now apply only
  to an install that sets `ODS_FLEET_HOST_ID` or `ODS_COMPATIBILITY_HOST`. The
  Dashboard used to fall back to the computer's own name, so a machine called
  `mac-mini`, `spark` or `windows-laptop` received another machine's verdicts.
- `ods model list` on Linux and WSL now resolves each tier from the installed
  tier map, the same way `ods model swap` does, and marks models that are
  already downloaded. It used to print a hard-coded list that still named
  retired tier models.
- The Linux installer's success card no longer tells you to open
  `http://<your-ip>:3001` from other devices. That port only listens on
  localhost. The card now shows the Dashboard's LAN address
  (`http://<your-ip>:3011`, sign-in required) only when LAN access is
  enabled.
- On openSUSE, the Linux installer now installs Docker and the Compose plugin
  from the distribution's own packages (`zypper install docker
  docker-compose`). It used to pipe get.docker.com, which refuses openSUSE, so
  Docker had to be installed by hand first. get.docker.com remains the
  fallback for the SLES variants it supports.
- The Qwen3.5-2B model used for CPU-only and lightweight installs, and as the
  fast-start bootstrap model, is now downloaded from a fixed Hugging Face
  commit and verified against its SHA-256 on Linux and macOS. The tier maps
  previously fetched it from `resolve/main` with no checksum, and the bootstrap
  checked a hash against a moving branch. The pin matches the catalog entry.
- macOS: the installer refuses to run when the install directory overlaps
  the source checkout (the clone root, an ancestor, or a folder inside it),
  comparing directories by identity rather than spelling. On the default
  case-insensitive APFS volume, the README steps (clone to `~/ODS`, install to
  `~/ods`) previously copied ODS into the clone itself, and uninstall then
  deleted the clone. Running the installer from the install directory, as the
  bootstrap does, is unchanged.
- `get-ods.sh` no longer deletes a directory that has no `.env` unless it is
  empty or still carries the ODS source tree. A `data/` directory kept by
  `ods-uninstall.sh --keep-data`, or an unrelated directory named by
  `ODS_INSTALL_DIR`, now stops the bootstrap with instructions instead of
  being removed by `--force` or a "y" answer. The reinstall prompt now reads
  its answer from the terminal, so it works under `curl | bash`, and stops
  cleanly when no terminal exists. The bootstrap banner no longer claims the
  source is verified.
- Windows `ods.ps1 uninstall` no longer stops at "Docker cleanup is incomplete"
  when a volume or network with the `ods` compose label is not in the saved
  compose files (an older release or a since-disabled extension). It now removes
  every labelled leftover after `compose down`, and a single leftover name is
  passed to `docker` whole instead of one character per argument. If something
  still cannot be removed, the message names it. Uninstall also removes the
  `ODSNativeLlamaRuntime` scheduled task, and a helper task it cannot remove is
  reported with the command to remove it instead of being skipped silently.
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
