# Installer Architecture

The Dream Server installer is modular — 19 installer library modules, the
shared service registry, and 13 ordered phases. This guide is the map for
understanding, changing, and reviewing install behavior without missing a
parallel Linux/macOS/Windows or upgrade-time writer.

For the per-phase ownership and validation contract, see
[INSTALLER_PHASE_CONTRACTS.md](INSTALLER_PHASE_CONTRACTS.md).

## Directory Tree

```
installers/
  lib/                        # Pure libraries — define functions, no side effects
    constants.sh              #   Colors, paths, VERSION, timezone detection
    logging.sh                #   log(), success(), warn(), error(), install_elapsed()
    ui.sh                     #   CRT theme: typing effects, spinners, boot splash, lore
    sudo.sh                   #   privilege escalation helpers
    detection.sh              #   GPU detection, capability profiles, backend contracts, secure boot fix
    host-arch.sh              #   host architecture detection and normalization
    tier-map.sh               #   resolve_tier_config() — tier → model/GGUF/context
    docker-images.sh          #   image pull/build planning
    compose-select.sh         #   resolve_compose_config() — compose overlay files + flags
    compose-failure-report.sh #   useful diagnostics when compose fails
    readiness-summary.sh      #   post-start readiness output
    packaging.sh              #   package-manager abstraction helpers
    python-runtime.sh         #   python/python3 discovery and bootstrap
    progress.sh               #   phase progress reporting
    amd-topo.sh               #   AMD topology helpers
    background-tasks.sh       #   async task helpers
    bootstrap-model.sh        #   bootstrap model selection helpers
    nvidia-topo.sh            #   NVIDIA topology helpers
    path-utils.sh             #   portable path helpers
  phases/                     # Sequential install steps — execute on source
    01-preflight.sh           #   Root/OS/tools checks, existing installation check
    02-detection.sh           #   Hardware detection → tier assignment → compose config
    03-features.sh            #   Interactive feature selection menu
    04-requirements.sh        #   RAM, disk, GPU, and port availability checks
    05-docker.sh              #   Install Docker, Docker Compose, NVIDIA Container Toolkit
    06-directories.sh         #   Create dirs, copy source, generate .env, configure services
    07-devtools.sh            #   Install Claude Code, Codex CLI, OpenCode
    08-images.sh              #   Build image pull list and download all Docker images
    09-offline.sh             #   Configure M1 offline/air-gapped operation
    10-amd-tuning.sh          #   AMD APU sysctl, modprobe, GRUB, and tuned setup
    11-services.sh            #   Download GGUF model, generate models.ini, launch stack
    12-health.sh              #   Verify services responding, configure Perplexica, pre-download STT
    13-summary.sh             #   URLs, desktop shortcut, sidebar pin, summary JSON
install-core.sh               # Orchestrator: trap → source libs → parse args → source phases
lib/service-registry.sh       # Shared service manifest/port registry loaded by installer + CLI
```

## How It Works

**Libraries are safe to source.** Every file in `lib/` defines functions only — no
side effects. Sourcing them loads function definitions and constants into the shell
without executing anything. They must be sourced in order because later libraries
depend on earlier ones (e.g., `logging.sh` uses color codes from `constants.sh`).

**Phases execute immediately when sourced.** Each file in `phases/` is a
self-contained install step that runs its logic the moment `source` evaluates it.
Phases rely on the functions defined by `lib/` and on global variables set by
earlier phases (e.g., phase 04 checks the GPU tier assigned by phase 02).

**The orchestrator is thin.** `install-core.sh` sets up interrupt traps, sources
the library modules and `lib/service-registry.sh`, parses CLI arguments, then
sources the 13 phases in order. All files share one global bash namespace —
everything is sourced, not exec'd.

## File Header Convention

Every module uses a standardized header:

```bash
#!/bin/bash
# ============================================================================
# Dream Server Installer — <Module Name>
# ============================================================================
# Part of: installers/lib/   (or installers/phases/)
# Purpose: <one-line description>
#
# Expects: <comma-separated list of globals/functions this file reads>
# Provides: <comma-separated list of globals/functions this file defines>
#
# Modder notes:
#   <when and why you'd edit this file>
# ============================================================================
```

| Field | Meaning |
|-------|---------|
| **Purpose** | What this file does in one line |
| **Expects** | Globals and functions that must already exist when this file is sourced |
| **Provides** | Globals and functions this file creates for later files to use |
| **Modder notes** | Plain-English hint for customizers |

If you add a new file, copy this template. The `Expects` / `Provides` chain is
how you trace data flow without reading every line.

## Mod Recipes

Common customizations and exactly where to make them:

| Recipe | What to edit | How |
|--------|-------------|-----|
| **Add a hardware tier** | `lib/tier-map.sh` + `lib/detection.sh` | Add a `case` in `resolve_tier_config()` (tier-map.sh) and a detection path in `detection.sh`. Also update `lib/compose-select.sh` if a new compose overlay is needed, and add the tier to `QUICKSTART.md` and `README.md` hardware tables. |
| **Swap CRT theme colors** | `lib/constants.sh` | Change the ANSI escape code variables (`GRN`, `AMB`, `RED`, etc.) near the top |
| **Change lore messages** | `lib/ui.sh` | Edit the `LORE_MESSAGES[]` array — add, remove, or reword entries |
| **Change boot splash** | `lib/ui.sh` | Edit the `show_stranger_boot()` function — it renders the CRT startup sequence |
| **Skip a phase** | `install-core.sh` | Comment out or remove the `source` line for that phase (e.g., remove phase 07 to skip dev tools) |
| **Add a new phase** | `installers/phases/` | Create a numbered `.sh` file with the standard header, then add a `source` line in `install-core.sh` in the right order |
| **Swap inference backend** | `lib/compose-select.sh` | Change the compose overlay logic in `resolve_compose_config()` to point at different compose files |
| **Change model downloads** | `phases/11-services.sh` | Edit the GGUF download logic or add new model files |
| **Add a service health check** | `phases/12-health.sh` | Add a new `check_service()` call for your service |
| **Change minimum requirements** | `phases/04-requirements.sh` | Adjust RAM/disk/VRAM thresholds per tier |

## Generated Config Writers

When a bug involves generated config, check every writer before calling the fix
done. This is the most common way install-time surprises survive a patch.

| Config surface | Linux writer | macOS writer | Windows writer | Upgrade/runtime writer |
|----------------|--------------|--------------|----------------|------------------------|
| `.env` and core ports/secrets | `phases/06-directories.sh` | `installers/macos/lib/env-generator.sh` | `installers/windows/lib/env-generator.ps1` | `dream config`, `dream update`, installer re-runs |
| OpenCode config | `phases/07-devtools.sh` | `installers/macos/install-macos.sh` | `installers/windows/lib/opencode-config.ps1` | `scripts/update-windows-opencode-config.ps1`, `scripts/bootstrap-upgrade.sh` |
| LiteLLM Lemonade config | `phases/06-directories.sh` | n/a | n/a | `scripts/bootstrap-upgrade.sh`, `bin/dream-host-agent.py` |
| Perplexica config | `phases/12-health.sh`, `phases/13-summary.sh` | `installers/macos/lib/env-generator.sh`, `installers/macos/install-macos.sh` | `installers/windows/lib/env-generator.ps1`, `installers/windows/install-windows.ps1` | `scripts/bootstrap-upgrade.sh`, `scripts/repair/repair-perplexica.sh` |
| Hermes config | `phases/11-services.sh`, `scripts/patch-hermes-config.py` | `installers/macos/install-macos.sh` | `installers/windows/phases/06-directories.ps1` | `scripts/bootstrap-upgrade.sh`, `bin/dream-host-agent.py` |

Recent examples: OpenCode on Linux Lemonade mode must use `LITELLM_KEY` because
LiteLLM enforces auth, while direct llama-server paths keep `no-key`; Lemonade
`lemonade.yaml` must preserve `extra_body.chat_template_kwargs.enable_thinking:
false` in install, bootstrap upgrade, and host-agent model activation paths;
Perplexica's persisted `defaultChatModel` must be refreshed after bootstrap
hot-swap.

## Cross-Platform Architecture

What's shared vs platform-specific across the installer:

| Layer | Shared | Platform-specific |
|-------|--------|-------------------|
| Colors, version, paths | `lib/constants.sh` | — |
| Logging | `lib/logging.sh` | — |
| CRT UI / spinners | `lib/ui.sh` | — |
| GPU detection | `lib/detection.sh`, topology helpers | Backend contract JSONs (`config/backends/`) |
| Tier → model mapping | `lib/tier-map.sh` | — |
| Compose selection | `lib/compose-select.sh` | Per-backend compose overlays |
| Package management | `lib/packaging.sh`, `lib/python-runtime.sh` | apt/dnf/pacman/zypper/brew/PowerShell equivalents |
| Pre-flight checks | `phases/01-preflight.sh` | — |
| Docker setup | `phases/05-docker.sh` | NVIDIA Container Toolkit vs ROCm |
| AMD system tuning | — | `phases/10-amd-tuning.sh` (AMD only) |
| Health checks | `phases/12-health.sh` | Port/service differences per backend |

## Testing Your Mods

### Syntax check all installer files

```bash
for f in installers/lib/*.sh installers/phases/*.sh install-core.sh; do
  bash -n "$f"
done
```

If any file has a syntax error, `bash -n` will print the file name and line number.

### Dry-run (no actual installs)

```bash
bash install-core.sh --dry-run --non-interactive --skip-docker --force
```

This walks through every phase, printing what would happen without making changes.

### Smoke tests

```bash
bash tests/smoke/linux-nvidia.sh
bash tests/smoke/linux-amd.sh
bash tests/smoke/wsl-logic.sh
bash tests/smoke/macos-dispatch.sh
```

### Full validation suite

```bash
bash scripts/simulate-installers.sh
bash tests/integration-test.sh
```

## See Also

- [CONTRIBUTING.md](../CONTRIBUTING.md) — Contributor validation checklist
- [EXTENSIONS.md](EXTENSIONS.md) — Adding Docker services (not installer mods)
- [BACKEND-CONTRACT.md](BACKEND-CONTRACT.md) — Backend runtime contract format
- [INSTALLER_PHASE_CONTRACTS.md](INSTALLER_PHASE_CONTRACTS.md) - Phase
  ownership, idempotency, and validation expectations
