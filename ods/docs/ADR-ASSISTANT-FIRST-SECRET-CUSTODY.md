# ADR: Assistant First host-owned secret custody

## Status

Accepted for the opt-in Assistant First source path. The feature remains off
unless `ODS_ASSISTANT_TRANSACTIONS_ENABLED` is explicitly enabled. Linux is the
only qualified custody platform in this phase; existing Windows and macOS
installation paths are unchanged.

## Decision

Assistant First configuration secrets are staged by the native ODS host agent,
not by the assistant runtime or Dashboard transaction store. The host stores one
owner-only record per extension transaction under:

```text
<ODS_DATA_DIR>/assistant-first/secrets/<transaction-id>.json
```

The record is bound to the exact transaction ID, plan hash, and configuration
schema hash. Its public handle is a random opaque reference. Secret-derived
digests are not returned or persisted outside the record because low-entropy
values could otherwise be guessed offline.

The authenticated host API exposes only three feature-gated POST operations:

- `/v1/assistant-first/secrets/stage`
- `/v1/assistant-first/secrets/status`
- `/v1/assistant-first/secrets/delete`

Stage accepts secret values once and returns only the opaque reference, binding
hashes, and sorted presence keys. Status requires the complete binding and the
opaque reference. Delete is idempotent when the transaction has no record, but
an existing record is deleted only when every binding and the reference match.
No operation returns secret values, byte counts, content hashes, timestamps, or
submitted values in an error.

## Filesystem contract

The first qualified implementation is POSIX dirfd-based and fails closed unless
`O_DIRECTORY`, `O_NOFOLLOW`, and advisory file locking are available. It:

- verifies the data root is an owner-controlled directory that is not group or
  world writable;
- creates the `assistant-first` and `secrets` directories with mode `0700`;
- opens every directory and file relative to a verified directory descriptor;
- rejects symlinks, non-regular files, hard-linked records, unexpected owners,
  broad file modes, and inode swaps;
- serializes processes with an owner-only advisory lock;
- writes same-directory temporary files with mode `0600`, fsyncs the file,
  atomically replaces the record, and fsyncs the directory;
- removes only strictly named, owner-only orphan temporary files while holding
  the lock; and
- treats a corrupt record as an integrity failure rather than overwriting it.

The on-disk record is the secret store itself and therefore necessarily contains
the values. Plans, approvals, configuration receipts, transaction journals,
lockfiles, API responses, logs, browser persistence, and model context may store
only the opaque reference and presence state.

## Idempotency and replacement

A repeated stage with the same idempotency key and byte-identical canonical
secret map returns the original reference. Reusing an idempotency key with
different values fails. A new idempotency key atomically replaces the prior
record and produces a new reference, invalidating the previous one.

## Dashboard integration

The opt-in production transaction runtime now connects Dashboard's typed
configuration manager to this host custodian. The manager loads the plan
envelope and exact plan hash from the durable `TransactionStore`, validates the
full Manifest v2 configuration schema, sends secret values directly to the
host endpoint, and persists only non-secret values plus the opaque reference.
It revalidates the reference and all hashes before approval; the future
lifecycle adapter must repeat that validation immediately before apply.

Windows and macOS require separate platform-secret-provider, service-management,
backup, update, and rollback qualification before these routes can be enabled on
those systems.
