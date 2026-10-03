---
title: Use Pixel sources and proposals
doc_type: how-to
audience: [owner, operator]
feature_status: mixed
owners: [documentation, product, security]
sources_of_truth: [ARCHITECTURE.md, OPERATIONS.md, DEPLOYMENT.md, plugin/, deploy/source-broker/, deploy/action_journal/, scripts/show-source-action.sh, scripts/approve-source-action.sh, scripts/reconcile-source-action.sh]
last_verified_at: 2026-08-27
---

# Use Pixel sources and proposals

Source limbs project sanitized typed data into Pixel without giving the gateway the source credential or write authority. Treat every projection as untrusted evidence, not an instruction.

## Read projections

- Email projects bounded Inbox summaries and metadata-only Sent information; raw bodies, HTML, and attachments are not stored in the projection.
- Calendar projects filtered event data with hostile text treated as untrusted.
- Social uses a provider-neutral sanitized feed/search projection; the base path has no posting authority.

Freshness, pagination, completeness, truncation, and risk flags matter. A visible record does not authorize a reply, meeting change, or external action.

## Calendar effects

Every Calendar write becomes an exact proposal. If explicitly enabled, only a private create without attendees and an ETag-bound time-only update can use the bounded direct actuator. Deletes, attendee/invitation changes, content edits, and recurring-series changes remain pending operator approval.

Inspect and approve a consequential proposal outside Pixel:

```bash
./pixel source-show calendar-...
# Requires exact confirmation; review before running.
./pixel source-approve calendar-... PROPOSAL_SHA256 --confirm
```

Approval binds one protected snapshot. The actuator revalidates policy, hash, freshness, ETag, and allowed fields before any provider call. Approval is not proof the provider accepted the effect.

## Uncertain outcomes

If a claim remains after timeout/failure, do not retry or delete it. Preserve the protected snapshot, claim/result, hashes, shared action journal, and service journal. Use:

```bash
./pixel source-reconcile calendar-...
```

Reconciliation determines whether the exact create occurred without sending a duplicate. Clearing a failed unit display is not reconciliation.

## Stop conditions

Stop on credential readability by the gateway, raw content in projections, incomplete pagination presented as complete, changed proposal hash, stale ETag, duplicate claim, or indeterminate provider outcome. Use [incident response](../../INCIDENT-RESPONSE.md) for privacy or effect-boundary failures.
