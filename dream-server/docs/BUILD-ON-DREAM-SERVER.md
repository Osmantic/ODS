# Build On Dream Server

This guide is for people who want to fork Dream Server, ship a custom edition,
build a hardware appliance, or add services without fighting the upstream repo.

Dream Server is designed to be extended through isolated service directories,
compose overlays, versioned manifests, and installer libraries. The safest path
is to keep custom work in those extension points and avoid patching generated
runtime files directly.

## What You Can Build

Good downstream shapes include:

- a research workstation with curated models, notebooks, and document tools
- a small-business private AI box with local chat, RAG, search, and workflows
- a school, lab, or nonprofit edition with a fixed service set and onboarding
- a hardware-specific image for NVIDIA, AMD Strix Halo, Apple Silicon, or Intel Arc
- a minimal local chat distribution with most optional services disabled
- a vertical edition with custom n8n workflows, prompts, templates, and services

Start with an extension or overlay when possible. Fork the installer only when
you need to change hardware detection, install phases, generated config, or
platform-specific runtime behavior.

## The Preferred Extension Path

Use this path when you are adding a Docker service, exposing a new feature tile,
or wiring another tool into the local stack.

1. Copy the service templates from `extensions/templates/`.
2. Create `extensions/services/<your-service>/`.
3. Add `manifest.yaml` and `compose.yaml`.
4. Add GPU overlays only when the service needs backend-specific runtime flags.
5. Run the extension audit and compose checks before opening a PR.

Useful starting files:

- `extensions/templates/service-template.yaml`
- `extensions/templates/compose-template.yaml`
- `extensions/templates/compose-gpu-swap.yaml`
- `extensions/templates/compose-gpu-only.yaml`
- `extensions/templates/dashboard-plugin-template.js`

The core contract is simple: a service manifest describes what the service is,
and a compose fragment describes how it runs. The registry, CLI, dashboard,
health checks, and compose resolver discover the service from those files.

## The Fork Path

Use a fork when you want to publish a distinct downstream edition. Keep your
changes easy to rebase by separating them from upstream-owned internals.

Safe places to customize:

- `extensions/services/<custom-service>/` for custom bundled services
- `extensions/library/services/<custom-service>/` for optional catalog services
- `extensions/templates/` for local starter patterns
- `config/model-library.json` for curated model catalogs
- `config/ports.json` for port policy changes
- `.env.example` and docs for downstream defaults
- dashboard branding and theme files when the edition needs a distinct product surface
- installer flags and presets when you need a different default service bundle

Be careful with:

- generated `.env` output
- generated runtime configs for LiteLLM, Hermes, OpenCode, Perplexica, and Lemonade
- platform-specific installer phases
- base compose files shared by every install path
- service IDs, aliases, and ports used by existing manifests

Do not patch files under `data/` as source files. Treat them as runtime state.

## Source Of Truth Map

| Area | Source of truth | Notes |
|------|-----------------|-------|
| Bundled service metadata | `extensions/services/*/manifest.yaml` | IDs, ports, aliases, categories, dependencies, health paths |
| Optional extension catalog | `extensions/library/services/*/manifest.yaml` | Dashboard/library installables |
| Manifest schema | `extensions/schema/service-manifest.v1.json` | Validate fields before relying on them |
| Compose stack | `docker-compose.base.yml` plus overlays and extension compose files | Resolved by installer/compose helper paths |
| Model catalog | `config/model-library.json` | Versioned installable model metadata |
| Linux/macOS model selector | `scripts/select-model.py` | Reads the model catalog and hardware envelope |
| Windows model selector | `installers/windows/lib/tier-map.ps1` | PowerShell selector backed by the same model catalog |
| Hardware tier maps | `installers/lib/tier-map.sh`, `installers/macos/lib/tier-map.sh`, `installers/windows/lib/tier-map.ps1` | Keep platform behavior aligned when changing tiers |
| Generated config contracts | `config/generated-config-contracts.json` | Documents which writers must stay in sync |
| Installer phases | `installers/phases/*`, `installers/macos/install-macos.sh`, `installers/windows/*` | Change only the owning phase/library |
| Docs index | `docs/README.md` | Link new operator/builder docs here |

## Generated Config Rule

If you change a runtime setting, find every writer before shipping. The same
setting may be written during Linux install, macOS install, Windows install,
bootstrap upgrade, and host-agent activation.

Run:

```bash
python scripts/validate-generated-configs.py
```

If the contract fails, update the writer map or the expected generated output
alongside your code change.

## Extension Compatibility Contract

For minor releases, downstream extensions should keep working when they:

- use `schema_version: dream.services.v1`
- keep service IDs unique and lowercase
- declare real health paths and container names
- avoid alias and port collisions
- use `compose_file: compose.yaml` for Docker services
- keep persistent state under `./data/<service-id>/`
- declare required secrets in `service.env_vars`
- declare GPU support through `service.gpu_backends` and compose overlays
- pass `scripts/audit-extensions.py`

Anything that imports private shell functions, mutates generated files directly,
or depends on a specific install phase ordering should be treated as an internal
fork patch and rechecked on every upstream merge.

## Keeping A Fork Rebase-Friendly

Recommended downstream layout:

```text
extensions/services/your-service/
extensions/library/services/your-library-service/
docs/your-edition/
config/your-edition-presets/
```

Recommended maintenance loop:

```bash
git fetch upstream
git switch your-edition-main
git merge upstream/main
python scripts/audit-extensions.py --project-dir .
python scripts/validate-generated-configs.py
python scripts/validate-golden-paths.py
git diff --check
```

For larger forks, keep a `DOWNSTREAM.md` in your fork that lists:

- changed defaults
- added services
- removed services
- hardware assumptions
- files intentionally patched from upstream
- validation commands used after each upstream merge

## Validation Checklist

For docs-only or template changes:

```bash
git diff --check
python scripts/audit-extensions.py --project-dir .
```

For a new service extension:

```bash
python scripts/audit-extensions.py --project-dir . <service-id>
python scripts/audit-extensions.py --project-dir .
docker compose -f docker-compose.base.yml -f extensions/services/<service-id>/compose.yaml config
```

For generated config or installer behavior:

```bash
python scripts/validate-generated-configs.py
python scripts/validate-golden-paths.py
```

For dashboard-facing behavior, also run the dashboard API and frontend tests
from their service directories.

## Example Downstream Editions

Research workstation:

- enable Open WebUI, LiteLLM, SearXNG, Qdrant, embeddings, Jupyter, and n8n
- add notebook and paper-ingestion extensions
- document model and storage requirements in `docs/your-edition/`

Small-business private AI:

- keep Hermes, RAG, search, backups, and dashboard health checks prominent
- add prebuilt n8n workflows for email, CRM, and document handling
- keep LAN exposure and invite flows documented

Hardware appliance:

- pin a known hardware class
- keep a dated validation receipt for installer runs
- document selected model, backend, driver versions, and expected ports

Minimal local chat:

- keep only core inference, Open WebUI, dashboard, and model management
- disable optional services by default
- keep the fork small and easy to merge upstream

## PR Expectations For Builder Changes

A good builder-facing PR should include:

- the user story: who is building on Dream Server and what becomes easier
- the files downstream authors should copy or edit
- validation commands and their results
- any compatibility promises or limits
- links from `docs/README.md` and relevant feature docs

When in doubt, add a template or example rather than another abstract paragraph.
