---
title: Evaluation and qualification boundaries
doc_type: concept
audience: [security-reviewer, maintainer, contributor]
feature_status: mixed
owners: [qualification, security, release]
sources_of_truth: [QUALIFICATION.md, SECURITY-ASSURANCE.md, RELEASE-MANIFEST.json, QUALIFICATION-MATRIX.json]
last_verified_at: 2026-08-27
---

# Evaluation and qualification boundaries

Pixel uses several evaluation lanes because no single test environment proves source integrity, operating-system isolation, model behavior, provider behavior, recovery, usability, and promotion readiness at once.

| Lane | Establishes | Does not establish |
|---|---|---|
| Automated host lane | Static, unit, security, and disposable lifecycle behavior on the declared image | Real service-manager semantics, real credentials, real provider/tool behavior |
| System-service host lane | Installation, effective service isolation, lifecycle, crash/restart, recovery, rollback, removal | A real external provider result unless explicitly exercised |
| Event-horizon campaign | Bounded synthetic long-running state and restart behavior under its exact corpus | Product support or an enabled Deep Work runtime |
| Outcome-parity campaign | Deterministic comparison across the complete declared paired corpus and environment | General model superiority or behavior outside the corpus |
| Real-backend qualification | One exact configured provider/model/tool route with observed usage and bounded receipts | Other providers, other models, later drift, update or rollback |
| Signed lifecycle | Candidate verification, activation, tool turn, rollback, and reactivation for the exact artifacts tested | Publication or future-candidate authority |
| Owner acceptance | The owner accepted the observed deployment for its intended use | A transferable claim about another host or client |

## Claim discipline

- A schema proves accepted structure, not correct runtime behavior.
- A signature binds bytes and signer policy, not publication or activation authority.
- A generated readiness record is only as strong as its inputs and exact-head provenance.
- A clean test run is not a live operation.
- A candidate result never silently becomes Supported.
- Deep Work evaluation contracts and synthetic campaigns do not enable its development-disabled runtime.

## Promotion readiness

The authoritative qualification contract defines the required host, recovery, parity, signed-lifecycle, usability, live-backend, historical-secret, license, and other gates. The readiness artifact fails closed when required evidence is absent, stale, mismatched, or incomplete. Publication and production activation remain separate decisions even after every qualification input is green.

Read current machine-derived status in [the generated status page](../status.md), then follow the exact candidate evidence referenced there. Do not infer current readiness from this maintained overview.

For test disposition and evidence custody, see [assurance](assurance.md). For held-out evaluation separation, see [sealed corpus](sealed-corpus.md).
