---
title: Pixel status and evidence
doc_type: concept
audience: [owner, operator, contributor, security-reviewer, maintainer]
feature_status: mixed
owners: [documentation]
sources_of_truth: [README.md, QUALIFICATION.md, RELEASE-MANIFEST.json, QUALIFICATION-MATRIX.json, OPENCLAW-COMPATIBILITY.json, DEPLOYMENT.md, CONTROL-SURFACE.md, schemas/runtime-attestation-v1.schema.json, schemas/control-chat-v1.schema.json]
last_verified_at: 2026-08-27
---

# Pixel status and evidence

Pixel has several evidence planes. A fact in one plane must not be promoted into a stronger claim in another.

## The evidence ladder

| Evidence | What it can prove | What it cannot prove by itself |
|---|---|---|
| Source is present | A path and implementation exist at an exact commit/tree | Merged, enabled, deployable, safe, or useful behavior |
| Authored tests pass | The tested fixtures behaved as asserted | Independent qualification, clean-host use, or live behavior |
| Synthetic qualification passes | A bounded synthetic contract passed | Real provider/model/tool behavior or owner usefulness |
| Release contract is coherent | Version, manifest, matrix, compatibility, and evidence references agree | Publication, installation, activation, or live acceptance |
| Runtime verification passes | Installed bytes, source identity, active configuration, services, connectors, and gateway match the attestation contract | Model quality, recovery, provider capability, or client acceptance |
| Real model/tool turn succeeds | The exact configured backend produced the observed bounded result | General capability, future reliability, or authorization for other effects |
| Recovery rehearsal passes | A specific signed encrypted backup restored and verified in isolation | Every future archive or a live restore decision |
| Owner accepts | The named owner reviewed the applicable evidence and residual limits | A broader support promise than the underlying evidence allows |

## Repository status versus host status

[Current repository status](../status.md) is generated from version, release manifest, qualification matrix, and compatibility records. It reports facts about this checkout. It explicitly does not say what is installed on a host.

Installed-host status comes from the host's active immutable release, generated configuration, services, projections, and fresh runtime attestation. Always refresh host evidence before a current claim. A repository Candidate does not become Supported because it was locally installed, and a Supported repository row does not prove a particular host is healthy now.

## Common status words

- **Supported**: the exact compatibility and qualification contract assigns this status.
- **Candidate**: evidence may be assembled for review, but merge, publication, installation, activation, and live acceptance remain separate.
- **Development-disabled**: inspectable source may exist, but runtime authority is deliberately absent.
- **Synthetic-only**: the named synthetic path passed; no real backend is implied.
- **Historical**: the record describes an earlier state and must not be read as current.
- **Prepared**: configuration or an inert candidate exists. It is not active.
- **Active**: fresh exact runtime evidence for the named surface is currently verified. It is not a universal health claim.
- **Unavailable**: Pixel could not verify the evidence safely. Missing data is not converted into a healthy or disabled state.

## Receipts are bounded claims

A Pixel receipt is useful because its schema says exactly what it binds. For example:

- a runtime attestation binds the installed manifest, source, active generated configuration, profiles, checks, and hashed model identity;
- a chat model receipt binds configured and launcher-reported model identities plus bounded accounting;
- a capability or broker receipt records content-free classifications and outcomes without exposing arguments or private result content;
- update, activation, rollback, backup, and recovery receipts bind their own exact lifecycle steps.

No receipt grants authority merely by existing. Read its boundary, freshness rule, exact input hashes, and failure states. Missing, stale, malformed, substituted, or contradictory evidence should fail closed.

## How to make an operational claim

For any claim such as “Pixel is active,” “the model works,” or “recovery is ready”:

1. Name the exact surface and host.
2. Name the evidence plane required for that claim.
3. Refresh the evidence instead of relying on a screenshot or old receipt.
4. Record exact source/release identity and the relevant receipt or drill.
5. Label partial, blocked, synthetic, historical, and not-applicable evidence separately.
6. State what the evidence does not prove.

Examples:

- “Runtime verification passed on this host at this time” is narrower and more accurate than an unbounded readiness claim.
- “A real configured local model turn succeeded and the operator accepted its answer” is stronger than “the chat tests pass,” but it still does not qualify every task.
- “A signed archive was created” is not the same as “recovery was rehearsed successfully.”

## Where to look next

- [Repository status](../status.md)
- [Qualification contract](../../QUALIFICATION.md)
- [Deployment verification](../../DEPLOYMENT.md)
- [First real conversation](../use/first-conversation.md)
- [Acceptance checklist](../../ACCEPTANCE-CHECKLIST.md)
- [Upgrade and rollback](../../UPGRADE.md)
