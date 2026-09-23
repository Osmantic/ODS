---
title: Back up and restore Pixel
doc_type: runbook
audience: [owner, operator]
feature_status: mixed
owners: [documentation, operations]
sources_of_truth: [OPERATIONS.md, DEPLOYMENT.md, INCIDENT-RESPONSE.md, scripts/backup-private-state.sh, scripts/restore-private-state.sh, scripts/audit-private-backup.py]
last_verified_at: 2026-08-27
---

# Back up and restore Pixel

Pixel streams private state directly into an `age`-encrypted archive, then writes a checksum and detached SSH signature. It does not write a plaintext tarball. Encryption protects confidentiality; the trusted signer authenticates the producer.

## Custody prerequisites

- Keep the `age` decryption identity offline from Pixel.
- Preserve the deployment's public `backup-allowed-signers` trust anchor separately from the archive set.
- Select an absolute backup directory outside the OpenClaw home and Pixel workspace.
- Keep enough space for the encrypted archive and, before live replacement, an automatic pre-restore safety backup.
- If Deep Work private roots are included, keep its external knowledge-vault credential outside every captured root and follow its key-specific restore contract.

## Create a backup

```bash
./pixel backup /secure/pixel-backups age1CLIENTBACKUPRECIPIENT
```

For unattended creation, set `PIXEL_BACKUP_AGE_RECIPIENT` instead of putting even the public recipient into a scheduler command. Preserve the returned archive, `.sha256`, and `.sig` together.

Backup creation is not recovery proof.

## Validate without restoring

```bash
backup=/secure/pixel-backups/pixel-private-TIMESTAMP.tar.gz.age
./pixel restore "$backup" --identity /offline/age-identity --validate-only
```

Validation checks the checksum, canonical detached signature, trusted signer, decryption, bounded archive structure, allowlisted destinations, and unsafe member/path conditions. Use `--signers ABSOLUTE_ALLOWED_SIGNERS` when the trusted signer file is not at the configured default.

## Rehearse into an empty root

```bash
./pixel restore "$backup" --identity /offline/age-identity \
  --rehearse /var/tmp/pixel-restore-rehearsal
```

The rehearsal root must be new and empty. Inspect the rehearsal and run the prescribed verification without pointing active services at it. Destroy or retain the rehearsal according to the client's private-data policy.

## Perform a reviewed replacement

Only after validation, rehearsal, owner approval, and a planned outage:

```bash
# Requires exact confirmation; review before running.
./pixel restore "$backup" --identity /offline/age-identity --replace --confirm
```

If live destinations exist, replacement requires `--replace --confirm` and a configured backup recipient so Pixel can create a fresh pre-restore safety backup. The transaction stages only audited roots and runs post-restore verification; a failed verification restores the preceding live state.

Do not delete the pre-restore backup until the owner accepts the restored deployment. Re-run a real configured-model conversation and applicable tool checks after host verification.

## Stop conditions

Stop on an absent/untrusted/non-canonical signature, wrong identity, checksum mismatch, symlink or special archive member, path escape, unexpected destination, knowledge-vault key/tombstone conflict, residual transaction, or failed verification. Never bypass authenticated decryption, copy a historical vault into place, or manually mark a receipt complete.
