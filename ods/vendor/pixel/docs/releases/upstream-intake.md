---
title: OpenClaw upstream intake
doc_type: how-to
audience: [contributor, security-reviewer, maintainer]
feature_status: supported
owners: [release, security]
sources_of_truth: [UPSTREAM-INTAKE.md, UPSTREAM-RELEASE-CHECKLIST.md, RELEASE-MANIFEST.json, OPENCLAW-COMPATIBILITY.json]
last_verified_at: 2026-08-27
---

# OpenClaw upstream intake

Upstream intake is a quarantine-and-qualification workflow. Discovery observes stable registry channels without modifying the checkout. Preparation downloads exact opaque archives, verifies registry integrity, and records a Candidate combination; it does not install or execute upstream code.

## Workflow

1. Start from a clean candidate branch and run read-only discovery.

   ```bash
   ./pixel upstream check
   ```

2. Choose the tracked stable channel explicitly and prepare into a private quarantine outside the source tree. Preparation may update only the authoritative manifest and compatibility record described in `UPSTREAM-INTAKE.md`.

   ```bash
   ./pixel upstream prepare --channel extended-stable
   ```

3. Review the exact supported-to-candidate contract diff. Every conservative blocker needs an exact, source-bound disposition; the review cannot waive a security finding.

   ```bash
   ./pixel upstream diff
   ```

4. Exercise the candidate only in disposable Linux state. Run both hosted quick lanes and the system-service VM lane. Verify versions, plugin/tool contracts, authentication denial and success, session/workspace restrictions, enabled/disabled limbs, a complete agent tool turn, sandbox confinement, shutdown, and rollback.

5. After the final relevant change, complete the required assurance passes and isolated canary. Assemble and independently verify the exact signed evidence decision described in `UPSTREAM-INTAKE.md`.

6. Only a separately authorized maintainer may promote the exact Candidate. Promotion re-verifies signature and evidence hashes, requires a clean exact source identity, and transactionally regenerates release contracts. Intake, review, CI, canary, and signed evidence do not themselves authorize promotion.

## Stop conditions

Stop on a dirty or drifting checkout; non-stable channel; registry or integrity mismatch; unsafe archive member; private-quarantine failure; contract blocker; source, credential, network, filesystem, tool, or authentication expansion; failed host lane; evidence redaction failure; unhealthy shutdown or rollback; short/non-isolated canary; or mismatched source/evidence identity.

Do not edit a generated contract or delete a blocker to make the gate pass. Repair the source or record a blocked candidate, regenerate, then restart the affected evidence sequence.

Use the authoritative [upstream intake guide](../../UPSTREAM-INTAKE.md) for exact quarantine, matrix, attestation, and promotion arguments. Copy [the release checklist](../../UPSTREAM-RELEASE-CHECKLIST.md) into the candidate review and bind every item to the same commit and candidate manifest.
