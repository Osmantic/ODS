---
title: Pixel CLI - Operations
doc_type: reference
audience: [owner, operator, contributor, maintainer]
feature_status: mixed
owners: [documentation]
sources_of_truth: [pixel]
generated_by: scripts/docs/generate-cli-reference.mjs
last_verified_at: generated
---

<!-- GENERATED FILE. Run `node scripts/docs/generate.mjs --write`; do not edit by hand. -->

# Operations

**Status boundary:** Mixed; private policy, grants, approval, lease, and target identity bound authority.

| Command | Purpose | Effect | Confirmation | Exit semantics | Normative source |
|---|---|---|---|---|---|
| `./pixel ops-broker` | Install the isolated Operations Broker (requires --confirm) | state-changing or authority-affecting | required for the named mutation | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/install-ops-broker.sh`](../../../scripts/install-ops-broker.sh) |
| `./pixel ops-keygen` | Generate the isolated Operations SSH identity (requires --confirm) | state-changing or authority-affecting | required for the named mutation | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/generate-ops-key.sh`](../../../scripts/generate-ops-key.sh) |
| `./pixel ops-target` | Enroll one verified dedicated runner target (requires --confirm) | state-changing or authority-affecting | required for the named mutation | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/provision-ops-target.sh`](../../../scripts/provision-ops-target.sh) |
| `./pixel ops-target-refresh` | Refresh helpers on one enrolled runner without changing trust | may write state; inspect command help and owning guide | mode/subcommand-dependent; inspect help | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/refresh-ops-target.sh`](../../../scripts/refresh-ops-target.sh) |
| `./pixel ops-target-actions` | Install private action-pack configuration on one runner (requires --confirm) | state-changing or authority-affecting | required for the named mutation | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/configure-ops-target-actions.sh`](../../../scripts/configure-ops-target-actions.sh) |
| `./pixel ops-show` | Show one immutable Operations plan and current status | read-only or inert inspection | none for the named inspection/preview | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/show-ops-job.sh`](../../../scripts/show-ops-job.sh) |
| `./pixel ops-approve` | Approve one exact Operations plan hash (requires --confirm) | state-changing or authority-affecting | required for the named mutation | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/approve-ops-job.sh`](../../../scripts/approve-ops-job.sh) |
| `./pixel ops-authority` | Inspect, grant, revoke, and audit bounded authority | mode-dependent; includes non-mutating and state-writing modes | mode/subcommand-dependent; inspect help | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/ops-authority.sh`](../../../scripts/ops-authority.sh) |
| `./pixel ops-policy-migrate` | Convert one private v1 Operations policy to reviewable v2 output | may write state; inspect command help and owning guide | mode/subcommand-dependent; inspect help | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/migrate-ops-policy.mjs`](../../../scripts/migrate-ops-policy.mjs) |
| `./pixel ops-policy-tighten` | Remove reviewed v1 compatibility grants from a v2 policy | state-changing or authority-affecting | mode/subcommand-dependent; inspect help | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/tighten-ops-policy.mjs`](../../../scripts/tighten-ops-policy.mjs) |
| `./pixel ops-action-pack` | Configure one reusable action pack and private target mapping | may write state; inspect command help and owning guide | mode/subcommand-dependent; inspect help | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/configure-ops-action-pack.mjs`](../../../scripts/configure-ops-action-pack.mjs) |
| `./pixel ops-pause` | Emergency-pause Operations execution (requires --confirm) | state-changing or authority-affecting | required for the named mutation | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/ops-authority.sh`](../../../scripts/ops-authority.sh) |
| `./pixel ops-resume` | Resume Operations execution (requires --confirm) | state-changing or authority-affecting | required for the named mutation | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/ops-authority.sh`](../../../scripts/ops-authority.sh) |

Use the [owning guide](../../use/operations.md) before a state-changing command. Return to the [CLI family index](README.md).
