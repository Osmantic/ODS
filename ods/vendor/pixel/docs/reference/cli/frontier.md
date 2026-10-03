---
title: Pixel CLI - Frontier
doc_type: reference
audience: [owner, operator, contributor, maintainer]
feature_status: mixed
owners: [documentation]
sources_of_truth: [pixel]
generated_by: scripts/docs/generate-cli-reference.mjs
last_verified_at: generated
---

<!-- GENERATED FILE. Run `node scripts/docs/generate.mjs --write`; do not edit by hand. -->

# Frontier

**Status boundary:** Mixed; a configured provider route still needs the applicable live qualification.

| Command | Purpose | Effect | Confirmation | Exit semantics | Normative source |
|---|---|---|---|---|---|
| `./pixel frontier-broker` | Install the isolated Frontier Broker (requires --confirm) | state-changing or authority-affecting | required for the named mutation | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/install-frontier-broker.sh`](../../../scripts/install-frontier-broker.sh) |
| `./pixel frontier-show` | Inspect one immutable sanitized Frontier payload and status | read-only or inert inspection | none for the named inspection/preview | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/show-frontier-job.sh`](../../../scripts/show-frontier-job.sh) |
| `./pixel frontier-approve` | Approve one exact Frontier payload hash (requires --confirm) | state-changing or authority-affecting | required for the named mutation | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/approve-frontier-job.sh`](../../../scripts/approve-frontier-job.sh) |
| `./pixel frontier-usage` | Show content-free rolling Frontier usage and routing totals | read-only or inert inspection | none for the named inspection/preview | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/show-frontier-usage.sh`](../../../scripts/show-frontier-usage.sh) |
| `./pixel frontier-live-qualify` | Prepare and explicitly run one fixed synthetic provider check | writes generated, private, evidence, or bounded task state | not declared by root help; this is not permission to mutate | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/frontier-live-qualify.sh`](../../../scripts/frontier-live-qualify.sh) |
| `./pixel frontier-budget` | Draft or apply one exact private custom Frontier budget change | mode-dependent; includes non-mutating and state-writing modes | mode/subcommand-dependent; inspect help | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/frontier_budget.py`](../../../scripts/frontier_budget.py) |
| `./pixel frontier-authority` | Inspect, grant, revoke, and audit bounded Frontier authority | mode-dependent; includes non-mutating and state-writing modes | mode/subcommand-dependent; inspect help | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/frontier-authority.sh`](../../../scripts/frontier-authority.sh) |
| `./pixel frontier-pause` | Emergency-pause Frontier egress (requires --confirm) | state-changing or authority-affecting | required for the named mutation | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/frontier-authority.sh`](../../../scripts/frontier-authority.sh) |
| `./pixel frontier-resume` | Resume Frontier egress (requires --confirm) | state-changing or authority-affecting | required for the named mutation | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/frontier-authority.sh`](../../../scripts/frontier-authority.sh) |

Use the [owning guide](../../use/frontier-review.md) before a state-changing command. Return to the [CLI family index](README.md).
