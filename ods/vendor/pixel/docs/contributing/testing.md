---
title: Testing Pixel changes
doc_type: reference
audience: [contributor, security-reviewer, maintainer]
feature_status: mixed
owners: [documentation, maintainers, security]
sources_of_truth: [CONTRIBUTING.md, tests/run.sh, tests/static.sh, tests/e2e.sh, security-evals/, QUALIFICATION.md]
last_verified_at: 2026-08-27
---

# Testing Pixel changes

Choose tests by boundary and claim. A green authored suite is necessary evidence for code changes; it is never automatic deployment, qualification, or live acceptance evidence.

## Test layers

| Layer | Typical path | Proves | Does not prove |
|---|---|---|---|
| Syntax/schema/unit | `tests/static.sh`, Node/Python tests | Deterministic parser, schema, state, and fixture behavior | Real services, host isolation, provider/tool usefulness |
| Repository e2e | `tests/e2e.sh` | Clean-room configuration/apply/rollback workflows under fixtures | A client host or live credentials |
| Crash/race/pressure | focused `tests/*crash*`, `*race*`, `security-evals/*pressure*` | Named replay, concurrency, recovery, hostile-input invariants | Absence of unknown attacks |
| Isolation/live harness | `security-evals/*-isolation`, `operations-live`, live test files | Exact boundary on the declared disposable infrastructure | Broader hosts, targets, models, or future commits |
| Supported-host matrix | `qualify-hosts`, supported-host probes | Exact host/systemd acceptance evidence | Other platforms or configurations |
| Provider/model/tool lane | owning live qualification command | One exact configured backend and task contract | General quality, billing authorization, or every capability |
| Release canary | signed activation, real tool turn, rollback, reactivation | Exact release lifecycle on the canary | Publication or all deployments |

## Minimum workflow

1. Add a regression that fails against the defect.
2. Run the smallest direct test during development.
3. Run adjacent schema, failure, replay, and recovery cases.
4. Run `node scripts/docs/check.mjs` when commands, configuration, status, errors, or ownership change.
5. Run `./pixel test` and `./pixel package` on a supported host.
6. Run every focused pressure/race/isolation/live lane required by the [high-risk change map](high-risk-change-map.md).
7. For security changes, run two consecutive clean full passes after the final edit.

## Deep Work and provider evidence

Deep Work lifecycle tests can prove inert preparation, stage, confirmation, idempotency, pause/resume/cancel, checkpoint, service, fleet, and cleanup contracts. They do not enable the development-disabled runtime or prove live autonomy.

Provider/Codex tests can prove wire formats, policy, custody, egress, accounting, and failure classification. Mock/fake/image results are synthetic unless the exact authorized live lane runs. Report them separately.

## Record results

Bind results to exact commit/tree, command, environment, fixture or live lane, timestamps, outcome, and retained evidence. Label skipped, blocked, partial, synthetic, and not-applicable cases. Never place private prompts, transcripts, credentials, policies, host identities, or live paths in Git or a pull request.
