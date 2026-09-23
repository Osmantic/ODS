---
title: Pixel operator command cheat sheet
doc_type: reference
audience: [owner, operator]
feature_status: mixed
owners: [documentation, operations]
sources_of_truth: [pixel, OPERATIONS.md, UPGRADE.md, INCIDENT-RESPONSE.md, docs/reference/cli.md]
last_verified_at: 2026-08-27
---

# Pixel operator command cheat sheet

This page groups common commands by intent. The [generated CLI reference](../reference/cli.md) remains the complete command inventory.

## Read-only or preparatory

| Intent | Command | Evidence boundary |
|---|---|---|
| Show local owner workspace | `./pixel ui` | Loopback process only; no deployment activation |
| Inspect rounded host fit | `./pixel doctor` | Advisory; no model-fit guarantee |
| Verify installed deployment | `./pixel verify` | Installed bytes/services/configuration, not model quality |
| Show enabled limbs | `./pixel limbs` | Configuration inventory, not live capability proof |
| Show reference services | `./pixel services status` | Reference profile only |
| Tail reference-service logs | `./pixel services logs` | Local logs may still be private |
| Build deployment plan | `./pixel plan` | Reviewable plan; no activation |
| Inspect signed update | `./pixel update-inspect ...` | Signature/artifact verification; no staging or execution |
| Preview update activation | `./pixel update-activate --preview ...` | Derives exact hash; no deployment change |
| Preview update rollback | `./pixel update-rollback --preview ...` | Derives exact hash; no rollback |
| Preview interrupted recovery | `./pixel update-recover --preview ...` | Diagnoses receipt state; does not rerun candidate code |

## Mutating commands that require review

| Intent | Command | Before confirming |
|---|---|---|
| Apply reviewed deployment | `./pixel apply --confirm` | Match exact plan and source identity |
| Restore preceding apply | `./pixel rollback --confirm` | Confirm the saved rollback marker and separately retained private state |
| Start reference services | `./pixel services up --confirm` | Use only for the reference profile |
| Rotate gateway credential | `./pixel rotate gateway --confirm` | Prepare to verify old-token rejection and new-token health |
| Pause Operations | `./pixel ops-pause "reason" --confirm` | Enabled Operations only; record incident/reason |
| Resume Operations | `./pixel ops-resume "reason" --confirm` | Reconcile incidents and leases first |
| Pause Frontier | `./pixel frontier-pause "reason" --confirm` | Enabled Frontier only |
| Resume Frontier | `./pixel frontier-resume "reason" --confirm` | Reconcile provider and local ledgers first |

Approval commands such as `source-approve`, `ops-approve`, and `frontier-approve` apply only to one exact previously inspected object and hash. Never derive an approval from this cheat sheet; use the owning limb guide.

## Backup and recovery

| Intent | Command shape |
|---|---|
| Create encrypted backup | `./pixel backup ABSOLUTE_DIRECTORY AGE_RECIPIENT` |
| Validate archive | `./pixel restore ARCHIVE --identity AGE_IDENTITY --validate-only` |
| Rehearse restore | `./pixel restore ARCHIVE --identity AGE_IDENTITY --rehearse NEW_EMPTY_ROOT` |
| Restore reviewed archive | `./pixel restore ARCHIVE --identity AGE_IDENTITY --replace --confirm` |

Keep the decryption identity offline and the trusted allowed-signers file separate from the archive set.

## Service orientation

Common read-only checks:

```bash
systemctl status openclaw-gateway.service
systemctl status pixel-web-courier.service
systemctl status pixel-source-broker.timer
systemctl status pixel-source-broker.service
systemctl status pixel-ops-broker.service
systemctl status pixel-frontier-broker.service
```

Only enabled/installed units should be active. An absent disabled limb can be correct.
