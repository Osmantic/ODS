---
title: Add a local capability declaration
doc_type: how-to
audience: [contributor, security-reviewer]
feature_status: candidate
owners: [documentation, architecture]
sources_of_truth: [CUSTOMIZATION.md, scripts/limb-kit.py, schemas/local-capability-pack-v1.schema.json]
last_verified_at: 2026-08-27
---

# Add a local capability declaration

A local capability pack is signed data inside one projection limb. It can expose only tools already declared by that limb, with observe-only authority and no raw-content storage.

Create the safe skeleton through `./pixel limb-kit add-local`, then review the generated JSON and re-run limb validation. The validator requires:

- tool names are a subset of the containing limb manifest;
- trust remains untrusted projection;
- authority remains observe-only;
- raw content is not stored;
- classifications and retention do not exceed the limb contract;
- signed limb provenance and file/tree hashes match.

Enabling the signed limb publishes the verified local declaration into private onboarding. Configuration reopens the no-follow file and rechecks its hash and signed provenance before granting the declared tool to the fixed Pixel agent.

This declaration cannot add network, credentials, process execution, source write authority, Operations targets, or Frontier egress. Failure to validate leaves it inert. Disable/remove follow the containing limb lifecycle.
