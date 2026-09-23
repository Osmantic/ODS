---
title: Validated extension workflow example
doc_type: tutorial
audience: [contributor, security-reviewer, maintainer]
feature_status: candidate
owners: [documentation, architecture, security]
sources_of_truth: [CUSTOMIZATION.md, scripts/limb-kit.py, schemas/limb-pack-v1.schema.json, schemas/local-capability-pack-v1.schema.json, tests/test_limb_kit.py]
last_verified_at: 2026-08-27
---

# Validated extension workflow example

This synthetic `host-health` example demonstrates the complete signed projection-limb lifecycle. The limb seam is a development preview, not a stable third-party compatibility promise. Use only fake/local fixture data while developing it.

## 1. Generate and validate

```bash
./pixel limb-kit generate host-health /absolute/work/host-health --name "Host health"
./pixel limb-kit add-local /absolute/work/host-health host-health-observe --name "Host health observe"
./pixel limb-kit validate /absolute/work/host-health
```

Review every generated file before changing the worker. Keep the gateway adapter generated. The worker remains offline and credential-free, reads only its signed pack/own state, and writes only its bounded projection. The local declaration can expose only tools already named by the containing limb, with observe-only authority and no raw-content storage.

After implementing the synthetic health projection, re-run validation and the limb-kit unit, isolation, pressure, stale-data, malformed-projection, injection, and disabled-limb tests. Validation proves shape and canonical-tree rules; it does not prove safe worker semantics.

## 2. Sign and independently verify

```bash
# Requires exact confirmation; review before running.
./pixel limb-kit sign /absolute/work/host-health \
  --signing-key /secure/pixel-limb-publisher \
  --identity owner@example --confirm
./pixel limb-kit verify /absolute/work/host-health \
  --allowed-signers /secure/pixel-limb-allowed-signers \
  --identity owner@example
```

Keep signing keys, allowed signers, and identities out of the repository. A valid signature binds reviewed bytes; it does not grant install, enablement, tools, network, credentials, or external effects.

## 3. Install, then enable

```bash
# Requires exact confirmation; review before running.
./pixel limb-kit install /absolute/work/host-health \
  --allowed-signers /secure/pixel-limb-allowed-signers \
  --identity owner@example --confirm
# Requires exact confirmation; review before running.
./pixel limb-kit enable host-health \
  --onboarding /secure/client/onboarding.json \
  --gateway-user pixel-owner --confirm
./pixel limb-kit status
```

Installation and enablement are separate. Install verifies and records exact bytes but leaves the pack disabled. Enablement revalidates installed bytes, creates the constrained worker identity/units, updates private onboarding/registry, and runs the projection lifecycle. Then reconfigure, review the plan, apply, verify, and confirm the projection is bounded and labeled untrusted.

Use the actual configured gateway user from the reviewed deployment; `pixel-owner` is a synthetic placeholder, not a default.

## 4. Disable and remove

```bash
# Requires exact confirmation; review before running.
./pixel limb-kit disable host-health --confirm
# Requires exact confirmation; review before running.
./pixel limb-kit remove host-health --confirm
./pixel limb-kit status
```

Disable verifies installed unit bytes before removing enablement. Remove refuses an enabled pack or unmanaged residue and deletes only its managed pack/projection state. Reconfigure/apply after removal, then verify tool absence, unit absence, registry state, and retained evidence policy.

For Operations target binding or Frontier narrowing, start with [Operations action packs](../operations-action-packs.md) or [Frontier task packs](../frontier-task-packs.md). Those declarations stay behind their base broker policy and never inherit authority from this local example.
