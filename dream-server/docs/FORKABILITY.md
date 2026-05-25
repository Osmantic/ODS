# Forkability And Independent Operation

Dream Server is Apache-licensed infrastructure. The upstream repository is a
coordination point, not a hosted control plane. Operators should be able to
inspect the rules, run their own node, validate their own hardware, and maintain
their own fork.

This document describes the project posture for downstream maintainers,
hardware builders, labs, schools, companies, and individuals who want to fork,
mirror, audit, customize, or run Dream Server independently.

## Goals

Dream Server should be:

- forkable without asking upstream for permission;
- auditable from a cold clone;
- customizable through documented extension points;
- reproducible from pinned refs and mirrored artifacts;
- validated by repeatable public and private test layers;
- understandable by maintainers who did not write the original installer.

Upstream is one implementation and coordination point. It is not meant to be a
single point of control.

## What To Fork

Fork the repository when you need a durable downstream edition, such as:

- a hardware appliance image with fixed service defaults;
- a lab or school distribution with curated models and workflows;
- a company-local appliance with private extensions;
- a research workstation image with extra tooling;
- an offline or low-connectivity distribution;
- a security-reviewed variant with stricter policies.

For smaller changes, prefer extensions, model catalogs, presets, and docs over
patching installer internals. A small extension is easier to keep current than a
large fork.

## Recommended Fork Strategy

Pick a source ref deliberately. `main` is the active development channel. Tagged
releases are the stable default for forks, appliances, and lab images. If you
need a commit between tags, record the exact commit and its validation receipt.
See [RELEASE_CHANNELS.md](RELEASE_CHANNELS.md) for the channel policy.

Most downstreams should choose one of two patterns:

- **Fork-and-pin:** start from a tagged release or audited commit, apply a small
  downstream layer, and update only on a cadence you control.
- **Fork-and-mirror:** operate your own mirror of the repository and allowed
  artifacts, then merge selected upstream tags or commits after local
  validation.

In both cases, keep your customization layer small and explicit.

Recommended downstream layout:

```text
extensions/services/<your-service>/
extensions/library/services/<your-library-service>/
docs/<your-edition>/
config/<your-edition>/
DOWNSTREAM.md
```

Keep a `DOWNSTREAM.md` in your fork that records:

- upstream commit or release tag last merged;
- changed defaults;
- added, removed, or disabled services;
- hardware assumptions;
- model and image pins;
- private patches to installer, CLI, compose, or dashboard code;
- validation commands and fleet receipts used after each upstream merge.

## What To Avoid Patching First

These areas are powerful but high blast radius:

- `install-core.sh` and `installers/phases/*`;
- `dream-cli`;
- `installers/lib/compose-select.sh`;
- `scripts/resolve-compose-stack.sh`;
- `docker-compose.base.yml` and hardware overlays;
- dashboard-api auth, host-agent, and extension install routes;
- generated config writers for `.env`, LiteLLM, Hermes, OpenCode, and Perplexica.

Patch them when you need to, but do it with the validation map in
[HIGH_RISK_CHANGE_MAP.md](HIGH_RISK_CHANGE_MAP.md).

## Safer Extension Points

Prefer these first:

- `extensions/services/*` for bundled Docker services;
- `extensions/library/services/*` for optional dashboard-installable services;
- `extensions/templates/*` for local starter patterns;
- `config/model-library.json` for model catalog changes;
- `config/ports.json` for deliberate port policy changes;
- `.env.example` for downstream defaults;
- dashboard theme or branding files for a distinct product surface;
- docs under `docs/<your-edition>/`.

The extension path lets forks add value without diverging from upstream
installer and lifecycle behavior.

## Pinning Upstream

For a reproducible fork release:

1. Pick an upstream commit or tag.
2. Record it in `DOWNSTREAM.md`.
3. Record model, image, driver, and package-manager assumptions.
4. Run the validation subset appropriate to your changes.
5. Keep the validation result with the release notes.

Do not build a downstream release from an unnamed local checkout. Future you
should be able to answer exactly what upstream code was used.

## Validation Expectations

At minimum, a downstream fork should run:

```bash
git diff --check
python scripts/audit-extensions.py --project-dir .
python scripts/validate-generated-configs.py
python scripts/validate-golden-paths.py
```

If you touch installer, compose, lifecycle, dashboard-api, model routing, or
service manifests, use [HIGH_RISK_CHANGE_MAP.md](HIGH_RISK_CHANGE_MAP.md) to
choose the stronger validation path.

If you publish a hardware image, keep a release receipt with:

- upstream ref;
- downstream ref;
- install command;
- target hardware;
- enabled services;
- model selected and served;
- validation gates passed, skipped, or deferred.

## Relationship To Upstream

Good fork hygiene helps everyone:

- keep local-only changes documented;
- upstream bug fixes that help the shared platform;
- avoid private patches to generated runtime files when an extension or config
  contract would work;
- report validation gaps when a fork reveals a new hardware or distro class.

Dream Server should be useful whether you stay close to upstream or maintain a
private appliance. The project is healthier when independent operators can own
their stack without needing centralized approval.
