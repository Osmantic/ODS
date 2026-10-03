---
title: Pixel operations runbook
doc_type: runbook
audience: [owner, operator]
feature_status: mixed
owners: [documentation, operations]
sources_of_truth: [OPERATIONS.md, DEPLOYMENT.md, INCIDENT-RESPONSE.md, UPGRADE.md, pixel, scripts/verify.sh, scripts/rotate-gateway-token.sh, scripts/tighten-ops-policy.mjs]
last_verified_at: 2026-08-27
---

# Pixel operations runbook

This is the normal operating rhythm for a technically managed Pixel deployment. Run commands as the dedicated deployment owner unless a linked procedure explicitly crosses a narrow administrator boundary.

## Start of an operating session

```bash
./pixel ui
./pixel verify
./pixel limbs
./pixel services status
```

Use `services status` only for the `reference` profile; a prepared profile has separately operated endpoints. Stop `./pixel ui` when it is not needed, and never expose its port.

Before acting on a warning, distinguish:

- repository status from installed-host status;
- a configured or prepared surface from an active one;
- a fresh verified receipt from a stale or unavailable projection; and
- a model/tool result from operator acceptance.

See [status and evidence](../concepts/status-and-evidence.md).

## Daily when Pixel is in active use

- Check the owner workspace for failed or interrupted turns and unresolved approval boundaries.
- Review `systemctl status` for the gateway and only the enabled services.
- Inspect bounded broker results before retrying any external action.
- Keep an uncertain Calendar, Operations, or Frontier result unretried until its existing journal or receipt is reconciled.
- Confirm disk space covers immutable releases, private state, projections, receipts, logs, and backup staging.

Use the [monitoring and logs guide](monitoring-and-logs.md) for exact read-only commands.

## After configuration or host maintenance

```bash
./pixel plan
# Requires exact confirmation; review before running.
./pixel apply --confirm
./pixel verify
```

Review the new plan hash and enabled-limb matrix before apply. A failed apply attempts transactional restoration. If the command reports that compensation or verification was incomplete, keep services stopped and follow [troubleshooting](troubleshooting.md); do not repair installed bytes manually.

After an upgrade or container/runtime maintenance, run one harmless real configured-model turn and one applicable read-only tool check. Verification alone does not prove those paths.

## Weekly

- Review recent gateway and enabled-broker journal windows for failures, restarts, or repeated denials.
- Confirm source projections are fresh without copying private source content into the review record.
- Review Operations grants/leases and Frontier usage only when those limbs are enabled.
- Reconcile pending external actions; do not clear a failed service display as a substitute for provider reconciliation.
- Confirm the most recent backup set has its encrypted archive, checksum, and detached signature.

## Monthly and before material changes

```bash
./pixel verify
```

- Create a fresh encrypted private-state backup.
- Validate its checksum, signature, signer, and decryption.
- Rehearse it into a new empty isolated root.
- Review credential owners, rotation dates, retention, disk budgets, and update policy.
- Exercise the applicable items in the full [acceptance checklist](../../ACCEPTANCE-CHECKLIST.md).

Use [backup and restore](backup-and-restore.md) for the exact sequence. Backup creation without validation and rehearsal is not recovery evidence.

## Credential and policy maintenance

Use the [gateway token rotation procedure](gateway-token-rotation.md) for the transactional lock, private rollback snapshot, restart, verification, old-token rejection, and new-session checks. Rotation is not a generic credential command and does not rotate broker, provider, signing, backup, or external issuer credentials.

`ops-policy-tighten` is conditional maintenance after an Operations policy has already been deliberately migrated to schema v2 and every compatibility grant has been reviewed. It removes the reviewed v1 compatibility grants and creates a backup; it is not an automatic migration step and is not appropriate for every v2 policy.

```bash
# Requires exact confirmation; review before running.
./pixel ops-policy-tighten /absolute/private/policy-v2.json --confirm
```

After tightening, validate the policy, inspect the backup/result, reconfigure only through the owning Operations path, and repeat the relevant grant, denial, plan, approval, pause/resume, and disposable-target checks before accepting the change.

## Incident boundary

If credentials, authority, privacy, host integrity, or unexpected external effects may be compromised, stop routine work and follow [incident response](../../INCIDENT-RESPONSE.md). Pause Operations or Frontier separately when enabled; a quiet queue is not containment. Preserve a secret-free timeline and private evidence without attaching raw state to a ticket.

## Related operator pages

- [Command cheat sheet](command-cheat-sheet.md)
- [Verify and diagnose](verify-and-diagnose.md)
- [Backup and restore](backup-and-restore.md)
- [Update and rollback](update-and-rollback.md)
- [Gateway token rotation](gateway-token-rotation.md)
- [Incident response entry point](incident-response.md)
- [Troubleshooting](troubleshooting.md)
- [Decommission](decommission.md)
