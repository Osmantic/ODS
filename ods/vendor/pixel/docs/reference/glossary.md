---
title: Pixel glossary
doc_type: reference
audience: [owner, operator, contributor, security-reviewer, maintainer]
feature_status: mixed
owners: [documentation]
sources_of_truth: [ARCHITECTURE.md, THREAT-MODEL.md, OPERATIONS-LIMB.md, FRONTIER-LIMB.md, DEEP-WORK.md, QUALIFICATION.md]
last_verified_at: 2026-08-27
---

# Pixel glossary

| Term | Meaning |
|---|---|
| Acceptance | A named owner or independent verifier's decision that applicable evidence and residual limits satisfy a declared criterion. It is not inferred from an agent answer. |
| Activation | The confirmed transition that makes a reviewed deployment or release candidate active. Preparation, rehearsal, and signature inspection are not activation. |
| Actuator | A narrowly scoped service that applies an approved or explicitly bounded effect, such as one Calendar mutation. |
| Approval | Fresh operator authorization for one exact reviewed object and boundary. It is not a generic grant or proof of execution. |
| Broker | A separately isolated service that owns policy, credential, authority, or private state for one capability plane. |
| Capsule | A bounded typed payload, often privacy-compiled, that crosses a specific review/provider boundary. It is not a generic prompt or file forwarder. |
| Capability | A named action or tool surface admitted by configuration and policy. Presence does not prove enablement or health. |
| Checkpoint | Durable, hash-bound progress and verification state for one bounded work context. It does not widen scope. |
| Claim | Durable evidence created before a path takes responsibility for a possible effect. A claim is not success. |
| Content-free | A projection or receipt that omits designated private content and exposes bounded state/hashes/counters. It may still be sensitive operational evidence. |
| Custody | Which identity owns, can read, can replace, or can authorize a private object throughout its lifecycle. |
| Evidence | An artifact or observation that proves only the facts in its exact contract and scope. |
| Frontier | Optional typed expert-review plane with privacy compilation, budgets, separate credential custody, and exact approval boundaries. |
| Gateway | The unprivileged OpenClaw runtime hosting the configured Pixel conversational agent and approved plugins. |
| Grant | Policy-scoped standing authority for named actions, targets, environments, parameters, and budgets. |
| Lease | Expiring, constrained delegated authority for one bounded context. Consumption, expiry, or revocation prevents replay. |
| Limb | Independently selected capability module, such as source, web, Operations, or Frontier. |
| Plan | Immutable review object describing an intended deployment or effect. A plan is not approval or execution. |
| Prepared | Coherent configuration or an inert candidate exists, while activation or another required boundary remains. |
| Projection | Sanitized typed data made readable across a one-way capability boundary without transferring source credentials or write authority. |
| Proposal | Untrusted typed request for a possible effect. It must still pass policy, review, approval, and execution checks as applicable. |
| Qualification | Evidence that a named system passed declared gates under an exact environment and identity. It does not generalize beyond that scope. |
| Receipt | Schema-bound record of exact identities, hashes, counters, states, or outcomes. It grants no authority merely by existing. |
| Reconciliation | Determining the actual outcome of an uncertain claimed effect before retry, rollback, or closure. |
| Runtime attestation | Fresh installed-host evidence binding the active release, source/configuration identity, and verification checks. It does not prove model quality. |
| Supported | Status assigned by the exact compatibility and qualification contract for a named scope. Source presence or test success cannot assign it. |

See [architecture](../concepts/architecture-overview.md), [trust and authority](../concepts/trust-authority-and-evidence.md), and [status](../concepts/status-and-evidence.md).
