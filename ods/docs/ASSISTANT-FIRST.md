# Assistant First

Assistant First is an opt-in, Linux-first public-beta installation profile. It
is intended for a fresh ODS installation whose first job is a generic,
user-named assistant rather than a preinstalled collection of applications.

Run it from a reviewed checkout:

```bash
./install.sh --assistant-first
```

The profile currently requires a host qualified for the assistant runtime and
its separately accepted license. The first public-beta path uses rootful Docker;
rootless Docker fails closed until its remapped container identity can share the
host updater's owner-private mutation guard. It does not change the default
installer choice. It also refuses to convert an existing Full, Core, or Custom
installation because silently removing optional applications from an active
Compose graph could stop containers the owner still uses.

## Candidate minimum graph

For managed local inference the resolver selects:

- `docker-compose.base.yml`;
- the detected CPU, NVIDIA, AMD, or Intel inference overlay; and
- `extensions/services/pixel-edge/compose.assistant-first.yaml`.

The resulting container graph contains `dashboard`, `dashboard-api`,
`pixel-edge`, `llama-server`, and provisionally `model-router`.
`pixel-edge` is an internal compatibility identifier, not the assistant's
public name. Cloud and external-provider modes replace the managed inference
services with their single selected route.

`model-router` remains provisional until a real installed journey proves the
assistant, model switching, and recovery paths can operate without it.

## What is absent

Open WebUI, SearXNG, Perplexica, remote-provider transport, voice, RAG,
workflows, image generation, observability, privacy tools, and other optional
applications are structurally absent from the resolved first-boot graph. Image
discovery reads that exact graph, so an unselected application's image is not
pre-pulled.

Full, Core, and Custom continue to use the legacy resolver behavior. The
services extracted from `docker-compose.base.yml` remain enabled there through
their manifest-owned Compose fragments.

## Update recovery state

Normal `ods update` image refreshes and source-checkout updates use the same
owner-private, schema-versioned rollback snapshot. Creation fails closed before
environment rewrites, image pulls, or checkout mutation. The snapshot records
exact presence, content hashes, and restore custody for environment and Compose
selection, generic `config/`, the extension desired-state lockfile, transaction
journals and finalization receipts, and `data/user-extensions` definitions and
receipts. Snapshot validation, path allowlisting, symlink rejection, and a
staged payload copy all complete before a manual rollback stops services or
changes files.

Committed definition rollback points under `data/user-extensions/.backups`
are durable state and are included. In-flight `data/user-extensions/.tmp`
staging is explicitly excluded and removed by exact rollback. Ordinary user
data backups also preserve nonsecret remote-provider routing and
pixel-inference owner state while excluding remote-provider secrets, generated
dashboard credentials, and secret-bearing environment backup history.

`data/assistant-first/secrets` is deliberately not copied. It remains in place
under host custody while lockfiles and transaction records carry references
only. Older pre-v2 rollback snapshots remain readable with an explicit warning,
but they cannot provide the v2 checksum guarantee.

The first pre-update gate is also a pure, hash-bound compatibility assessment.
It binds the exact current lockfile, candidate ODS version, and verified
candidate catalog revision. Every enabled locked extension must remain present
in the candidate catalog. Immutable same-version definition drift and catalog
version regressions fail closed. When the locked compatibility range excludes
the candidate core, only a newer candidate definition whose range includes that
core may become a required extension upgrade. Disabled incompatible entries are
reported without silently enabling or upgrading them.

`scripts/assess-extension-update.py` is the read-only filesystem adapter for
that decision. It resolves the installed core version from `.env`, `.version`,
or the installed manifest in that order; requires a canonical owner-private
lockfile; and reads the candidate version and generated catalog from a separate
candidate tree. Its hash-bound result includes the caller-supplied exact Git
object ID plus hashes of the candidate manifest and catalog files. Stable exit
states distinguish a ready update, a required separately approved extension
plan, a compatibility blocker, and invalid input. The adapter creates no lock,
snapshot, receipt, directory, or output file.

This assessment performs no mutation and does not itself authorize an extension
or core update. For Assistant First source checkouts, `ods-update.sh update`
first requires a clean tracked index and worktree, then binds the exact installed
HEAD, symbolic branch or detached state, configured upstream, and canonical
lockfile bytes. Untracked and ignored runtime data do not fail that source
precondition. The updater
fetches the configured `origin/*` upstream branch into a private disposable Git
repository. An unconfigured or detached checkout falls back to `main`, then
`master`; a configured `origin/*` branch never silently crosses channels, and
a checkout configured to another remote fails closed instead of substituting an
`origin` branch. The updater
materializes the assessed manifest and catalog from their exact Git blob bytes,
runs this gate against the exact fetched object, and stops before a rollback
snapshot, installed Git-object import, checkout, migration, image, or service
change unless the result is ready. A ready candidate is imported from the
disposable repository only after the snapshot and an immediate revalidation of
the bound source and desired state. Drift fails before candidate-object import
or runtime mutation. The exact candidate is applied with a verified
fast-forward followed by a second tracked-tree check; there is no archive
substitution or second network fetch that could change the assessed candidate
bytes. If apply fails without moving HEAD, the updater does not cycle services.
Once the snapshot exists, HUP, INT, or TERM is bound to the same fail-closed
source-first recovery path, including the narrow interval after Git moves HEAD
but before the caller records the applied revision. If an interrupt, migration,
or health check fails after mutation begins, rollback validates and restores the
original source revision before restoring the snapshot and restarting the old
graph. Full, Core, and Custom retain their established source-update path.

A result requiring extension changes still stops for a separately generated and
approved composite plan. Assistant First source update and rollback now acquire
the same owner-private global mutation guard used by extension lifecycle routes.
The host runner opens the canonical guard under
`data/.extension-operation-locks`, clears close-on-exec only for that descriptor,
and replaces itself with the updater so the kernel lock remains held through
candidate inspection, snapshot, source mutation, migrations, service restart,
health verification, and recovery. Dashboard mutations acquire this guard
before their sorted service locks. The legacy bulk template-apply route fails
closed in Assistant First until it is backed by the approved composite
transaction executor; template preview remains read-only. Contention fails fast
with an actionable busy result; unsafe custody fails closed without exposing
host paths. Read-only routes and the established Full, Core, and Custom update
paths are unchanged.

The Assistant First Compose fragment runs Dashboard API with the persisted host
UID/GID so the container and updater address the same owner-private inode. Before
writing any data child, the installer rejects a symlinked or wrong-type data
root, requires the installing host UID, and removes group/world write access.
It creates both `data/assistant-first` and
`data/.extension-operation-locks` as real owner-private mode `0700`
directories, repairs mode only for the matching owner, and rejects a stale
`ODS_UID`. The first transaction-store initialization can therefore create its
own private child without weakening the parent contract. Native Windows source
update/rollback currently fails closed as unqualified rather than claiming
equivalent descriptor-lock semantics.

When the opt-in transaction runtime first starts without an active desired-state
lockfile, it writes one canonical owner-private bootstrap record. That record
contains the verified catalog and observed-state revisions, claims no extension
ownership, has no fabricated last transaction or backup, and is never allowed to
replace an existing lockfile. The first real committed transaction hash-chains
from this baseline; later starts preserve the active record unchanged.

Exact composite upgrade execution, combined core/desired-state commit, candidate
health qualification, adoption, and strict offline artifact custody remain later
Phase 5 gates.

The dormant lifecycle-work boundary now uses the existing host receipt store as
its exact response-loss recovery anchor. A started receipt must already exist;
the host publishes the matching completed or failed terminal before replying,
and an exact completed replay never dispatches work again. A started-only
receipt still requires durable side-effect observation and cannot be replayed
or declared failed by inference. Concrete host operations and the production
executor therefore remain disabled.

Future host work is also bound to the exact approved transaction before a
dispatcher can run. The host loads the owner-private transaction store shared
with Dashboard API, revalidates its canonical plan, approval, journal, and
filesystem custody, and accepts only the operation, service order, definitions,
and artifact digests contained in that plan for the transaction's current
phase. The lifecycle request continues to carry only the closed typed operation
reference; request-supplied definition or Compose material cannot become host
authorization. Exact completed replays do not reload the plan or redispatch. A
plan mismatch is terminalized as failed, while an unavailable loader leaves a
started receipt for explicit recovery. The bound material remains in process
and contains no lease credential or secret.

This plan binding is still an inert safety boundary. The production dispatcher
and transaction executor remain disabled until host-side artifact verification,
real installed-state observation, idempotent effects, and crash recovery have
been implemented and qualified.

The catalog's manifest and Compose hashes are now produced through one shared,
host-importable canonical document digest. YAML/JSON spelling and checkout line
endings cannot change semantic identity, while duplicate keys and non-portable
values fail closed. The shipped catalog is regenerated in tests to prove the
refactor did not alter existing provenance. No host artifact path is opened and
no lifecycle dispatcher is enabled by this prerequisite boundary.

New catalog revisions and approved plans bind each definition's origin and
exact relative Compose filename. Library and installed user copies may
legitimately share a service ID, so execution must not choose between them with
an implicit precedence rule. Legacy stored plans remain readable with unknown
origin rather than being relabeled after approval. Artifact opening and
lifecycle execution remain disabled until the next reviewed boundaries.

A standalone Linux verifier can now read the exact plan-bound definition
snapshot without enabling execution. It selects only the named built-in,
library, or user root; performs descriptor-relative, symlink-safe, bounded
reads; enforces file custody; and verifies the manifest and optional Compose
semantic digests. It returns the verified source bytes so a future staging
boundary need not reopen an unchecked path. It is not wired to a dispatcher or
executor, and legacy plans with unknown origin fail before any filesystem open.

Those verified in-memory bytes can now be persisted as one immutable ordered
batch without reopening their source. The staging bundle is bound to the exact
transaction, plan hash, and mutable service order; contains raw and semantic
digests; and is published once under an owner-private Linux directory. Replay
accepts only the exact canonical bundle, while divergent, corrupt, writable,
linked, replaced, or partially published state fails closed. The store does not
create or discover its root, delete staging data, evaluate Compose, call a
service manager, or install a production lifecycle dispatcher in this phase.

A dormant staging adapter now closes the in-process handoff between those two
boundaries. It validates the complete `stage` command before any definition is
opened, verifies every mutable plan definition in its exact approved order,
and only then makes one batch call with the returned in-memory bytes. The
adapter returns the staged bundle hash as terminal lifecycle evidence and adds
no retry or persistence of its own. Verification failure cannot publish a
partial batch, and a post-publication evidence mismatch is distinguished from
pre-read plan rejection. No production module imports the adapter, so this
still does not enable lifecycle execution or installed-state recovery.

The lifecycle receipt boundary also accepts an optional typed observer for a
`started` receipt. The first observer implementation is intentionally limited
to staged artifact bundles: an exact validated bundle recovers completion with
its digest, while an explicit missing result continues to the ordinary
dispatcher path. Invalid, corrupt, conflicting, or unavailable observations
cannot dispatch or terminalize the receipt. The production host path does not
provide this observer yet, so the seam remains dormant and grants no recovery
authority for configuration, apply, verification, rollback, or removal.

## Evidence boundary

The source contract checks resolver ordering, the exact candidate service set,
capability declarations, Compose rendering, and legacy graph equivalence.
These checks do not claim that an installed assistant journey has passed.
Installed download, readiness, idle-resource, chat, and lifecycle evidence is
required before Assistant First can become the recommended default.
