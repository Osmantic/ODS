---
title: Pixel CLI - Install, configure, and maintain
doc_type: reference
audience: [owner, operator, contributor, maintainer]
feature_status: mixed
owners: [documentation]
sources_of_truth: [pixel]
generated_by: scripts/docs/generate-cli-reference.mjs
last_verified_at: generated
---

<!-- GENERATED FILE. Run `node scripts/docs/generate.mjs --write`; do not edit by hand. -->

# Install, configure, and maintain

**Status boundary:** Mixed; installed-host state and each command contract control the claim.

| Command | Purpose | Effect | Confirmation | Exit semantics | Normative source |
|---|---|---|---|---|---|
| `./pixel bootstrap` | Verify or install host dependencies (use --apply to install) | state-changing or authority-affecting | mode/subcommand-dependent; inspect help | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/bootstrap.sh`](../../../scripts/bootstrap.sh) |
| `./pixel configure` | Generate .env, gateway environment, and client workspace | writes generated, private, evidence, or bounded task state | not declared by root help; this is not permission to mutate | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/configure.mjs`](../../../scripts/configure.mjs) |
| `./pixel services` | Manage reference SearXNG/model services | may write state; inspect command help and owning guide | mode/subcommand-dependent; inspect help | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/reference-services.sh`](../../../scripts/reference-services.sh) |
| `./pixel plan` | Render, validate, and summarize the proposed deployment | inspection or verification; may emit bounded evidence | mode/subcommand-dependent; inspect help | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/plan.sh`](../../../scripts/plan.sh) |
| `./pixel apply` | Apply the reviewed plan (requires --confirm) | state-changing or authority-affecting | required for the named mutation | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/apply.sh`](../../../scripts/apply.sh) |
| `./pixel verify` | Run post-deployment health and policy checks | writes generated, private, evidence, or bounded task state | not declared by root help; this is not permission to mutate | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/verify.sh`](../../../scripts/verify.sh) |
| `./pixel doctor` | Show rounded local hardware and model-readiness guidance | read-only or inert inspection | none for the named inspection/preview | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/pixel-doctor.py`](../../../scripts/pixel-doctor.py) |
| `./pixel authorize` | Authorize the configured Google Workspace account | may write state; inspect command help and owning guide | mode/subcommand-dependent; inspect help | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/authorize-google.sh`](../../../scripts/authorize-google.sh) |
| `./pixel ui` | Start the loopback-only local onboarding and status page | inspection or verification; may emit bounded evidence | mode/subcommand-dependent; inspect help | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`control/server.py`](../../../control/server.py) |
| `./pixel limbs` | Show enabled/disabled modular capability limbs | read-only or inert inspection | none for the named inspection/preview | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/limbs.sh`](../../../scripts/limbs.sh) |
| `./pixel rollback` | Restore the preceding Pixel release/configuration | state-changing or authority-affecting | mode/subcommand-dependent; inspect help | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/rollback.sh`](../../../scripts/rollback.sh) |
| `./pixel backup` | Create an age-encrypted private-state backup and checksum | writes generated, private, evidence, or bounded task state | not declared by root help; this is not permission to mutate | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/backup-private-state.sh`](../../../scripts/backup-private-state.sh) |
| `./pixel restore` | Validate, rehearse, or restore an age-encrypted private-state backup | state-changing or authority-affecting | mode/subcommand-dependent; inspect help | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/restore-private-state.sh`](../../../scripts/restore-private-state.sh) |
| `./pixel rotate` | Rotate a credential transactionally (gateway is currently supported) | mode-dependent; includes non-mutating and state-writing modes | mode/subcommand-dependent; inspect help | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/rotate-gateway-token.sh`](../../../scripts/rotate-gateway-token.sh) |
| `./pixel package` | Build a versioned, checksummed handoff archive | writes generated, private, evidence, or bounded task state | not declared by root help; this is not permission to mutate | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/package-release.sh`](../../../scripts/package-release.sh) |
| `./pixel test` | Run repository and clean-room workflow tests | writes generated, private, evidence, or bounded task state | not declared by root help; this is not permission to mutate | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`tests/run.sh`](../../../tests/run.sh) |
| `./pixel pressure` | Repeatedly fuzz and exercise Operations safety/capability paths | may write state; inspect command help and owning guide | not declared by root help; this is not permission to mutate | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`security-evals/operations-pressure/run-loop.sh`](../../../security-evals/operations-pressure/run-loop.sh) |
| `./pixel help` | Show this help | read-only or inert inspection | none for the named inspection/preview | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`pixel#usage`](../../../pixel) |

Use the [owning guide](../../operations/runbook.md) before a state-changing command. Return to the [CLI family index](README.md).
