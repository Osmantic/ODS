---
title: Add a Frontier task restriction pack
doc_type: how-to
audience: [contributor, security-reviewer]
feature_status: candidate
owners: [documentation, architecture, security]
sources_of_truth: [CUSTOMIZATION.md, FRONTIER-LIMB.md, scripts/limb-kit.py, schemas/frontier-task-pack-v1.schema.json]
last_verified_at: 2026-08-27
---

# Add a Frontier task restriction pack

A Frontier task pack can only narrow one existing typed task class. It cannot create a task class, generic prompt, provider, credential, data category, budget, rehydration capability, or enablement.

Create the safe skeleton with `./pixel limb-kit add-frontier`, select an existing plan-review or failure-triage class, and review the generated restriction:

- mode remains `restrict`;
- classifications/categories are a subset of the base policy;
- input/output limits are no wider;
- local tools are a subset of the containing limb;
- rehydration can only be reduced or disabled;
- signed limb and pack identities/hashes match.

Enabling the containing limb adds the verified receipt to private onboarding. Configure composes the restriction with the base Frontier policy and fails if it would widen the deployment.

The declaration does not call a provider, grant approval, enable bounded automation, or expose a credential. Test malformed inputs, reserved markers, identifier/secret rejection, classification narrowing, token ceilings, replay, disabled behavior, and removal.
