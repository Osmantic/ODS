# High-Risk Change Map

This map turns maintainer intuition into review policy. Use it to decide what
validation is required before merging a change.

The goal is not to slow small work down. The goal is to make high-impact areas
obvious to contributors, fork operators, and auditors.

## Risk Levels

| Level | Meaning | Typical validation |
|---|---|---|
| Low | Cannot affect installed runtime behavior | `git diff --check`, link/doc sanity |
| Medium | Affects one service, UI flow, or contract | Focused tests plus relevant build/lint |
| High | Affects install, lifecycle, auth, compose, model routing, or host mutation | Focused tests plus release-grade or hardware-backed validation |

## Change Map

| Area | Risk | Why | Required validation |
|---|---|---|---|
| Docs only | Low | No runtime impact | `git diff --check`; markdown/link sanity when available |
| Extension manifest metadata | Medium | Can affect dashboard, CLI, health, and compose discovery | `python scripts/audit-extensions.py --project-dir .` |
| New or changed service compose | High | Can break stack resolution, ports, health, and dependencies | Extension audit, compose config, resolver checks, install smoke |
| `install-core.sh` | High | Orchestrates every Linux install phase | Installer syntax, golden paths, release-grade install/lifecycle |
| `installers/phases/*` | High | Mutates host, config, services, models, or health state | Phase syntax, generated config tests if applicable, release-grade lane for operational changes |
| Installer libraries | Medium/High | Shared by multiple phases and platform paths | Focused unit/contract tests plus install smoke when behavior changes |
| Windows installer | High | Separate shell/runtime semantics and Docker Desktop assumptions | PowerShell lint/tests, Windows smoke, fleet Windows lane when available |
| macOS installer | High | Native Metal llama-server plus Docker services | macOS smoke, lifecycle, model swap when applicable |
| `ods-cli` | High | User lifecycle, config, restart, backup, mode, and extension control | CLI smoke, `ods restart`, `ods doctor`, lifecycle lane; use [ODS_CLI_DECOMPOSITION.md](ODS_CLI_DECOMPOSITION.md) for behavior-preserving split work |
| Compose resolver | High | Determines actual runtime stack | Resolver tests, compose matrix, distro smoke, fleet if operational |
| Base or hardware compose overlays | High | Can break every install in a hardware class | Compose matrix plus matching real-hardware lane |
| Dashboard UI | Medium | Operator workflows and setup visibility | `npm test`, `npm run lint`, `npm run build`, Playwright smoke for visible flows |
| Dashboard API auth/setup/host-agent | High | Protected control plane and host mutation boundary | Focused pytest, security tests, dashboard API smoke |
| Dashboard API status/read-only routes | Medium | Can affect UI and diagnostics | Focused pytest and dashboard build/test |
| Hermes, LiteLLM, model routing | High | Affects agent behavior and inference path | Agent seed test, model identity, capability probes |
| Model catalog or selector | High | Determines first-run user experience and memory fit | Selector tests, hardware truth table review, model swap smoke |
| Network binding/proxy routes | High | Affects LAN exposure and security posture | Network exposure contracts, auth checks, owner-card/Talk lane when enabled |
| Dependency updates | Medium/High | Risk depends on runtime surface | Package tests plus service startup smoke; release-grade if installer/runtime wiring changes |
| CI-only workflow changes | Low/Medium | Does not affect users but can hide failures | Workflow review and green checks |

## Release-Grade Trigger

Run the release-grade fleet or an explicitly scoped equivalent when a change can
affect:

- clean install;
- zero-prereq bootstrap;
- compose stack generation;
- lifecycle recovery;
- service health;
- model download/swap;
- dashboard API control flows;
- Hermes or capability probes;
- host mutation;
- LAN/proxy exposure.

When a full fleet run is not practical, record the narrower validation and the
reason it is enough.

## PR Body Checklist

For medium or high-risk PRs, include:

- changed surface;
- user impact;
- validation commands and results;
- skipped or deferred lanes;
- rollback strategy if the change is risky;
- whether a full fleet rerun is required before release.

For docs-only PRs, say that no runtime behavior changed.
