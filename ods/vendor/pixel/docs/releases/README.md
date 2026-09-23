---
title: Releases and compatibility
doc_type: concept
audience: [owner, operator, contributor, security-reviewer, maintainer]
feature_status: mixed
owners: [release, documentation]
sources_of_truth: [RELEASE-MANIFEST.json, OPENCLAW-COMPATIBILITY.json, QUALIFICATION.md, UPGRADE.md, scripts/package-release.sh]
last_verified_at: 2026-08-27
---

# Releases and compatibility

A Pixel release is an exact source tree packaged into checksummed artifacts and bound to a generated manifest, SBOM, provenance statement, update envelope, and detached signatures. A release file is not a deployment, and a signature is not activation authority.

## Release claim chain

| Stage | Establishes | Does not establish |
|---|---|---|
| Source commit/tree | Exact reviewed bytes | Generated files, tests, packaging, or runtime behavior |
| Generated release contracts | Internal version, dependency, schema, and policy consistency | A clean qualification run |
| Package | Deterministic archive, checksums, SBOM, provenance, update envelope | Trusted signature or host eligibility |
| Qualification signature | Candidate identity accepted for the isolated qualification workflow | Publication, production staging, activation, or compatibility promotion |
| Qualification gates | Exact automated, host, recovery, provider, and usability evidence named by policy | A different commit, host, provider, or future state |
| Release signature | Exact bundle verifies against a trusted publisher identity | Operator approval to stage or activate |
| Update rehearsal | Candidate extracts and validates without executing candidate code | Successful activation or tool behavior |
| Activation and real tool turn | Exact candidate ran on the observed host and completed the tested turn | Rollback, reactivation, or another host |
| Rollback and reactivation | Exact lifecycle recovered and returned to the candidate | Publication or owner acceptance |
| Compatibility row | Repository-recorded Candidate, Supported, retired, or blocked status | Current installed-host truth |

Current repository-derived facts are in [Pixel status](../status.md). Every retained compatibility row and audit link is in the generated [release evidence index](evidence-index.md).

## Artifact set

`./pixel package` calls the Linux-only deterministic packager. It requires a clean worktree and agreement between `VERSION` and `RELEASE-MANIFEST.json`. The package step produces the source archive, checksums, SBOM, provenance, and update envelope in `dist/`; it refuses to overwrite an existing release signature for the same version.

```bash
./pixel package
```

Packaging uses committed source only. Runtime state, credentials, generated private configuration, workspaces, and evidence directories do not belong in the archive.

## Maintainer paths

- [Maintainer runbook](maintainer-runbook.md): exact-head review through signed activation, rollback, and reactivation.
- [Qualification](qualification.md): required lanes and evidence boundaries.
- [Upstream intake](upstream-intake.md): discover and qualify a new OpenClaw/plugin combination.
- [Failure guide](failure-guide.md): fail-closed triage without bypassing a gate.
- [Operator update and rollback](../operations/update-and-rollback.md): consume a previously signed release on an installed host.

Signing, compatibility promotion, publishing, staging, and activation are state-changing authorities. Documentation generation and package inspection do not authorize them.
