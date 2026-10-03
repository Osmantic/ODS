# Upgrade and rollback

## Signed release intake

Pixel 4.1.0 is the Deep Work successor and requires Pixel 4.0.0 or later for in-place
upgrade. Its manifest deliberately retains the bootstrap qualification-policy value that
the frozen Pixel 4.0 updater understands. This is a single documented compatibility
bridge, not permission for future releases to reuse bootstrap mode: the Pixel 4.1
controller requires `forward` policy for 4.1.1 and later. A clean 4.1 installation remains
available when no eligible 4.0 deployment exists. Do not patch or replace the installed
4.0 updater to force an upgrade. Pixel 4.2 and every later release line must declare
`forward`; release generation fails closed if the current manifest attempts to reuse the
bootstrap bridge.

Pixel 4.3.23 is the one-release compatibility bridge for failed-rollback evidence
archiving. Its signed `releaseUpdate` object deliberately retains the exact pre-archive
shape understood by the qualified Pixel 4.3.21 predecessor controller, while the new
archive operation is declared separately in the signed `releaseArchive` object. The
4.3.23 verifier accepts that split only for 4.3.23 and requires the separate policy
byte-for-meaning exact; later releases return to the archive-aware `releaseUpdate` shape.

Release packaging creates the archive, standalone CycloneDX SBOM, SLSA provenance, and
an unsigned update envelope plus checksums. The envelope is not an approval. Before
promotion, a dedicated qualification signer may bind the exact Candidate bundle:

```bash
./pixel release-qualification-sign \
  --envelope "$(realpath dist/pixel-VERSION.update.json)" \
  --signing-key /absolute/private/qualification-ed25519 \
  --confirm

./pixel release-qualification-inspect \
  --envelope "$(realpath dist/pixel-VERSION.update.json)" \
  --allowed-signers /absolute/trust/pixel-qualification-allowed-signers \
  --identity pixel-qualification
```

That signature uses the separate `pixel-release-update-qualification` namespace and
cannot be consumed by `update-inspect`, staging, activation, publication, or the
production trust path. It proves only that reviewers exercised the exact archive and
source binding that may later be released.

Maintainers must promote the exact Pixel/OpenClaw/plugin/source combination to
`supported` only after every required qualification gate passes, then sign the same
fully validated envelope from a protected release workstation:

```bash
./pixel release-sign \
  --envelope "$(realpath dist/pixel-VERSION.update.json)" \
  --signing-key /absolute/private/release-ed25519 \
  --confirm
```

The signed envelope names two source identities: `sourceCommit` is the exact packaged
release and `qualificationSourceCommit` is the preceding functional source that passed
qualification. The signer proves the latter is an ancestor and requires the final delta
to update exactly `OPENCLAW-COMPATIBILITY.json`, its generated Markdown table, and the
versioned live-audit record, each as an ordinary tracked file. It also requires the repository HEAD/tree and clean worktree
to match the envelope. This avoids the impossible requirement that a commit contain its
own hash while preventing unqualified code, configuration, schema, or workflow changes
from entering the release.

The production command refuses Candidate releases, qualification signatures, non-Ed25519
or loosely protected keys, changed
artifacts, stale signatures, missing versioned audit/table files, ambiguous or incompatible source evidence, and any bundle
that fails the same structural checks used by intake. It signs private copies of the
already-read key and envelope so a path swap cannot change the reviewed bytes.
Before using the key, it also compares every archived source file byte-for-byte with the
exact Git tree, requires the package script's normalized file/directory modes and exact
file/directory set, and permits only the separately bound generated SBOM addition.

Obtain the publisher's allowed-signers file separately from the release artifacts. Never
trust a key merely because it arrived inside the candidate bundle. Place the envelope,
its `.sig`, the archive, SBOM, and provenance in one private intake directory, then run:

```bash
./pixel update-inspect \
  --envelope /absolute/intake/pixel-VERSION.update.json \
  --allowed-signers /absolute/trust/pixel-release-allowed-signers \
  --identity pixel-release
```

Inspection verifies the detached `pixel-release-update` signature and every signed
artifact/source/compatibility binding. It parses the compressed archive in memory with
path, type, count, member, compressed, and unpacked-size limits; it does not extract or
execute candidate code. A `verified` receipt is not authorization to install. To make an
eligible forward release available for later rehearsal, use the same independently
provisioned trust inputs and confirm private staging:

```bash
./pixel update-prepare \
  --envelope /absolute/intake/pixel-VERSION.update.json \
  --allowed-signers /absolute/trust/pixel-release-allowed-signers \
  --identity pixel-release \
  --confirm
```

Preparation takes the deployment lock, verifies the complete signed bundle again, and
copies the exact envelope, signature, archive, SBOM, and provenance into owner-only
`$PIXEL_INSTALL_DIR/update-staging`. The stable candidate ID is the version plus the full
envelope hash. Repeating the same request revalidates every staged file and is idempotent;
an interrupted private temporary copy is removed only when its exact bounded file set is
safe. Unknown entries, changed staged bytes, unsupported host versions,
same-version/downgrade releases, ineligible
upgrade floors, and more than eight retained candidates fail closed. The receipt contains
no local path and explicitly records that candidate code was neither extracted nor run.

Rehearse the exact staged candidate ID before considering activation:

```bash
./pixel update-rehearse \
  --candidate-id pixel-VERSION-FULL_ENVELOPE_SHA256 \
  --allowed-signers /absolute/trust/pixel-release-allowed-signers \
  --identity pixel-release \
  --confirm
```

Rehearsal revalidates the signature, staged receipt, artifacts, source identity, upgrade
floor, and exact supported host under both deployment and staging locks. It safely
materializes only regular archive files into an owner-only normalized tree, bounds paths,
counts, sizes, disk reserve, and retention, and atomically publishes without replacement.
Using the installed pinned Node, Python, and Bash parsers, it checks the candidate's fixed
release JSON, shell, JavaScript, and Python surfaces. Those parsers do not run candidate
programs; rehearsal performs no network operation and does not read or change the active
deployment. Repeating it rehashes and reparses the tree. A changed source file, receipt,
toolchain, staged artifact, unsafe interrupted tree, or syntax failure stops the process.

Activation, update-bound rollback, interrupted-receipt recovery, and completed-update
cleanup are implemented as terminal-only exact-hash transactions. After rehearsal,
derive an activation hash without changing the deployment:

```bash
./pixel update-activate --preview \
  --candidate-id pixel-VERSION-FULL_ENVELOPE_SHA256 \
  --allowed-signers /absolute/trust/pixel-release-allowed-signers \
  --identity pixel-release
```

Review the complete preview and repeat its `activationHash` with `--confirm`. Pixel first
creates a single-use claim, then runs the candidate's fixed configure, pinned bootstrap,
plan, transactional apply, and verification sequence against the existing private onboarding
file. Bootstrap establishes the exact candidate sandbox image and preserves the active image
for offline rollback inside the already-claimed execution boundary. The result
binds the exact candidate, active version, phase, and rollback availability without
including onboarding content or local paths:

```bash
./pixel update-activate \
  --candidate-id pixel-VERSION-FULL_ENVELOPE_SHA256 \
  --allowed-signers /absolute/trust/pixel-release-allowed-signers \
  --identity pixel-release \
  --activation-hash FULL_ACTIVATION_SHA256 \
  --confirm
```

If the activated release must be reverted, obtain a fresh exact rollback preview and
repeat its `rollbackHash`. The trusted current controller restores the prior release;
candidate code never implements the rollback:

```bash
./pixel update-rollback --preview \
  --candidate-id pixel-VERSION-FULL_ENVELOPE_SHA256 \
  --allowed-signers /absolute/trust/pixel-release-allowed-signers \
  --identity pixel-release \
  --activation-hash FULL_ACTIVATION_SHA256

./pixel update-rollback \
  --candidate-id pixel-VERSION-FULL_ENVELOPE_SHA256 \
  --allowed-signers /absolute/trust/pixel-release-allowed-signers \
  --identity pixel-release \
  --activation-hash FULL_ACTIVATION_SHA256 \
  --rollback-hash FULL_ROLLBACK_SHA256 \
  --confirm
```

A successfully rolled-back candidate may be reactivated only by its previously activated
release controller. Run `./pixel update-reactivate --preview` with the same candidate,
trust, identity, and activation hash, then repeat the returned `reactivationHash` with
`--confirm`. Each attempt is single-use. Immediately before the candidate apply path makes
its first live change, it durably writes an owner-private live-mutation marker into that
attempt's receipt directory.

If an attempt fails with the restored release still active, no rollback marker or rollback
claim, and **no live-mutation marker**, `./pixel update-reactivation-recover --preview` may
offer `authorize-reactivation-retry`. Repeating that recovery hash with `--confirm` appends
an immutable retry authorization; it does not execute candidate code. A later
`update-reactivate --preview` then returns a distinct hash that binds the prior claim,
failure result, and authorization, and the new attempt receives a separate append-only
receipt directory. Marker-present, changed-active, rollback-started, missing-result,
malformed, unknown-custody-entry, or forked histories remain manual review and cannot be
deleted or retried automatically.

After an interruption, run `./pixel update-recover --preview` with the same candidate,
trust, identity, and activation hash. Its exact-hash confirmed mode may finalize the
missing activation or rollback receipt after verifying observed state; it cannot rerun
candidate code or resume a partial deployment. After a completed rollback, use
`./pixel update-cleanup --preview` and repeat its exact `cleanupHash` to quarantine and
remove only the verified staging, rehearsal, and activation workspaces while retaining a
path-free audit tombstone. Never infer a recovery or cleanup hash, and never reuse a
preview after any relevant state changes. None of these operations is available in the
browser.

When all eight candidate slots are occupied but no journey satisfies cleanup, use
`./pixel update-archive --preview` only against one terminal failed rollback chain without
reactivation custody. The exact preview binds the signed candidate, immutable failed
rollback receipts, active version, unrelated live rollback marker, three complete
content-hash manifests, active service state, fixed same-filesystem destination, and the
eight-to-seven capacity transition. Repeat its full `archiveHash` with `--confirm`.
The transaction publishes an owner-private immutable replay-tombstone claim and manifest before atomically moving
the candidate, rehearsal, and activation roots. Each parent is fsynced, every archived
tree is rehashed, and a terminal result is written only after the count is exactly seven.
Symlinks, hardlinks, extended attributes, foreign ownership, special modes, collisions,
drift, an active candidate, its live rollback marker, any reactivation workspace, or an
inactive production service stop before a new move. Interrupted moves resume from the
same claim; the complete failed chain remains preserved and the candidate ID is permanently
unavailable for restaging. Do not replace this transaction with manual deletion or moves.

Release maintainers still complete the source and client qualification procedure below
before signing or publishing a candidate:

1. For a Pixel release that keeps the current Supported OpenClaw baseline, edit release
   pins only in `RELEASE-MANIFEST.json`, update `OPENCLAW-COMPATIBILITY.json`, regenerate,
   and run the release contract. For a new OpenClaw release, do not hand-edit either
   file: follow `UPSTREAM-INTAKE.md` from `./pixel upstream check` through signed
   qualification and explicit promotion.
2. Run `node scripts/generate-release-files.mjs --write`, review every generated diff,
   then complete `UPSTREAM-RELEASE-CHECKLIST.md`, including two clean assurance passes,
   the three-lane real-runtime matrix, isolated canary, and rollback evidence.
3. On the client host, create `./pixel backup /secure/location age1CLIENTBACKUPRECIPIENT`.
4. On the client, use the signed inspect, prepare, rehearse, activation-preview, and
   exact-confirmation sequence above during the agreed window. Do not replace it with an
   unsigned archive or direct candidate invocation.
5. Repeat the acceptance checklist, including Gmail read and unconfirmed Calendar
   mutation rejection.

Apply and rollback stop the gateway and retire only containers in Pixel's exact
agent-scoped OpenClaw sandbox namespace before switching releases. After an upgrade,
run one harmless workspace tool turn and then `./pixel verify`; the verifier must bind
the recreated container to the active image digest and release instead of merely
trusting the mutable image tag.
The transition also rebuilds OpenClaw's persisted plugin registry. Verification fails
if either Pixel custom plugin still resolves through an older immutable release even
when the configured load path uses the moving `current` symlink.

### Broker executable bytes across apply and rollback

The source, ops, and frontier brokers run as isolated system services from fixed
root-owned install directories (for example `/opt/pixel-source-broker/broker.py`),
which are separate from the immutable release tree selected by the `current` symlink.
Ordinary `./pixel apply --confirm` now transactionally updates the executable bytes of
every **already-installed, enabled** broker to match the active release, so a release
upgrade actually activates the new enforcement code on the host.

- Apply backs up the exact prior installed bytes before overwriting, installs the new
  bytes from the active release, and restarts/validates each affected broker service.
- If apply fails after touching a broker, its compensation restores the exact prior
  installed bytes. Explicit `./pixel rollback --confirm` likewise restores them.
- `./pixel verify` checks that every installed, enabled broker's executable bytes
  hash-match the active release and fails closed on any drift.
- For the source broker this includes `action_journal/__init__.py`; for the frontier
  broker it includes `verify-codex.py`.
- Apply never installs a disabled or not-yet-installed limb: only limbs that are both
  enabled in configuration and already present on the host are updated.

This privileged broker-byte transaction uses only the fixed direct `sudo` surface. The
release operator's narrow trust boundary is deliberately **not** broadened to install
broker bytes. When that operator is enabled, the owner must explicitly set
`PIXEL_BROKER_BYTES_DIRECT_SUDO=1` for the reviewed apply or rollback command; otherwise
the transaction fails closed before deployment mutation.

### Web Courier policy-refusal status

The workspace `scripts/browse.sh` client now exits with status `3` when the Courier's
response begins with the exact `# Request refused by policy` envelope. It still writes the
complete refusal body to standard output. A retrieved page that merely contains those words
later in its content remains a successful status `0`; timeouts and missing responses remain
status `1`. Callers should branch on the status rather than searching response text.

Planning now fails if `.env` and the generated deployment still name an older Pixel
release. This is intentional: rerun the same private onboarding file with `--force`
before reviewing a new release, rather than editing the cached version by hand.

### Work-provider state across upgrade and rollback

The multi-provider plane is additive and local-only by default. Release archives contain
public provider profiles, schemas, adapters, and routing code, but never an API credential,
owner-private provider policy, semantic qualification, run ledger, or routing authorization.
An upgrade therefore cannot enable a remote provider merely because its adapter is present.

Before activating a release that introduces the provider plane, preserve owner-private
provider policy, qualification, credential-custody, and ledger roots in the authenticated
encrypted backup. Restore them only to owner-private storage after the clean bootstrap and
validate their ownership, modes, hashes, expiry, provider identity, exact egress hosts, and
budgets before any explicit remote test. Do not place credentials in the source checkout,
onboarding file, environment, command line, release archive, or update staging directory.

Rollback restores the prior Pixel release and deployment state. It does not delete,
export, convert, or silently activate separately held provider credentials or policies.
If the restored release does not understand the provider plane, keep that private state
dormant until a compatible release is active again. A local-only deployment must remain
functional when every remote policy, credential, qualification, proxy, and network service
is absent.

Maintainers with the former unversioned manifest can create a reviewable v1 copy with
`./pixel release-manifest-migrate /absolute/legacy.json /absolute/v1.json
--openclaw-integrity sha512-... --confirm`. Obtain the integrity from authoritative npm
registry metadata, review the result, replace the repository manifest deliberately,
then regenerate. The migration never overwrites either input or an existing output.

## 3.0 to 3.1

Pixel 3.1 introduces Operations policy schema v2 and does not silently rewrite private
policy. Back up broker state and use `./pixel ops-policy-migrate` to create a distinct
v2 file. Explicitly classify every target environment, inspect the generated
compatibility grants, and replace broad legacy grants with scoped standing grants.
After review, `./pixel ops-policy-tighten POLICY-V2.json --confirm` removes only the
generated v1 compatibility grants and retains an adjacent private backup.

Map reusable action packs in the private onboarding file, re-run configure, install the
broker, and refresh already enrolled runners with `./pixel ops-target-refresh`; this
does not alter the broker SSH key or trust pins. Install reviewed private action and
managed configuration with `./pixel ops-target-actions`.

Before accepting production work, verify that production and `change` automation wait
for a temporary authority lease, test grant expiry/revocation/pause and budget circuits,
and exercise managed verification/rollback only against a disposable fixture. Existing
v1 policies remain loadable for compatibility, but new deployment and autonomy work
should use v2.

## 2.x to 3.0

Version 3.0 introduces explicit capability profiles and the optional Operations Broker.
Existing deployments default to the `chief-of-staff` limb set unless their answer file
sets individual booleans. Review the generated limb matrix before apply. Enabling
Operations requires a private policy, new isolated broker identity/key, and individual
target enrollment; it never imports the operator's SSH key or configuration implicitly.
Do not enable a target until its expected hostname and existing host-key pin are
independently verified. Complete the Operations acceptance exercises before allowing
real work.

## 1.x to 2.0

Version 2.0 deliberately moves Google refresh material out of the gateway owner's
account. Run `./pixel authorize` if a fresh credential is required, then
`./pixel source-broker --confirm` before applying 2.0. Verify projections and complete
the live injection suite. Direct Calendar mutations become proposals and therefore need
the separate `source-show` and hash-bound `source-approve` commands. Existing pending
proposal files remain reviewable; run `source-show` once to create their protected
snapshot before approval.

The confirmed Operations Broker installation restarts its long-running service after
installing a new binary or unit. Verify its active process start time after every
upgrade; copying a broker binary without restarting the service does not activate new
enforcement code.

For a legacy hand-built OpenClaw installation, back up `openclaw.json`, install the new
plugin directory, and use `scripts/migrate-source-broker-config.mjs` to remove the old
plugin ID/path and atomically add the projection plugin. Normal managed deployments use
`./pixel plan` and `./pixel apply --confirm` instead.

This credential migration is not silently reversed by a 1.x release rollback. Exporting
a broker-owned token back to the gateway account would recreate the old trust flaw and
requires a separate, explicit incident or downgrade procedure.

Installed releases are immutable directories selected by an atomic `current` symlink.
`./pixel rollback --confirm` restores the config, gateway/Web Courier environments and
units, managed navigation files, and prior release pointer saved by the last successful
apply. Other private database or workspace content is not rolled backward automatically;
restore it only from an explicitly selected encrypted backup.

Rollback changes the Pixel release and generated gateway configuration. It does not
silently delete broker state, rotate OAuth/SSH credentials, undo remote runner
provisioning, or restore a prior Operations policy. Treat those as separately reviewed
host-state changes.

## 3.2.2 to 4.2 (terminal-only clean migration)

Pixel 3.2.2 is a supported legacy source, but there is **no in-place 3.2.2 update path** to
4.2. The 4.2 release remains `qualificationMode=forward` with `minimumUpgradablePixel=4.0.0`.
Migrating from 3.2.2 is a **terminal-only clean migration**: you first create an authenticated
encrypted backup of the legacy deployment, then perform a fresh 4.2 install into a separate
location, and finally restore private state transactionally. The release never claims or
enables an in-place 3.2.2 update, and no bootstrap/apply/replace/rollback step is automated.

The sanctioned, phased review/rehearsal/activation sequence is provided by
`./pixel migrate-legacy-clean`, which composes the existing trusted restore validation,
`restore --rehearse`, the trusted `./pixel verify`, runtime attestation, and Git source
identity. The plan and rehearsal phases run the **exact legacy 3.2.2 pixel validator** by
passing `--pixel /absolute/legacy-3.2.2/pixel`, because the standard 4.2 restore adds
control/frontier/deep-work-era roots and must keep its exact modern allowlist unchanged. The
plan binds the authenticated 3.2 root contract (`backupRootsSha256`) and backup SHA-256, so the
exact legacy contract cannot drift between phases. The `activate` subcommand does **not** reuse
the legacy validator or its `--receipt`; instead it executes the trusted 4.2 restore in an
explicit **migration-only mode** (`restore --migration`) that builds its allowlist from exactly
the authenticated 3.2 root contract. The authenticated 3.2 manifest may omit new 4.2-era
control/frontier roots, but every declared legacy destination must still be within the known
4.2 destination allowlist — a 3.2 root that 4.2 dropped is rejected, never silently accepted.
It reserves every output/receipt/journal path and
fingerprints the pre-prepared 4.2 configuration before any live mutation, then swaps the legacy
data, restarts services, and keeps the restore's old-path rollback state **armed** (services
may run through the 4.2 verify, which can require them) through the 4.2 verify,
runtime-attestation validation, restore-receipt finalization, and completion-receipt write. Only after those evidence phases succeed does activation commit and
delete the old state. No deployment configuration is regenerated: the 4.2 configuration is
pre-prepared and frozen, and activation proves the frozen files are byte-identical before and
after the legacy data swap. On any failure it leaves a truthful non-pass receipt that records
whether live mutation was rolled back, and never deletes the authenticated backup. All plan,
rehearsal, and completion receipts are owner-only (parent `0700`, file `0600`), written
atomically outside the source repository with no-overwrite semantics, and are content-free
(hashes only, no paths, host, credential, signer, model/provider identity, or user content).

The migration transaction journal must be written to the fixed root-owned custody directory
`/var/lib/pixel-migration-journals` (mode `0700`), directly beneath it with a safe basename.
A privileged helper (run as root via sudo) creates/validates the root-owned mode-0700 custody
directory through a bound descriptor and atomically reserves the exact journal basename
(`O_CREAT|O_EXCL|O_NOFOLLOW`, mode `0600`) under an exclusive `flock` before any live mutation;
the non-root orchestrator never claims it can test availability inside the root-0700 custody.
The reservation is O_EXCL-only and refuses every pre-existing marker (including a stale
reservation for the same contract; there is no implicit stale reclaim in normal activation). The
reserved marker is bound to the authenticated contract, the exact backup SHA256, and the fixed
3.2.2 -> 4.2.0 source/target, and the helper returns a per-invocation reservation token that arm
and abort must present, so a concurrent same-contract activation can never reclaim or arm another
invocation's reservation. Control verbs reject a reserved (not yet armed) journal, and a later
arming step atomically replaces the exact reserved marker with the fully armed transaction after
validating it with the same exact path/sibling/contract/unit validator used by commit and
rollback (failing closed on token/contract/backup mismatch or any malformed/malicious entry).
Ownership of the journal and custody directory is validated by the privileged restore helper
against its own effective uid (root), with no caller- or environment-supplied owner override; the
helper runs as the root euid and invokes only fixed absolute trusted binaries
(`/usr/bin/systemctl`, `/usr/bin/rm`, `/usr/bin/mv`) directly, with no environment/PATH tool
override a privileged euid accepts. The helper holds an exclusive `flock` on the custody directory
through the descriptor-bound journal read, every state operation, and the atomic state write, so
concurrent commit/rollback cannot race. Every armed/progress/commit/rollback/finalization state
transition is a crash-safe atomic replacement (an O_EXCL mode-0600 temp written and fsynced
beneath the bound custody dirfd, verified, renamed over the journal via dirfds, then the custody
directory is fsynced) — never an in-place ftruncate that a power loss between truncate and fsync
could leave truncated and destroy the only rollback state.

Rollback quiesces the exact validated units (stopped and verified inactive) before any filesystem
mutation; if any stop or verification fails, every validated unit is restarted best-effort and no
mutation or rollback progress is recorded, so one unit is never left down by a failure on another.
Rollback persists per-root progress after each root with recorded inode/device/type evidence, so a
retry never deletes an already-restored pre-state. Terminal commit never restarts services (they
are already running and just passed the 4.2 verify) and marks finalization complete truthfully
without a disruptive restart; it tracks filesystem cleanup separately (`cleanup`), is idempotent,
and a cleanup failure is reported truthfully and never triggers rollback. A rolled-back transaction
is never committed and a committed transaction is never rolled back.

**Crash-safe armed-before-swap recovery.** The full transaction journal is durably armed BEFORE
the first live destination rename, and per-root old-snapshot evidence is captured from each live
destination while it is still at destination (the inode/device/type follows the content to
`oldPath` if moved). Arm fails closed under the custody lock unless every `hadOld=1` destination
still exists, every `hadOld=0` destination is still absent, no `oldPath` pre-exists, and every
prepared temporary path is present and safe. After arm, any ordinary failure before the successful
migration-mode return is undone automatically from the shell's EXIT path by the same privileged
rollback. A hard kill (SIGKILL/power loss) never runs any trap, so nothing is invoked
automatically; the armed journal remains the single exact recovery authority. Recovery is
**deterministic resumable recovery**: an operator or resumer runs the explicit
`--migration-rollback` control verb to reconstruct unswapped, half-swapped (destination moved to
old but new not installed), partially swapped, or fully swapped roots exactly and cleans every
temp/old sibling. Before arm, the authenticated backup is the single recovery authority and the
reservation is aborted exactly, never leaving an armed transaction.

Before step 1, record any failed `pixel-source-action@PROPOSAL_ID.service` instances and
preserve their approved snapshots, result files, and processing claims in the authenticated
backup. Pixel 3.2.2 predates the shared external-action journal. A retained processing claim
therefore remains an indeterminate, non-retryable provider outcome after restore; Pixel 4.2
refuses it in both approval and create-reconciliation paths before creating or advancing a
journal and before any provider call. A legacy
terminal result without a matching shared journal remains historical evidence, not permission
to synthesize a 4.2 success record. Do not delete either shape or retry its proposal during
migration. After the backup and rehearsal are verified, retain content-free file hashes and
the exact unit journal as operator evidence. Resetting a failed systemd display is optional
manager-state cleanup only; it is not provider reconciliation and must not remove the retained
files.

```bash
# 1) On the legacy 3.2.2 deployment, create an authenticated encrypted backup.
./pixel backup /absolute/backup-directory <age-recipient>

# 2) Review and bind the plan (validates manifest policy, installed legacy version,
#    authenticated backup, and backup SHA-256 / sourcePixel from the content-free audit).
#    --pixel intentionally runs the EXACT legacy 3.2.2 pixel validator, so the standard
#    4.2 restore allowlist is never widened.
./pixel migrate-legacy-clean plan \
  --pixel /absolute/legacy-3.2.2/pixel \
  --install-dir /absolute/pixel-install \
  --backup /absolute/backup/pixel-private-....tar.gz.age \
  --identity /absolute/age-identity \
  --signers /absolute/backup-allowed-signers \
  --output /absolute/review/plan.json

# 3) Rehearse restore into an explicit new non-live root only (no live mutation).
./pixel migrate-legacy-clean rehearse \
  --pixel /absolute/legacy-3.2.2/pixel \
  --plan /absolute/review/plan.json \
  --backup /absolute/backup/pixel-private-....tar.gz.age \
  --identity /absolute/age-identity \
  --signers /absolute/backup-allowed-signers \
  --rehearsal-root /absolute/rehearsal-root \
  --output /absolute/review/rehearsal.json

# 4) Perform a clean 4.2 install into a separate location, then run the self-contained
#    activation transaction. It uses the TRUSTED 4.2 pixel in its explicit migration-only mode
#    (never the legacy 3.2.2 validator, which does not support --receipt). It reserves every
#    output/receipt/journal path and fingerprints the pre-prepared 4.2 configuration before any
#    live mutation, swaps the legacy data while keeping rollback armed, verifies the live
#    deployment and runtime attestation, and only then commits and deletes the old state. On any
#    failure it leaves a truthful non-pass receipt and never deletes the backup.
./pixel migrate-legacy-clean activate \
  --pixel /absolute/new-pixel-install/pixel \
  --install-dir /absolute/new-pixel-install \
  --plan /absolute/review/plan.json \
  --rehearsal /absolute/review/rehearsal.json \
  --backup /absolute/backup/pixel-private-....tar.gz.age \
  --identity /absolute/age-identity \
  --signers /absolute/backup-allowed-signers \
  --restore-receipt /absolute/review/restore-receipt.json \
  --completion /absolute/review/completion.json \
  --journal /var/lib/pixel-migration-journals/migration.json

# Operators who prefer to run the restore as an explicit step may instead do step 4 as a
# separate migration-only restore (writing a private 0600 restore receipt and an armed
# transaction journal), verify the deployment themselves, then commit and finalize. The
# explicit restore MUST use the trusted 4.2 pixel in --migration mode (bound to the same
# contract evidence), because the standard 4.2 allowlist stays strict and rejects a 3.2
# manifest that drifts from the exact contract:
#   PIXEL_MIGRATION_CONTRACT_SHA256=<plan backupRootsSha256> \
#   ./pixel restore /absolute/backup/pixel-private-....tar.gz.age \
#     --identity /absolute/age-identity --signers /absolute/backup-allowed-signers \
#     --replace --confirm --receipt /absolute/review/restore-receipt.json \
#     --migration /var/lib/pixel-migration-journals/migration.json
#   ./pixel restore --migration-commit /var/lib/pixel-migration-journals/migration.json
#   ./pixel migrate-legacy-clean finalize \
#     --install-dir /absolute/new-pixel-install \
#     --plan /absolute/review/plan.json --rehearsal /absolute/review/rehearsal.json \
#     --backup /absolute/backup/pixel-private-....tar.gz.age \
#     --receipt /absolute/review/restore-receipt.json \
#     --output /absolute/review/completion.json
```

The v1 contract accepts only `sourcePixel` 3.2.2 and `targetPixel` 4.2.0. `activate` requires
the authenticated `--backup` and the signed plan/rehearsal, recomputes the backup SHA-256, and
binds the restore receipt's exact fields and self-hash to the plan. It confirms the active
release pointer is exactly `install-dir/releases/4.2.0` and fingerprints the pre-prepared 4.2
configuration, reserves the completion output, restore receipt, and migration journal before any
live mutation, and runs the trusted 4.2 restore in `--migration` mode bound to the plan's exact
3.2 root contract (`backupRootsSha256`). The migration restore keeps rollback armed and restarts the services
(activation's verify may require them running); activation then proves the frozen 4.2
configuration is unchanged, runs
`./pixel verify --expected-install-dir ABSOLUTE` (which fails closed unless the trusted
configuration's `PIXEL_INSTALL_DIR` resolves exactly to that safe non-root path), separately
requires the attestation at the same directory, strictly validates the freshly-written runtime
attestation and the current Git source identity, and writes the completion receipt. Only then
does it commit (delete the old state; services already run and passed the outer verify, so
commit never restarts). On any failure after the swap it
rolls the live state back and writes a truthful content-free non-pass receipt whose
`rolledBack` is set only when the rollback verb verified an exact restoration; a best-effort or
unverified rollback is recorded conservatively as not-rolled-back. It never deletes the
authenticated backup. The separate `finalize` subcommand performs the same terminal evidence
binding for operators who ran the restore explicitly, and never performs restore, bootstrap,
apply, or rollback itself.

**Trusted clean source checkout.** The terminal migration binds every phase (plan, rehearsal,
activate, finalize) to one exact Git source commit/tree. Each phase requires a clean, current,
available Git checkout of the executing release; a dirty, stale, different, or unavailable source
is rejected rather than silently accepted. Because an actual signed release operation cannot
reliably carry `.git`, drive the migration from an explicit trusted clean source checkout
(`git clone` of the exact reviewed release at the recorded commit) rather than from the
packaged install tree, which contains no Git metadata.

### Rollback boundary

Rollback is deliberately **out of scope** for the clean-migration tool and for any in-place
3.2.2 update. There is no `update-rollback` from a 3.2.2 source. If a clean 4.2 deployment
must be abandoned, the legacy environment is preserved only by the authenticated backup made
in step 1; restore it onto a fresh compatible host, never over a live 4.2 install. The
migration contract therefore documents a **single rollback boundary at the backup**: verify
and rehearse the backup before touching the live source, and treat the completion receipt as
the terminal handoff evidence, not a reversible in-place transition.
