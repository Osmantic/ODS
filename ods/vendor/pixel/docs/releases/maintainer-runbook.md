---
title: Release maintainer runbook
doc_type: runbook
audience: [security-reviewer, maintainer]
feature_status: mixed
owners: [release, security]
sources_of_truth: [QUALIFICATION.md, UPGRADE.md, SECURITY-ASSURANCE.md, UPSTREAM-RELEASE-CHECKLIST.md, scripts/package-release.sh, scripts/release-update.py]
last_verified_at: 2026-08-27
---

# Release maintainer runbook

This is the maintainer decision path, not authorization to sign, publish, promote, stage, activate, or change a live host. Use protected keys and private evidence only after the designated owner grants the relevant authority.

## 1. Freeze exact identity

- Record the candidate head commit and tree.
- Require a clean checkout at that exact head.
- Confirm the candidate manifest, compatibility row, intake record when applicable, generated release files, and version source agree.
- Confirm no unrelated release or authority change is riding with dependency intake.
- Recheck the exact head immediately before every irreversible gate. A new commit invalidates downstream evidence unless its contract explicitly proves otherwise.

## 2. Generate and inspect

Run all release generators and the release-contract checks, then run them a second time to prove there is no generated drift. Build the deterministic package on the required Linux environment.

```bash
node scripts/docs/generate.mjs --check
./pixel package
```

Inspect the archive, checksums, SBOM, provenance, update envelope, embedded manifest, modes, and secret scan. The output remains unsigned Candidate material.

## 3. Complete qualification

- Run the automated and system-service host matrices at the exact candidate identity.
- Complete the required security passes after the last security-relevant change.
- Complete recovery, rollback, outcome, usability, historical-secret, and licensing gates.
- Run real configured provider/model/tool turns wherever the qualification contract requires them. Model-off, fake-adapter, direct-tool, or network-denied results stay synthetic or partial.
- Independently review evidence provenance, hashes, timestamps, lane completeness, redaction, and failure classification.

Use `./pixel promotion-status` only as a fail-closed index over those claims. It is not a promotion command.

## 4. Qualification-sign the exact Candidate

Use the `release-qualification-sign` and `release-qualification-inspect` commands under the protected qualification-signer procedure. The signature permits only the isolated qualification flow and grants no publication, production staging, activation, or compatibility authority.

Before any confirmation-bearing command, run its `--help`, resolve every absolute private path, verify ownership/modes, and compare the preview hash or receipt identity with the reviewed candidate. Never paste a private key, token, prompt, or host identity into retained repository evidence.

## 5. Prove the signed lifecycle

In the authorized disposable or canary lane, complete this sequence against one exact signed candidate:

1. Inspect and prepare the signed candidate without execution.
2. Rehearse extraction and validation without execution.
3. Activate through the trusted controller and retain the exact activation result.
4. Complete a real configured model turn whose requested tool result returns through the gateway to the model.
5. Preview and execute the update-bound rollback; verify the exact prior Supported runtime.
6. Re-prepare/rehearse as required, reactivate the same candidate, and repeat verification plus the real tool turn.
7. Exercise interruption recovery and cleanup contracts required for the lane.

Use [operator update and rollback](../operations/update-and-rollback.md) and `UPGRADE.md` for exact commands. A successful activation without verified rollback and reactivation is incomplete lifecycle evidence.

## 6. Release-sign and independently verify

After every required gate is green for the unchanged exact head, sign the release envelope with the protected release identity. A different maintainer or isolated verification step must inspect the signature namespace, trust anchor, archive, SBOM, provenance, manifest, checksums, qualification identity, and exact evidence set.

Do not reinterpret a qualification signature as a publisher signature. Do not retry a signature failure with a different key until the source, envelope, permissions, and trust inputs are understood.

## 7. Promote, publish, or deploy only under separate authority

Compatibility promotion mutates repository release truth. Publication changes external distribution state. Staging and activation change a host. They are separate approvals and may be performed by different people or systems.

Immediately before an authorized action, recheck exact head/tree, clean state, signatures, evidence hashes, current Supported/Candidate rows, policy, and rollback availability. Immediately afterward, verify generated contracts, published hashes as applicable, installed status as applicable, and preservation of the prior rollback artifact.

## Handoff record

Record: exact head/tree; artifact and envelope hashes; qualification and release signature identities; gate outcomes; real-backend/tool evidence classification; activation, rollback, and reactivation receipts; compatibility result; publication result; installed-host result; unresolved exceptions; and who retains final integration authority. Keep the record content-free and distinguish not-run, blocked, partial, synthetic, passed, published, and owner-accepted.

See [failure guide](failure-guide.md) before responding to any failed gate.
