# Pixel Deep Work knowledge vault

Status: implemented through the supervised local worker and recovery boundaries, but still
disabled unless an exact controller configuration opts in. The gateway never receives vault
content. The local owner page receives only content-free readiness and lifecycle guidance.
Supported production status still depends on the release qualification gates below.

The vault gives Deep Work an intentionally narrow way to reuse private local knowledge
without turning ordinary agent memory into ambient authority. An owner explicitly admits
one exact source into one client partition and chooses its classification and retention.
A job can later request bounded, cited excerpts from that same owner/client partition at
one exact vault state. No match means no context. There is no recent-item, semantic-neighbor,
or model-generated fallback.

## Data boundary

- Source title and text are AES-256-GCM encrypted under a fresh random per-source key.
- The source key is wrapped by a vault/client key derived with HKDF-SHA-256 from an external
  256-bit master key. The master key is never written into the vault.
- The persisted key verifier is a keyed digest, not the master key. It prevents a wrong key
  from being misreported as an ordinary empty search.
- Search uses client-specific 128-bit truncated HMAC-SHA-256 term fingerprints. Plaintext
  terms, queries, titles, and excerpts are not written to the index or claim ledgers.
- Exact source and title SHA-256 values are private metadata. They can reveal equality to a
  party that already knows candidate content, so the complete vault remains owner-only.
- The keyed lexical index leaks bounded structural facts inside the private vault: source,
  chunk, and unique-term counts and repeated-term equality within one client partition.
- Plaintext necessarily exists in the trusted controller's memory while ingesting or
  returning an authorized excerpt. Encryption at rest does not protect a compromised
  controller process or an unlocked machine.

The current index is deliberately lexical and deterministic. It does not load an embedding
model, infer relevance from a model name, or send data to a provider. A future semantic
index requires its own pinned model receipt, embedding-drift tests, rebuild contract, and
privacy review before it can replace or supplement this profile.

## Exact flows

Ingestion is an expiring, single-use authorization bound to one source ID, owner, client,
classification, parser, byte count, title hash, content hash, retention choice, and exact
owner approval. The controller validates all text and searchable-term ceilings before
burning the authorization. It writes encrypted chunks and the index into a private staging
directory, syncs them, then publishes the complete source with one atomic rename. A crash
after authorization consumption cannot replay the authorization; recovery discards an
unfinished ingestion stage.

Retrieval is an expiring, single-use read bound to one job, checkpoint, owner, client,
query hash, classification ceiling, minimum relevance, result ceiling, and exact vault
head. Any source/deletion change invalidates the request. Results preserve source
classification, deterministic rank order, ciphertext/source hashes, and a local citation.
Titles and excerpts are explicitly untrusted data with no instruction, tool, network,
external-effect, scope-expansion, or completion authority.

Deletion is destructive and exact. A short-lived owner authorization binds one source
receipt. Its execution-start record is durable before the source enters the deletion
state. A content-free tombstone is synced before ciphertext is removed. Recovery completes
an already-started deletion and rejects ambiguous or unbound residue. A retention date is
pre-approval for the local purge routine to issue this same exact deletion flow after the
date; it is not authority to delete another source.

## Backup and key custody

Backups may contain encrypted source keys and ciphertext. Keep the master key in a separate
OS-backed secret store and never put it in Git, the release tree, a worker mount, browser
storage, an MCP pack, or a model prompt. Losing the key makes the backup unrecoverable.
Copying both key and vault together defeats the main at-rest boundary.

A deletion cannot erase an older independent backup. Every deletion receipt therefore
reports that backup propagation is required. The lifecycle foundation now reconciles the
exact authoritative live tombstone ledger into a historical vault through a separately
staged, deep-audited atomic replacement. It rejects a conflicting tombstone or a restored
source whose owner, client, identity, or content hash differs, and it works when the live
vault and historical backup use different generations of the external key. Pixel's private-
state restore hook now enforces this sequence before a historical vault can become active.
It quiesces Deep Work, copies the historical vault into separate private staging, reconciles
the active tombstones, removes resurrected ciphertext, rotates historical wrapping to the
current external key, and deep-audits the result before the general atomic restore transaction.
Any failure leaves the active vault in place or rolls it back.

The key loader accepts only a 32-byte key represented as exactly 64 lower-case hexadecimal
characters plus one newline in an explicitly selected, owner-private, single-link file named
`pixel-knowledge-vault-key`. The goal service projects the reviewed source through systemd
`LoadCredential`; the worker receives neither that credential nor the vault filesystem.
Keep the long-lived source outside all captured backup roots. The reference layout uses
`/etc/pixel-work-credentials/pixel-knowledge-vault-key`, separate from `/etc/pixel-work`.
The reviewed setup command can generate this key without displaying it; production service-
account ownership, platform-specific secret-store installation, and recovery-key custody
remain deployment gates.

Rotation stages a complete private vault, decrypts and verifies every active source with the
old key, rewraps each random source key, recomputes every client-keyed search fingerprint,
writes a content-free rotation receipt, and deep-audits with the new key before one atomic
publication. The old live tree is retained only as transaction rollback custody and removed
after verification. A fixed private transaction journal supports explicit recovery from each
publication boundary; ambiguous or unjournaled residue fails closed.

## Trusted-terminal lifecycle

`./pixel work-knowledge` is the disabled foundation's operator entry point. First create
owner-private vault and credential parent directories, then run `setup-review` with exact
`--vault`, `--vault-id`, and external `--credential` paths. Repeat those options with
`setup-apply --confirm-review-sha256 HASH`. Apply generates a random key directly into a new
mode-`0600`, single-link `pixel-knowledge-vault-key`, builds and deep-audits the empty vault in
a sibling private stage, and atomically publishes it without printing the key. It never
overwrites a credential or vault. If a process stopped after the credential or stage became
durable, a fresh review binds and safely resumes that exact state.

`status` performs
a complete authenticated audit but returns only counts and one lifecycle-head digest.
`ingest-review` reads an explicitly selected owner-private title file and text source, then
shows the title, source filename, classification, retention, byte count, and exact hashes.
It does not mutate the vault. Repeating the same options with `ingest-apply` and the emitted
`--confirm-review-sha256` ingests only those exact bytes; any file, option, partition, or
vault-head drift invalidates confirmation. The title file is one UTF-8 line ending in one
newline, and both input files must be real, owner-private, and single-link.

`delete-review` decrypts only the selected source title and displays its classification,
retention, size, content hash, and reason. `delete-apply` requires the exact fresh review
hash and returns a content-free tombstone result. Rotation and historical reconciliation
use the same review/apply split, require separately selected exact credential files, and
also require `--controller-offline confirmed`. This flag is an explicit operational
attestation, not a service-state detector; production service wiring must stop and verify
the controller before invoking either operation. Plaintext source content and key material
never appear in status or mutation receipts.

`query-review` binds one owner-private single-line query file, job/checkpoint, vault head,
classification and score ceilings, result count, and a new owner-private output path. It
performs no retrieval. `query-apply` requires that exact fresh review, performs one local
single-use read, and writes the plaintext retrieval contract only to that new private file;
stdout remains content-free. Production goal cycles perform the same checkpoint-bound read
in memory and append its explicitly quoted, untrusted data to one local-only worker attempt.

For the plain-language path, use `./pixel work-knowledge-guide setup|add|find|remove|rotate|reconcile`
with the same exact local options. It displays a bounded human review, escapes terminal-control
characters in untrusted titles and filenames, and asks for `APPLY` plus the final twelve
characters of that review's digest. Empty or incorrect input cancels without mutation. The
guide then calls the strict core apply operation with the complete digest; it does not create
an alternate authority path. Private bytes and keys remain outside the browser and no key is
printed by the guide.

## Encrypted backup and restore

Set `PIXEL_DEEP_WORK_BACKUP_ENABLED=1` in the private deployment environment to include the
separate Deep Work state, configuration, and encrypted vault roots in `./pixel backup`.
`PIXEL_WORK_STATE_ROOT`, `PIXEL_WORK_CONFIG_ROOT`, and `PIXEL_KNOWLEDGE_VAULT_ROOT` default to
`/var/lib/pixel-work`, `/etc/pixel-work`, and `/var/lib/pixel-knowledge/vault`. Pixel refuses
the backup when `PIXEL_KNOWLEDGE_VAULT_CREDENTIAL` is inside any captured root, and it stops
and later restores the exact previously active `pixel-work-*` units around archive creation.

Validation and isolated rehearsal do not need a vault key. An actual vault restore additionally
requires the historical backup key, the current external key, and the vault ID:

```bash
./pixel restore /secure/pixel-private-TIMESTAMP.tar.gz.age \
  --identity /offline/age-identity --replace --confirm \
  --knowledge-vault-id knowledgevault-012345abcdef \
  --restored-knowledge-key /offline/historical/pixel-knowledge-vault-key \
  --current-knowledge-key /etc/pixel-work-credentials/pixel-knowledge-vault-key
```

The keys are copied only into short-lived owner-private restore custody, never into the
backup or restored roots. A live vault is the authoritative deletion ledger. On a clean
host, the staged historical vault is still rotated to the selected current key before use.

## Qualification already covered

The deterministic suite covers encrypted-at-rest canaries, explicit approval, exact source
binding, single-winner ingestion races, single-use retrieval, owner/client isolation,
classification ceilings, retention, no-match behavior, prompt-injection text as untrusted
data, head drift, wrong keys, Unicode multi-chunk reconstruction, deterministic ranking,
hard-linked and corrupted state, encrypted backup restore, exact deletion, purge idempotence,
reviewed/resumable first-time setup, crash recovery, strict external credentials, atomic key rotation, interrupted transaction
recovery, cross-generation backup/tombstone reconciliation, conflicting historical state,
and linked or substituted lifecycle ledgers.

Before enablement, Pixel still needs two exact-candidate supported-host passes for the complete
systemd credential and backup/delete/restore journey, production OS credential installation,
accessibility/first-user testing of the plain-language trusted-terminal journey, and finalized
incident/key-loss/restore runbooks.
