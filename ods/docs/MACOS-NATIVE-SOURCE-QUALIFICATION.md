# Native Source lifecycle qualification

This note describes the native Source custody boundary used by Portal on macOS.
It is a technical qualification record, not evidence of a physical Mac run.

## Supported retirement boundaries

- Normal retirement starts while the installed Core services are still available.
  The outer ODS uninstaller invokes native retirement before stopping Core or Docker.
- The actual old protected bridge selects the installed owner, gateway, runtime,
  and configuration. Source disables its triggers and stops all recorded roles.
  A fresh boot, label, process, identity, and file-custody proof precedes withdrawal.
- Private Source generations use a distinct installer-retirement transaction.
  The publisher revokes the old execution authority and preserves its definitions
  and journals at the complete v1 boundary before Core is stopped.
- An inert code kit without private Source state uses a separate code-only
  inventory. It does not invent a generation, token, broker account, or withdrawal.
- A known legacy Core with the fixed public provider descriptors and no native
  Source component, data, metadata, account, or actors has a separate absence proof.
  A failed Source inspection is never reclassified as this legacy case.
- The protected Source cursor preserves only the exact witness-bound state,
  logs, kit, current metadata, and ledger-derived history. Its archive identifier
  is fixed by the immutable retirement intent.
- Source finishes before the fixed Core archive plan starts. Core retries keep
  the original plan, archive, and object inodes even after settings or runtime
  directories have moved. The original controller lock, witness, and cursor are
  the last control objects archived.

State is preserved by rename. A lost acknowledgement observes the same object
at its fixed destination; it does not choose another archive or copy data.
Token contents are never read, copied, restored, or used as a recovery shortcut.
Token and environment inodes remain unchanged. The dedicated Source identity
is retained and revalidated with the existing Operations identity contract.
New Source jobs are not activated by retirement or an installer rebind.

## Cases that remain refused

- A fresh Source-bearing retirement whose Core services were already stopped
  without this transaction's witness is not automatically adopted.
- Source-bearing `--prepare-stopped-recovery` and `--resume-stopped-recovery`
  are not qualified by this implementation. The public descriptor-only fresh
  stopped-recovery case is also outside the new ordinary classification path.
  The pre-existing no-descriptor legacy schema 1/2 assist path is unchanged.
- A reboot during a retirement transaction does not refresh its old boot witness.
  This differs from the separately qualified pending-initial-generation recovery.
- An old protected bridge/publisher without a required Source capability is
  refused when Source state exists. Prepared code is not treated as active data.
- Unknown or mixed metadata, unbound historical names, changed owner or DS GUID,
  new token/state, replaced inodes, live actors, changed authority bytes, and
  incompatible target templates preserve the current state and stop the operation.
- Cross-filesystem renames are not replaced with a copy/delete fallback.

Keep private receipts, cursors, archives, and existing data after a refusal.
Deleting a pending record or editing a private receipt is not a recovery method.

## Failed first-sidefile preservation

The explicit `archive-sidefiles` installer operation covers one narrow legacy
failure: a restored schema 5 update, with no prior Source kit, identity or private
state, left an empty Root-owned `gateway.env` with the owner directory's inherited
group before recording its inode. The caller must select the exact receipt and
file device/inode pairs observed in the failed attempt. `onboarding.json` must
remain absent. Filled, linked, ACL-bearing, replaced or unrelated files are
refused; this operation never completes or adopts the unbound file.

The original controller lock, current restored deployment and fresh Source
absence checks precede preservation. The original file and sidefile receipt
move by exclusive rename without changing their bytes, ownership or inodes.
Private copies of the frozen context and restored journal keep the archive
verifiable after normal runtime-attempt retirement. A complete immutable manifest
precedes the moves; only a terminal Root ledger entry makes the exact archive
visible to later absence, first-connection and retirement inventories. Unlisted
names remain refusals and no historical record grants Source execution authority.

After a sealed manifest exists, an interrupted move can resume only with the
same captured inodes. Interruption while preparing the capsule, before a complete
manifest exists, preserves the partial capsule and both originals and refuses
automatic retry. This narrower boundary must not be reported as recovery from
every interruption. The mutator handles the first selected archive or replay of
that same archive; it does not prune or merge unrelated preservation histories.

An explicit `recover-runtime --rollback-before-source-publication` operation
covers a different boundary: Core is active, its first Source publication never
began, and both owner sidefiles have complete inode-bound receipts. Ordinary
active recovery still resumes the selected candidate. The explicit rollback
requires a schema 5 context and journal, all approved after-images, retained
admission, original absent kit/identity proof, and fresh absence of Source state,
metadata, identity and actors. It does not roll back a partially published Source.

An explicit `recover-runtime --quarantine-empty-source-stage` operation covers
another narrow interruption: the active installer's inert code-kit staging
created a root-owned empty directory or one empty file before recording its
inode. It requires the exact active journal, Source absence proof, current
candidate selection and live admission hold. Recorded files must still match
their qualified prefixes. A private receipt is sealed before an exclusive move
preserves the unrecorded inode in protected quarantine; it is never adopted as
Source code. Contents, unknown names, changed recorded files, conflicting
receipts or a changed hold are refusals. The existing bounded publication still
has to complete separately; quarantine alone is not Source readiness.

Before stopping Core, the operation seals private context/journal copies and a
fixed preservation manifest. Only after every service is confirmed stopped may
it exclusively rename the two owner sidefiles and both Root receipts into the
capsule. Their bytes, ownership and inodes are preserved. The previous archive
ledger is compared and extended without dropping its earlier entries. A lost
move or ledger acknowledgement replays only the same sealed selection; an
unsealed preparation remains retained and refuses automatic adoption.

The schema 2 capsule has exactly seven objects. Readers validate its original
active journal, sidefile contents and ready receipts, prior archive images and
the absence of history cycles. Retirement records the owner UID only for the
two exact sidefile leaves from an independently qualified capsule; every other
history object keeps its Root custody requirement. A historical capsule is
retained data, not authority to run Source or enable a connection.

## Bounds

| Boundary | Limit |
| --- | --- |
| Retirement history | 1,024 captured nodes, 32 MiB total, maximum depth 16 |
| Source archive move plan | 1,024 fixed items |
| Core archive move plan | 1,024 fixed items |
| Core authority hashes | 512 files |
| Recorded Core process tree | 4,096 birth-identity rows per service |
| Installer history ledger | 64 explicit entries |
| Pending-initial boot history | 16 explicit transactions |
| Failed-sidefile preservation history reader | 16 explicit archives |
| Read-only OS census output | 1 MiB per command, bounded command deadline |

Exceeding a bound preserves the records and refuses the operation. No filename
prefix or capacity failure grants permission to delete or prune Source history.
Source-bearing archives use a distinct receipt schema and are excluded from
legacy completed-retirement pruning.

## Evidence and remaining platform gate

Owned-file controls exercise actual immutable bundle verification, Root
contexts/journals, publication, withdrawal, renames, inode preservation, typed
receipt CAS, and lost-acknowledgement recovery. Apple ownership, Directory
Services, ACL, and launchd observations are simulated explicitly on Linux.
They do not establish Darwin kernel, service, Metal/model, or physical Mac behavior.

Earlier records remain bound to their original Source 4.3.45 identity. Separate
coupled checks use the genuine Source 4.3.46 bundle and pins; they do not relabel
the earlier evidence. The composed release preserves that 4.3.46 selection.

The code-only installer support capsule has a separate closed manifest from
the 111-file runtime Source deployment. Its fixed peers execute in fresh
isolated Root Python processes. Cold bridge discovery reads the installed
Root definitions and current owner configuration; it does not accept candidate
constructor fields as installed authority. This boundary is exercised separately
from the file-only archive controls. A physical Apple Silicon run of the composed
release remains required before claiming macOS acceptance.
