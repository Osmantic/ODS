---
title: Incident response operator entry point
doc_type: runbook
audience: [owner, operator, security-reviewer, maintainer]
feature_status: supported
owners: [security, operations]
sources_of_truth: [INCIDENT-RESPONSE.md, SECURITY.md, SECURITY-ASSURANCE.md]
last_verified_at: 2026-08-27
---

# Incident response operator entry point

Use the authoritative [incident response procedure](../../INCIDENT-RESPONSE.md) for every real incident. This page helps the operator enter it without confusing routine troubleshooting with containment.

## Enter incident response when

- a credential, private source, client boundary, target identity, signer, or recovery key may be exposed;
- an unauthorized external effect, execution, provider call, listener, tool, or approval occurred;
- a gateway, broker, runner, sandbox, update, rollback, or evidence-integrity boundary may be compromised; or
- installed state cannot be reconciled safely after a security-relevant failure.

## Sequence

1. **Contain.** Stop the affected gateway/courier path; pause Operations and Frontier independently; disable the affected limb. Do not retry an uncertain action.
2. **Classify and rotate.** Identify the breached trust zone, data, identity, authority, and time window. Rotate exposed credentials at their issuers, not only in local files.
3. **Eradicate and recover.** Repair the cause from trusted source, restore through authenticated recovery, and keep unrelated state out of the replacement.
4. **Return to service.** Repeat the focused adversarial test, required full assurance passes, installed-host verification, real-backend/tool checks, and recovery gates before controlled resume.

Preserve a secret-free timeline, hashes, exact source identity, decisions, and observed outcomes in private evidence. Do not attach tokens, prompts, user content, raw source, private paths, host identity, or exploit payloads to public tickets.

For routine non-security failures, use [troubleshooting](troubleshooting.md). For severity and evidence rules, use [security assurance](../security/assurance.md).
