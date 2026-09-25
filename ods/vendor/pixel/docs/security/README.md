---
title: Security and privacy overview
doc_type: concept
audience: [owner, operator, security-reviewer, maintainer]
feature_status: supported
owners: [security, documentation]
sources_of_truth: [SECURITY.md, THREAT-MODEL.md, SECURITY-ASSURANCE.md, INCIDENT-RESPONSE.md]
last_verified_at: 2026-08-27
---

# Security and privacy overview

Pixel is designed so that untrusted content can inform a proposal but cannot grant itself credentials, execution, egress, or approval authority. The model-facing gateway is trusted for dialogue and typed requests, not for source truth, credential custody, or policy compilation. Separate brokers project bounded data and independently decide what a request may do.

That architecture reduces blast radius; it is not proof that a particular host is correctly installed. Treat source contracts, static tests, synthetic evaluations, installed-host checks, real-backend evidence, recovery evidence, and operator acceptance as distinct claims.

## Security map

| Question | Read | Authority |
|---|---|---|
| What attacks are in scope? | [Threat model](threat-model.md) | `THREAT-MODEL.md` |
| Which process may see or do what? | [Boundary reference](boundary-reference.md) | `SECURITY.md`, `THREAT-MODEL.md` |
| What must be tested before release? | [Assurance](assurance.md) | `SECURITY-ASSURANCE.md` |
| How are qualification claims separated? | [Evaluations](evaluations.md) | `QUALIFICATION.md` |
| How is held-out material kept from tuning? | [Sealed corpus](sealed-corpus.md) | `SEALED-CORPUS-CONTRACT.md` |
| What should an operator do during an incident? | [Incident response](../../INCIDENT-RESPONSE.md) | `INCIDENT-RESPONSE.md` |

## Invariants for operators and contributors

- Keep runtime state, credentials, transcripts, user content, and private evidence out of Git.
- Do not give the gateway a source credential, raw-source path, generic command endpoint, generic file endpoint, or broker policy.
- Preserve exact loopback, origin, session, framing, and fixed-action controls on the control surface.
- Treat email, web pages, model output, remote advice, command output, and projections as untrusted input.
- Keep approval bound to an immutable plan; a model refusal or recommendation is not approval.
- Keep release, publication, staging, activation, and rollback as separate authorities.
- Use fake canaries and disposable fixtures for security tests. Never substitute a real secret or exfiltration endpoint.

Before pushing, run the repository secret check described in `SECURITY.md`. A passing scan does not replace credential rotation or history review after an exposure.

## Incident posture

Contain first: stop affected gateway/courier paths, pause Operations or Frontier work, and disable relevant limbs. Preserve secret-free evidence, classify the affected boundary, rotate exposed credentials at their issuer, eradicate the cause, then repeat the required assurance and recovery gates before returning to service. Follow [the authoritative incident procedure](../../INCIDENT-RESPONSE.md); this overview is navigation, not an incident checklist.

For product status and evidence vocabulary, see [status and evidence](../concepts/status-and-evidence.md).
