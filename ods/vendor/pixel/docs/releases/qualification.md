---
title: Release qualification
doc_type: assurance
audience: [security-reviewer, maintainer, contributor]
feature_status: mixed
owners: [qualification, release, security]
sources_of_truth: [QUALIFICATION.md, QUALIFICATION-MATRIX.json, SECURITY-ASSURANCE.md, RELEASE-MANIFEST.json]
last_verified_at: 2026-08-27
---

# Release qualification

Qualification binds evidence to one exact source commit/tree and candidate artifact set. The generated matrix defines the current host lanes, profiles, required gates, and consecutive-pass policy; this page explains how to read them without copying release-specific values.

## Required evidence families

| Family | Required proof | Common non-proof |
|---|---|---|
| Automated release matrix | Static, unit, security, clean-room lifecycle, plugin integrity, packaging | Authored green tests without an independent run |
| Supported-host system-service matrix | Install/apply/verify, effective isolation, lifecycle, recovery, rollback, removal | Container-only run or source inspection |
| Synthetic endurance | Bounded milestones, dependencies, fresh-process restarts, forced exits, deterministic outcome | Elapsed time, one process, or development-disabled runtime source |
| Recovery/security matrix | Wrong-key/signature/corruption rejection, isolated restore, forced-failure rollback, credential-rotation rollback | Backup creation alone |
| Model-capability contract | Exact model artifact, backend, envelope, usage source, expiry, and task evidence | Model name, estimate, or direct endpoint shortcut |
| Outcome parity | Complete declared paired corpus under the same environment and verifier | A favorable anecdote or incomplete pair set |
| Signed release lifecycle | Qualification signature, candidate execution, activation result, rollback, recovery, cleanup as applicable | Qualification prepare/rehearse receipts that explicitly report no execution |
| Owner usability | Documented owner journey completed and defects dispositioned | Maintainer familiarity |
| Live-provider and multi-provider | Real configured turns with observed usage and correct local-first routing | Fake adapter, model-off response, or network-denied failure |
| History and licensing | Active lineage clean; acknowledged historical refs separated; distribution obligations satisfied | Clean current tree only |

## Qualification-only release operations

The `release-qualification-*` command family operates in an isolated private qualification root. Prepare and rehearse do not execute candidate code. Activation claim and host-run acquisition create custody records; they are not terminal execution evidence. A terminal result is valid only after the separately controlled candidate execution and exact content-free result recording described by `QUALIFICATION.md`.

Use `./pixel help` and the [generated CLI reference](../reference/cli.md) for the current subcommands. Do not improvise receipt fields or reuse a claim after interruption.

## Readiness

```bash
./pixel promotion-status
```

The default status command is fail-closed. A readiness record derives from independently reviewed claims and grants no publication, staging, activation, or production authority. If a gate is absent, mismatched, stale, partial, or synthetic where real evidence is required, report it as blocked or partial rather than filling the gap with prose.

The complete authoritative procedure and evidence schemas remain in `QUALIFICATION.md`. Security test disposition is in [assurance](../security/assurance.md); retained repository evidence is indexed in [release evidence](evidence-index.md).
