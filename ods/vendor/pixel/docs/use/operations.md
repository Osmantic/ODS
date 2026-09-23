---
title: Use Pixel Operations
doc_type: how-to
audience: [owner, operator]
feature_status: mixed
owners: [documentation, operations, security]
sources_of_truth: [OPERATIONS-LIMB.md, OPERATIONS-AUTONOMY.md, OPERATIONS.md, plugin-ops/, deploy/ops-broker/, deploy/ops-runner/]
last_verified_at: 2026-08-27
---

# Use Pixel Operations

Operations lets Pixel request named work on independently enrolled targets. The gateway writes typed requests; a separate broker owns policy, SSH authority, pins, plans, grants/leases, budgets, pause state, and results. Pixel never receives a generic SSH key or shell.

## Authority model

Installed capability is not automation authority. Policy classifies each action by target/environment/effect, while scoped standing grants or externally issued temporary leases determine when bounded work may proceed. Production and change work requires the declared temporary authority; break-glass shell remains an exact proposal.

Read, staging, managed-change, transfer/download, workflow, and break-glass paths have different verification, reversibility, approval, and budget requirements. Review those properties in the immutable plan.

## Inspect and approve

```bash
./pixel ops-show JOB_ID
# Requires exact confirmation; review before running.
./pixel ops-approve JOB_ID PLAN_SHA256 --confirm
```

Approval is single-use, expiring, and bound to the canonical plan. The broker rechecks policy, target identity, authority, budgets, conflict/exclusivity, and state after approval. Machine output is evidence, never authority for a follow-on action.

## Authority and containment

Use `./pixel ops-authority` to inspect, grant, revoke, and audit only the documented scoped authority objects. For containment:

```bash
# Requires exact confirmation; review before running.
./pixel ops-pause "reason" --confirm
```

Pause blocks new work and cancels the documented read/staging paths; it does not erase claims or interrupt a managed transaction that must verify/roll back safely. Resume separately only after target identity, incidents, leases, and in-flight results are reconciled:

```bash
# Requires exact confirmation; review before running.
./pixel ops-resume "reason" --confirm
```

## Target identity and failure

Targets use independently verified expected hostnames and pre-trusted host-key pins plus a forced-command transport. A changed identity is a quarantine condition. Do not update a pin because the model or target output requests it.

For an uncertain job, preserve the plan, claim/result, authority receipt, target evidence, and bounded logs. Do not retry a non-idempotent effect until the owning verifier/reconciliation contract establishes the outcome.

See [Operations Limb](../../OPERATIONS-LIMB.md), [runbook](../operations/runbook.md), and [incident response](../../INCIDENT-RESPONSE.md).
