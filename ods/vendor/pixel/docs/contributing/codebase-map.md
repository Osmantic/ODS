---
title: Pixel codebase map
doc_type: reference
audience: [contributor, maintainer, security-reviewer]
feature_status: mixed
owners: [documentation, maintainers]
sources_of_truth: [CONTRIBUTING.md, pixel, control/, deploy/, plugin/, plugin-ops/, plugin-frontier/, scripts/, schemas/, tests/, security-evals/, workspace-template/, .github/workflows/]
last_verified_at: 2026-08-27
---

# Pixel codebase map

Use this map to find the smallest owning surface. The [generated CLI reference](../reference/cli.md) maps every root command to its exact dispatcher target; this page maps those targets to boundaries and test families.

## Entry points and contracts

| Path | Owns | Start tests/review at |
|---|---|---|
| `pixel` | Root command inventory and dispatch | Generated CLI drift, `tests/static.sh`, owning command parser tests |
| `README.md` and root runbooks | Current product/operator contracts and path-sensitive release evidence | Documentation checks plus owning source tests |
| `RELEASE-MANIFEST.json` | Release pins, capability/status and packaging contract | `scripts/check-release-contract.mjs`, release generation/artifact tests |
| `QUALIFICATION-MATRIX.json` | Host/model/profile promotion gates | schema, qualification and supported-host tests |
| `OPENCLAW-COMPATIBILITY.json` | Compatibility status/evidence rows | generator, upstream, release-signing tests |
| `schemas/` | Machine-readable data and receipt contracts | `tests/json-schema.test.mjs` and the owning subsystem tests |

## Product surfaces

| Path | Responsibility | Principal test families |
|---|---|---|
| `control/` | Loopback owner workspace, status, onboarding, chat, fixed actions, authenticated adapter | `test_control_*`, `control-*.test.mjs`, control pressure/boundary evals |
| `plugin/` | Source projection plugin and portal/session boundaries | plugin OAuth/security, source/session/modular tests |
| `plugin-ops/` | Gateway-side typed Operations request surface | Operations broker/runner/live/pressure tests |
| `plugin-frontier/` | Gateway-side typed Frontier request/finalization surface | Frontier broker/isolation/pressure/live qualification tests |
| `workspace-template/` | Agent workspace policy, skills, local scripts, and private-memory boundary | e2e, session, modular and workspace-specific tests |

## Runtime and authority implementations

| Path | Boundary |
|---|---|
| `deploy/source-broker/` | Source credential custody and sanitized projections |
| `deploy/action_journal/` and `deploy/github-broker/` | Idempotent external-action claims, results, and reconciliation |
| `deploy/ops-broker/` and `deploy/ops-runner/` | Operations policy/SSH custody, immutable plans, runners, fixed helpers |
| `deploy/frontier-broker/` | Privacy compiler, provider policy/credential custody, budgets, cache, results |
| `deploy/web-courier/` | Public-web retrieval and SSRF/content boundary |
| `deploy/release-operator/` | Narrow release activation/rollback/verification transport |
| `deploy/sandbox/` | Agent-scoped networkless container image |
| `deploy/work-controller/` | Deep Work inert authoring, lifecycle, capability, knowledge, fleet, and status contracts |
| `deploy/work-broker/`, `deploy/work-runner/`, `deploy/work-research-broker/` | Deep Work compile/runner/research implementation source |
| `deploy/work-provider/`, `deploy/work-provider-router/`, `deploy/work-codex-provider/` | Local/remote provider adapters, custody, routing, qualification, and egress |
| `deploy/agent-comparison/` | Backend-neutral outcome comparison and evaluation support |

Deep Work runtime status is [development-disabled](../status.md). The presence of deploy code and tests does not make those paths active.

## Scripts

`scripts/` contains deployment commands, broker installers, update/recovery transactions, configuration/rendering, release generation, qualification harnesses, and shared secure-file helpers. Start at the command's dispatch line in `pixel`, then follow only its direct script/module and `scripts/lib/` imports.

Important ownership clusters:

- `bootstrap.sh`, `configure.mjs`, `plan.sh`, `apply.sh`, `verify.sh`, `rollback.sh`: base deployment transaction;
- `install-*-broker.sh`, `*-authority.sh`, `approve-*`, `show-*`: isolated limb lifecycle and approvals;
- `backup-private-state.sh`, `restore-private-state.sh`, migration/receipt helpers: recovery;
- `release-update.py` and update wrappers: signed update lifecycle;
- `generate-release-files.mjs`, `check-release-contract.mjs`, package/sign/qualification scripts: release contract;
- `portal_outcome_*`, supported-host, upstream, and work qualification scripts: evaluation evidence, not ordinary runtime.

## Tests and evaluations

- `tests/` contains Node, Python, shell, e2e, live-lane, crash, race, schema, and release tests.
- `security-evals/` contains named adversarial/isolation/pressure/live harnesses. Each proves only its declared boundary.
- `.github/workflows/` runs docs, security, product-qualification, upstream-compatibility, and upstream-discovery jobs.

Use [testing](testing.md) and the [high-risk change map](high-risk-change-map.md) to choose checks.

## Generated, private, and runtime exclusions

Do not hand-edit generated release/docs/configuration output. Never commit `.env`, `.generated`, `.runtime`, `.secrets`, private onboarding, OpenClaw state, broker state, approvals, messages, memory, credentials, host identities, or live transcripts. See [generated files](generated-files.md).
