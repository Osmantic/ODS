---
title: Configure Pixel Operations and Frontier
doc_type: how-to
audience: [owner, operator, security-reviewer]
feature_status: mixed
owners: [documentation, configuration, security]
sources_of_truth: [onboarding.example.json, scripts/configure.mjs, OPERATIONS-LIMB.md, FRONTIER-LIMB.md, deploy/ops-broker/policy.example.json, deploy/frontier-broker/policy.example.json]
last_verified_at: 2026-08-27
---

# Configure Pixel Operations and Frontier

Operations and Frontier are optional isolated authority planes. Enable either only after its private policy, credential/identity, budgets, acceptance, pause, and recovery owners are defined.

## Operations

Private onboarding names the Operations policy and any signed action-pack mappings. The policy defines targets, environments, fixed actions, grants/leases, budgets, verification, and rollback metadata. The broker owns its isolated SSH identity and pins; the gateway must not receive them.

Install only after policy and target identities are independently reviewed:

```bash
# Requires exact confirmation; review before running.
./pixel ops-broker --confirm
```

Target enrollment, action mapping, authority grants, approvals, pause/resume, and live acceptance remain separate steps in [Operations Limb](../../OPERATIONS-LIMB.md).

## Frontier

Choose `chatgpt` or `api-key` authentication and a managed or private custom budget policy. Keep credential/auth cache and policy in broker-owned private state. The primary model must satisfy the configured private/attested boundary.

```bash
# Requires exact confirmation; review before running.
./pixel frontier-broker --confirm
```

Installation does not prove a provider call. Use the fixed synthetic live-qualification path only with explicit owner authorization and retain its evidence separately from ordinary configuration.

## Browser budget boundary

The owner page can select managed settings or draft a short-lived custom budget proposal for an already private custom policy. It cannot apply or activate the proposal. The terminal rechecks proposal ID, hash, policy/onboarding identity, expiry, authentication, and billing before changing only the source policy's budget block.

## Change and rollback

Policy or credential changes require reconfiguration and a new reviewed deployment plan. Pause the affected broker before incident-sensitive changes. Rollback does not silently remove remote Operations keys/targets or Frontier provider credentials/policies; reconcile and revoke them separately.
