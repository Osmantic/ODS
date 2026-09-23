---
title: Pixel CLI - Customization
doc_type: reference
audience: [owner, operator, contributor, maintainer]
feature_status: mixed
owners: [documentation]
sources_of_truth: [pixel]
generated_by: scripts/docs/generate-cli-reference.mjs
last_verified_at: generated
---

<!-- GENERATED FILE. Run `node scripts/docs/generate.mjs --write`; do not edit by hand. -->

# Customization

**Status boundary:** Mixed; status and authority depend on the selected extension seam.

| Command | Purpose | Effect | Confirmation | Exit semantics | Normative source |
|---|---|---|---|---|---|
| `./pixel limb-kit` | Generate, sign, verify, and manage constrained customization packs | mode-dependent; includes non-mutating and state-writing modes | mode/subcommand-dependent; inspect help | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/limb-kit.py`](../../../scripts/limb-kit.py) |
| `./pixel client-kit` | Generate or validate a private golden-core/client-overlay contract | mode-dependent; includes non-mutating and state-writing modes | mode/subcommand-dependent; inspect help | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/client-kit.mjs`](../../../scripts/client-kit.mjs) |
| `./pixel extension-hash` | Compute a custom gateway extension's approved tree digest | may write state; inspect command help and owning guide | not declared by root help; this is not permission to mutate | 0 completed; nonzero is command-specific refusal/error—read stderr or JSON before acting | [`scripts/hash-gateway-extension.mjs`](../../../scripts/hash-gateway-extension.mjs) |

Use the [owning guide](../../extend/choose-an-extension-point.md) before a state-changing command. Return to the [CLI family index](README.md).
