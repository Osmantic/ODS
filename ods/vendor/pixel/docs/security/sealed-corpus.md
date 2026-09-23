---
title: Sealed-corpus custody and evidence
doc_type: concept
audience: [security-reviewer, maintainer, contributor]
feature_status: synthetic-only
owners: [qualification, security]
sources_of_truth: [SEALED-CORPUS-CONTRACT.md, QUALIFICATION.md]
last_verified_at: 2026-08-27
---

# Sealed-corpus custody and evidence

The sealed-corpus contract prevents held-out task content and metadata from influencing tuning. It separates tuning-visible material, reveal custody, authoritative freeze evidence, guardian-controlled materialization, and the later held-out campaign.

## Custody model

| Object | Custody | Visible contents or authority |
|---|---|---|
| Tuning root | Tuning worker namespace | Tuning corpus plus a content-free commitment only |
| Reveal root | Separate owner/guardian namespace and service identity | Opaque task files and exact reveal manifest; never exposed to tuning |
| Commitment | Tuning-visible | Aggregate counts and hashes; no held-out IDs, semantic filenames, profiles, axes, sources, or per-task sizes |
| Authoritative freeze | Produced from the completed, validated tuning campaign | Exact commitment, candidate/campaign identity, materialization and evidence-set bindings |
| Guardian receipt | Owner-private, immutable after reveal validation | Binds commitment, recomputed freeze, campaign/candidate, reveal manifest, and held-out materialization |
| Held-out materialization | Created only after guardian validation | Opaque tasks made available to the held-out campaign, not to tuning |

The freeze is not self-authenticating. Before opening any reveal bytes, the guardian reloads original tuning materialization, campaign output, pair configuration and preflight, recomputes completed comparisons and campaign identity, and requires exact equality with the stored freeze. A schema-valid freeze containing invented evidence must fail before reveal.

## Fail-closed conditions

Reveal is rejected for missing or substituted files, traversal, links, wrong ownership or modes, oversized inputs, duplicate keys or task IDs, candidate or commitment drift, retuning after freeze, mismatched receipts, or any disagreement between the supplied candidate artifacts and the recomputed frozen candidate. Drift requires a new candidate and fresh commitment.

## What the repository sequence proves

The operator sequence in `SEALED-CORPUS-CONTRACT.md` exercises split, tuning materialization, tuning campaign, freeze, guardian materialization, held-out campaign, and validation as a synthetic sealed lifecycle. It does not create the final formal held-out corpus. That corpus remains separately custodied by the owner and must not enter the tuning namespace or repository.

Do not paste corpus content, task identifiers, reveal paths, private receipts, or evaluation prompts into issues or documentation. Retain only content-free provenance and outcome evidence required by policy.

See [evaluations](evaluations.md) for how this lane fits promotion evidence and [assurance](assurance.md) for evidence handling.
