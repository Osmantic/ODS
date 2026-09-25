---
title: Trust, authority, and evidence
doc_type: concept
audience: [owner, operator, contributor, security-reviewer, maintainer]
feature_status: mixed
owners: [documentation, security]
sources_of_truth: [ARCHITECTURE.md, THREAT-MODEL.md, OPERATIONS-LIMB.md, FRONTIER-LIMB.md, DEEP-WORK.md, schemas/runtime-attestation-v1.schema.json]
last_verified_at: 2026-08-27
---

# Trust, authority, and evidence

Pixel separates four questions that agent systems often blur:

1. Who or what is trusted to hold a credential?
2. What exact authority exists right now?
3. What effect was proposed or executed?
4. What evidence proves the observed state?

## Identities and credential custody

The deployment owner, gateway, Source Broker, Operations Broker, Frontier Broker, dedicated runners, and external providers have different identities. A component receives only the files and flows required for its role. The gateway cannot read broker credentials merely because it can submit a typed request.

File ownership is a software boundary, not protection from root, the kernel, or compromise of the owning broker identity.

## Proposal is not approval

A model or plugin can create a typed proposal. That proposal is untrusted input until the owning broker revalidates its schema, policy, identity, freshness, and exact hash. Where approval is required, the owner inspects one immutable object and confirms its unpredictable hash-bound phrase or digest through the trusted terminal.

Editing, expiring, substituting, cancelling, or replaying the proposal invalidates approval.

## Approval is not execution

Approval permits only the exact declared boundary. The actuator or broker still checks current policy, budgets, target identity, conflicts, claims, and replay protection before an effect. A successful human confirmation can still be followed by a safe rejection.

## Grants, leases, and claims

- A **grant** declares scoped standing authority for named action/target/environment combinations.
- A **lease** delegates expiring, budgeted authority for one bounded context.
- A **claim** is durable evidence that one execution path took responsibility before a possible external effect.

These objects are not interchangeable. A plan hash is not a lease, a lease is not completion, and a claim is not success. Uncertain post-claim outcomes are reconciled rather than retried automatically.

## Receipts and attestations

Receipts bind exact inputs, state codes, hashes, counters, and outcomes while excluding prompts, credentials, paths, and private content where their contract requires it. Runtime attestations bind the active installed deployment. Model, capability, broker, update, backup, and qualification receipts have narrower meanings.

A receipt never grants new authority merely by existing, and content-free does not mean non-sensitive in every context. Freshness, signer, input identity, schema, chain, and custody all matter.

## Prohibited equivalences

- source present ≠ enabled;
- enabled ≠ healthy;
- prepared ≠ active;
- proposal ≠ approval;
- approval ≠ execution;
- execution receipt ≠ semantic correctness;
- verification ≠ qualification;
- synthetic qualification ≠ real-backend evidence;
- release signature ≠ publication or installation;
- backup creation ≠ validated recovery;
- agent answer ≠ independent acceptance.

Use [status and evidence](status-and-evidence.md) whenever a claim crosses one of these boundaries.
