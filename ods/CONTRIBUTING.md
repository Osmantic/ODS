# Contributing to ODS

ODS is the fight to take AI back from the corporations charging you a subscription to use your own data on their servers. Every PR that lands here puts sovereign AI into someone's hands who didn't have it yesterday. This isn't a startup. This isn't a product. This is a movement — and if you're here, you're already part of it.

## Getting Started

Fork, branch, build, PR. That's it.

```bash
git checkout -b my-change
```

No CLA. No committee. No waiting for permission. If it makes ODS better, send it.

If you're adding or extending services, read these first:
- [docs/EXTENSIONS.md](docs/EXTENSIONS.md) — how to add a new service in 30 minutes
- [docs/INSTALLER-ARCHITECTURE.md](docs/INSTALLER-ARCHITECTURE.md) — how the installer works under the hood
- [docs/DASHBOARD-API-DEVELOPMENT.md](docs/DASHBOARD-API-DEVELOPMENT.md) — how to actually iterate on the dashboard-api FastAPI backend without your changes silently no-op'ing

## Dashboard API development

If you're touching `extensions/services/dashboard-api/`, read [docs/DASHBOARD-API-DEVELOPMENT.md](docs/DASHBOARD-API-DEVELOPMENT.md) **before** you start editing.

The short version: the dashboard-api `Dockerfile` copies the Python source into `/app/` at image build, and uvicorn imports from there. The compose service mounts `./extensions:/ods/extensions:ro` — but that bind-mount is for manifest and config discovery, not Python imports. Editing files under `extensions/services/dashboard-api/` on the host does **not** reload the running container. Your changes silently do nothing until the image is rebuilt.

The recommended workflow is to run uvicorn natively on the host with `--reload`:

```bash
cd ods/extensions/services/dashboard-api
pip install -r requirements.txt
ODS_INSTALL_DIR=/path/to/ods \
  uvicorn main:app --host 127.0.0.1 --port 3002 --reload
```

Other services already reach the host via `host.docker.internal:host-gateway`, which is wired into the dashboard-api compose service, so the rest of the stack keeps working while you iterate. Hot-reload works natively on macOS and Linux; on WSL2, keep the repo on the WSL2 filesystem (not `/mnt/c/...`) for the watcher to behave.

If you can't run native uvicorn for some reason, `docker cp <file> ods-dashboard-api:/app/<file>` followed by `docker compose restart dashboard-api` is a survivable stop-gap until the next image rebuild. The full guide covers why we did **not** ship a bind-mount overlay or a `uvicorn --reload` compose mode as the default.

## What We Care About Right Now

We have a broad contributor base and the number keeps growing. These are the areas where your work hits hardest — and where PRs get merged fastest.

### 1. Runs on anything

A student with a $200 laptop and no GPU should be able to run ODS. So should someone with a 96GB Strix Halo laptop. We don't care if you have a 4090 or a hand-me-down ThinkPad — ODS runs on your machine or we haven't done our job.

Where to help:
- **New hardware tiers** — we have Tier 0 (4GB, no GPU) through Tier 4 (48GB+ VRAM) plus Strix Halo and Intel Arc. If your hardware isn't supported, make it supported.
- **CPU-only inference** — llama.cpp does the heavy lifting, but the installer, memory limits, and model selection all need to work without a GPU.
- **Low-RAM environments** — compose overlays that reduce memory reservations so services fit on constrained machines. See `docker-compose.tier0.yml` for how we did it.
- **ARM, Chromebooks, older GPUs** — if it runs Docker and has 4GB of RAM, we want to support it.

### 2. Clean installs

If someone runs the installer and it doesn't work first try, we failed. Not them — us. Every install failure is a person who might not come back.

Where to help:
- **Idempotent re-runs** — running the installer twice shouldn't break anything. Secrets, configs, and data should survive.
- **Error messages that actually help** — "what went wrong" and "what to do about it." No stack traces. No silent failures.
- **Preflight checks** — catch bad Docker versions, insufficient disk, port conflicts *before* the install starts.
- **The weird edge cases** — WSL2 memory limits, macOS Homebrew paths, Windows Defender, Secure Boot blocking NVIDIA. These are what actually break installs in the real world.
- **Offline installs** — pre-downloaded models, air-gapped environments, corporate firewalls. Real people deal with this.

### 3. Extensions and integrations

An LLM in a terminal is a toy. ODS becomes something people can't live without when it connects to everything they already use. This is how we build the ecosystem that makes sovereign AI actually useful.

Where to help:
- **New services** — wrap any Docker-based tool as a ODS extension. Manifest, compose file, health check — that's it. Look at `extensions/services/` for examples.
- **API bridges** — connect ODS to Slack, Discord, email, calendars, CRMs. n8n workflows are the fastest path.
- **Workflow templates** — pre-built n8n workflows that solve actual problems people have.
- **Manifest quality** — health checks, dependency declarations, port contracts, GPU compatibility. Run `ods audit` to validate yours.
- **Reliability between services** — correct startup ordering, graceful handling of dependencies being temporarily down. The `compose.local.yaml` pattern handles this.

### 4. Tests that catch real bugs

We want tests for code that exists. Not tests for features we haven't built. Not test suites that skip() everything and report "all passed."

Where to help:
- **Installer integration tests** — actually run installer phases in a container and verify the output.
- **Tier map validation** — every tier resolves to the right model, GGUF, URL, and context. See `tests/test-tier-map.sh`.
- **Health checks that verify real behavior** — not just "is a port open" but "does the service actually respond correctly."
- **Extension contract tests** — manifests parse, compose files are valid, ports don't conflict.
- **Platform smoke tests** — scripts parse and core functions work on Linux, macOS, Windows, and WSL2.

### 5. Installer portability

macOS, Linux (Ubuntu, Debian, Arch, Fedora, NixOS), Windows (PowerShell + WSL2). Every platform bug you fix unblocks hundreds of people you'll never meet.

Where to help:
- **POSIX compliance** — BSD sed is not GNU sed. BSD date is not GNU date. If it runs on macOS, don't use GNU-only flags. Use `_sed_i` and `_now_ms`.
- **Package managers** — apt, dnf, pacman, brew, xbps. If your distro isn't supported, add it.
- **Bash compatibility** — macOS ships Bash 3.2. No associative arrays unless you guard for Bash 4+.
- **Path handling** — Windows vs Unix, spaces, symlinks, external drives. Use `path-utils.sh`.
- **Docker flavors** — Docker Desktop, Docker Engine, Podman, Colima. Different sockets, different compose plugins, different permission models.

## Before You Submit

Don't make us find bugs you could have caught. Run this:

```bash
make gate    # lint + test + BATS + smoke + simulate
```

Or if you just want a quick check:

```bash
make lint    # shell syntax + Python compile
make test    # unit + installer + AMD/Lemonade + overlay/secret contract tests
make smoke   # platform smoke tests
```

Touched the frontend? Make sure it builds:
```bash
cd extensions/services/dashboard && npm install && npm run lint && npm run build
```

## What Gets Merged Fast

We merge good work quickly. You'll know it's good if:

- It fixes a real bug and you can show us how to reproduce it
- It tests code that wasn't tested before
- It does exactly one thing and does it well
- It makes ODS run on hardware it didn't before
- It fixes a security hole and explains what was exposed

## What Gets Sent Back

We review a lot of PRs. These patterns waste everyone's time — yours and ours:

- **Bundled PRs.** One PR, one concern. A bug fix + a feature + a refactor = three PRs. Every time.
- **Code that was never run.** If your function is referenced but never defined, or your shell variable won't expand in exec form — we'll catch it. Please catch it first.
- **Breaking changes with no migration path.** Changing port defaults, tightening schemas, broadening volume mounts — these need an issue and a discussion *before* the PR. Existing installs matter.
- **Tests for imaginary features.** A test suite that skip()'s every assertion because the feature doesn't exist yet is worse than no tests — it creates false confidence.
- **Formatting-only PRs.** Running black or prettier across the whole codebase creates merge conflicts for every other contributor and ships zero functionality.
- **Over-engineering.** If the fix is three lines, don't build a framework. We value simple code that works over clever code that impresses.

## Style

We're not precious about style, but we have standards:

- **Bash** — `set -euo pipefail` at the top. Quote your variables. Run `shellcheck`. If it passes, we're happy.
- **Python** — match whatever the file already does. Don't reformat code you didn't change.
- **YAML/JSON** — stable keys, no tabs, don't get creative.
- **Commit messages** — imperative subject ("fix X", not "fixed X"). Body explains *why*, not *what* — we can read the diff.

## Questions and Bugs

**Got a question?** Open an issue or start a [GitHub Discussion](https://github.com/Osmantic/ODS/discussions). Seriously — ask before you build. It's faster for everyone.

**Found a bug?** Open an issue with your hardware (GPU, RAM, OS), what you expected, what actually happened, and logs (`docker compose logs`). The more context you give us, the faster we fix it.

## License

[Apache 2.0](LICENSE). Your code stays open. That's the whole point.
