---
title: Decommission a Pixel deployment
doc_type: runbook
audience: [owner, operator]
feature_status: supported
owners: [documentation, operations, security]
sources_of_truth: [DEPLOYMENT.md, OPERATIONS.md, INCIDENT-RESPONSE.md, SUPPORT.md, scripts/limbs.sh]
last_verified_at: 2026-08-27
---

# Decommission a Pixel deployment

Pixel intentionally has no one-command destructive uninstall. Source releases, runtime state, workspace memory, OAuth material, broker credentials/policies, remote target trust, backups, and audit evidence have different owners and retention requirements.

## 1. Approve scope and retention

Name the deployment, host, owner, reason, evidence-retention deadline, credential issuers, enabled limbs, remote targets, backup sets, and person authorized to approve each data category's removal. Resolve actual paths from the reviewed private deployment records; do not infer them from examples.

## 2. Create the final recovery record

Create, validate, and rehearse a final signed encrypted backup if policy requires recoverability. Export the trusted allowed-signers file separately. Decide which audit/incident evidence must outlive the deployment. A backup is not authorization to delete its source.

## 3. Contain authority and external access

When enabled:

```bash
# Requires exact confirmation; review before running.
./pixel ops-pause "decommission" --confirm
./pixel frontier-pause "decommission" --confirm
```

Revoke active Operations leases/grants as required, remove Pixel's forced key from every enrolled target through the independently trusted operator channel, and revoke provider/API/OAuth access at each issuer. Removing a local file alone does not revoke an external credential.

Stop source timers/actions before revoking Google access. Reconcile every pending or uncertain external action first so decommission does not erase the only evidence needed to prevent replay.

## 4. Stop the expected services

Inventory before stopping:

```bash
./pixel limbs
systemctl status openclaw-gateway.service
systemctl status pixel-web-courier.service
systemctl status pixel-source-broker.timer
systemctl status pixel-source-broker.service
systemctl status pixel-ops-broker.service
systemctl status pixel-frontier-broker.service
```

Have the client approve the exact installed unit list. Stop and disable only those reviewed units using the administrator's normal service-management process. Include any client-specific or signed custom limb units; do not copy a generic deletion list from documentation.

## 5. Remove categories separately

After services are stopped and external authority is revoked, process each approved category independently:

- immutable Pixel releases and update staging;
- generated gateway configuration and OpenClaw runtime state;
- workspace, memory, sessions, and local model/search state;
- Source, Operations, Frontier, custom-limb, and Deep Work private state;
- OAuth, SSH, provider, backup-signing, and knowledge-vault keys;
- system service units, isolated identities/groups, ACLs, and container images;
- backups, allowed signers, logs, receipts, and incident/audit evidence according to retention policy.

Use literal resolved paths, no-follow checks, and a recorded owner approval for each destructive action. Do not use broad globs, home-directory recursion, or an unreviewed script. This guide deliberately does not provide deletion commands.

## 6. Prove absence

Confirm expected units are inactive/disabled or absent; listener ports are closed; container/scheduler jobs are gone; external credentials and target keys are rejected; no Pixel process owns the removed state; and retained evidence/backups are in their approved custody. Record the secret-free result and any deliberately retained artifact.

If a boundary cannot be proven, leave the item quarantined and escalate through [support](../../SUPPORT.md) or [incident response](../../INCIDENT-RESPONSE.md) rather than widening deletion scope.
