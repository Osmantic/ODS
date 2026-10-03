---
title: Pixel CLI - Sources and external actions
doc_type: reference
audience: [owner, operator, contributor, maintainer]
feature_status: mixed
owners: [documentation]
sources_of_truth: [pixel]
generated_by: scripts/docs/generate-cli-reference.mjs
last_verified_at: generated
---

<!-- GENERATED FILE. Run `node scripts/docs/generate.mjs --write`; do not edit by hand. -->

# Sources and external actions

**Status boundary:** Mixed; projection, proposal, approval, and actuator authority remain separate.

| Command | Purpose | Effect | Confirmation | Exit semantics | Normative source |
|---|---|---|---|---|---|
| `./pixel source-broker` | Install/refresh the isolated Source Broker (requires --confirm) | state-changing or authority-affecting | required for the named mutation | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/install-source-broker.sh`](../../../scripts/install-source-broker.sh) |
| `./pixel source-show` | Snapshot and inspect one Calendar proposal outside Pixel | writes generated, private, evidence, or bounded task state | mode/subcommand-dependent; inspect help | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/show-source-action.sh`](../../../scripts/show-source-action.sh) |
| `./pixel source-approve` | Apply one hash-bound Calendar proposal outside Pixel (requires --confirm) | state-changing or authority-affecting | required for the named mutation | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/approve-source-action.sh`](../../../scripts/approve-source-action.sh) |
| `./pixel source-reconcile` | Reconcile an indeterminate Calendar create or update without retrying it | writes generated, private, evidence, or bounded task state | not declared by root help; this is not permission to mutate | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/reconcile-source-action.sh`](../../../scripts/reconcile-source-action.sh) |
| `./pixel github-action` | Apply or reconcile one exact allowlisted GitHub proposal | state-changing or authority-affecting | mode/subcommand-dependent; inspect help | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`deploy/github-broker/broker.py`](../../../deploy/github-broker/broker.py) |
| `./pixel action-journal` | Inspect or cancel one still-unsent external action journal | mode-dependent; includes non-mutating and state-writing modes | mode/subcommand-dependent; inspect help | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`deploy/action_journal/cli.py`](../../../deploy/action_journal/cli.py) |

Use the [owning guide](../../use/sources-and-proposals.md) before a state-changing command. Return to the [CLI family index](README.md).
