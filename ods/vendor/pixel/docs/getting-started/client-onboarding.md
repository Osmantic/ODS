---
title: Client onboarding and handoff
doc_type: tutorial
audience: [owner, operator]
feature_status: mixed
owners: [documentation, deployment, security]
sources_of_truth: [CLIENT-ONBOARDING.md, CLIENT-KIT.md, DEPLOYMENT.md, SECURITY.md]
last_verified_at: 2026-08-27
---

# Client onboarding and handoff

Create one isolated deployment contract per owner or client. Never reuse another client's cloud project, OAuth token, model key, memory, session, workspace, broker policy, target identity, backup key, or evidence.

## Collect decisions before configuration

- Owner, organization, deployment name, timezone, and technical custodian.
- Prepared or reference deployment profile and an explicit decision for every capability limb.
- Model and web-search endpoints or reference-model artifact, with private-host classification.
- Source accounts and calendar identifiers; backup, incident, update, and notification owners.
- Operations targets, allowed roots/actions, approval tiers, break-glass owner, download domains, and SSH custodian when enabled.
- Frontier auth/billing owner, allowed task classes, disclosure rules, budgets, and live-qualification owner when enabled.

Copy the sanitized [onboarding example](../../onboarding.example.json) to an owner-private path and fill only the selected capability fields. Run the configuration flow described in [first deployment](first-deployment.md). Generated `.env`, broker policy, tokens, and runtime state stay private and are not hand-edited as a substitute for regeneration.

## Google source onboarding

Use a new client-owned cloud project. Enable only the source APIs and scopes named by `CLIENT-ONBOARDING.md`, prefer an Internal audience where the owner's workspace policy permits it, and store the downloaded desktop-client JSON at the configured private path with owner-only mode.

After owner authorization, immediately transfer the staged refresh token to Source Broker custody through the reviewed broker install path. The gateway must not retain or read the token, and the token must never be printed, pasted into the onboarding file, or copied into a support packet.

```bash
./pixel authorize
# Requires exact confirmation; review before running.
./pixel source-broker --confirm
```

For a remote host, use only the loopback callback forwarding pattern in `CLIENT-ONBOARDING.md`; do not expose the callback or control surface on the LAN.

## Acceptance exercise

1. Verify installed-host health and enabled limbs.
2. Complete one real configured-model conversation and retain only content-free outcome facts.
3. Read harmless projected source summaries and confirm raw message/event content and credentials are absent.
4. Create a synthetic proposal, inspect its protected snapshot and exact hash, approve it through the terminal path, reconcile the provider result, then remove the synthetic object.
5. Run the hostile-source harness with fake canaries; confirm untrusted content cannot obtain file, process, memory, network, credential, or actuator authority.
6. For Operations or Frontier, use disposable targets/tasks and complete the limb-specific identity, denial, approval, result, pause/recovery, and live-qualification exercises.
7. Rehearse the encrypted backup into an isolated empty root and record the restore boundary.

A direct tool call, fake adapter, model-off reply, or synthetic harness does not replace the real configured conversation or the exact live lane required by the selected capability.

## Handoff

Give the owner the exact release/checksum identity, deployment and enabled-limb record, external-account ownership, private-policy custody map, backup/recovery procedure, escalation contact, maintenance window, and known partial/blocked items. Do not transfer a source-machine session, another owner's data, raw evidence, or broker credentials.

Complete the [post-install checklist](post-install-checklist.md), [support matrix](../reference/support-matrix.md), and [security overview](../security/README.md) during handoff.
