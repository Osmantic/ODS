---
title: Pixel lifecycle and state
doc_type: concept
audience: [owner, operator, contributor]
feature_status: mixed
owners: [documentation, architecture]
sources_of_truth: [DEPLOYMENT.md, UPGRADE.md, CONTROL-SURFACE.md, OPERATIONS-LIMB.md, FRONTIER-LIMB.md, DEEP-WORK.md]
last_verified_at: 2026-08-27
---

# Pixel lifecycle and state

Pixel uses explicit state machines so a restart, timeout, or uncertain external result does not silently become success or permission to retry.

## Deployment lifecycle

```text
authored settings -> generated configuration -> reviewed plan -> applied release -> fresh verification
```

Configuration does not activate. Planning does not mutate. Apply creates a transactional change and verifies it. A failed verified change restores the preceding managed deployment where the contract allows. A runtime attestation becomes current only after the complete verification pass.

## Update lifecycle

```text
signed intake -> inspected -> privately staged -> rehearsed -> previewed -> claimed -> activated
                                                              -> rolled back -> cleaned
```

Inspection, staging, rehearsal, preview, activation, rollback, recovery, and cleanup have separate receipts and confirmation hashes. Interrupted state is inspected and finalized from observed evidence; candidate code is not blindly replayed.

## External-action lifecycle

```text
request -> proposal -> protected review -> approval or bounded direct decision -> durable claim -> result -> reconciliation
```

A timeout after a claim may mean the provider acted. The original claim/result/journal is authoritative until reconciliation. Clearing a UI failure or deleting state cannot make retry safe.

## Conversation lifecycle

Turns are `running`, `succeeded`, `failed`, or `interrupted`. Only `succeeded` includes a bounded assistant response. Failed and interrupted turns claim no answer. The owner workspace disables duplicate submission while one turn is running and recovers an interrupted turn as interrupted.

## Operations and Frontier authority lifecycle

Policies define the outer boundary. Grants and leases narrow it. Pause blocks new work; resume is a separate audited decision. Revocation or expiry does not erase evidence of work already claimed. Provider or target results remain subject to their own reconciliation and verification.

## Deep Work source lifecycle

Deep Work source defines inert draft, assembly, preparation, stage, service, pause/resume/cancel, checkpoint, fleet, and acceptance transitions. Current runtime status remains [development-disabled](../status.md). An inert prepared or staged object grants no lease, scheduling, model call, worker, network, external effect, or completion authority.

Cancellation is not a generic process kill. It can settle an inactive goal or one whose started child has terminal cleanup evidence; otherwise the supervising boundary must stop and prove cleanup first.
