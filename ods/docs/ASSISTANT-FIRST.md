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
UID/GID so the container and updater address the same owner-private inode. The
installer creates that directory as mode `0700` and rejects a mismatched owner
or stale `ODS_UID`; the containing data root must also be host-owned without
group/world write access so another account cannot replace the guarded inode.
Native Windows source update/rollback currently fails closed as unqualified
rather than claiming equivalent descriptor-lock semantics.

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

## Evidence boundary

The source contract checks resolver ordering, the exact candidate service set,
capability declarations, Compose rendering, and legacy graph equivalence.
These checks do not claim that an installed assistant journey has passed.
Installed download, readiness, idle-resource, chat, and lifecycle evidence is
required before Assistant First can become the recommended default.
