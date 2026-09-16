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

Fresh Assistant First also includes the bundled SearXNG search provider so
the assistant can search on first boot. An explicitly selected `parallel-free`
provider omits that container; an older installation's saved search choice is
preserved on rerun. SearXNG is first-boot support, not a library application
installed on demand.

`model-router` remains provisional until a real installed journey proves the
assistant, model switching, and recovery paths can operate without it.

## What is absent

Open WebUI, Perplexica, remote-provider transport, voice, RAG,
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
It creates `data/assistant-first`, its `artifact-stage` child, and
`data/.extension-operation-locks` as real owner-private mode `0700`
directories, repairs mode only for the matching owner, and rejects a stale
`ODS_UID`. The transaction store can therefore initialize its own private child
while immutable artifact staging receives the pre-existing root required by its
descriptor-relative store. Native Windows source update/rollback currently
fails closed as unqualified rather than claiming equivalent descriptor-lock
semantics.

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
the host publishes a completed terminal after proof, or a failed terminal for
a pre-effect failure, before replying. A typed uncertain effect instead leaves
the started receipt intact for current-state observation and recovery. An exact
completed replay normally avoids redispatch. `verify` is different: its
terminal records historical evidence, so an exact replay must rebind the
current plan and rerun the verifier under the active lease. A mismatch or
unavailable current check fails closed without rewriting the completed
receipt. A started-only receipt still requires durable side-effect observation
and cannot be replayed or declared failed by inference. General apply, health
verification, and composite compensation operations and the production
executor therefore remain
disabled; the exact canary operations activated below are the only exceptions.
The future verifier must hash stable current-state identity, health, and
functional evidence, not observation timestamps, so a healthy recheck can
match its historical receipt without accepting a degraded state.

Every receipted `apply:<serviceId>` now requires a started-state observer
before any dispatcher may run. After a possible file/container effect, the
dispatcher can raise `LifecycleWorkUncertainEffect`; the receipt stays started,
and a retry must first classify actual state. An observer that finds partial or
contradictory state refuses redispatch. The image-preparation observer-failure
terminalization option cannot terminalize an apply observation failure. This
contract alone does not publish
application records or select generic apply in production.

Future host work is also bound to the exact approved transaction before a
dispatcher can run. The host loads the owner-private transaction store shared
with Dashboard API, revalidates its canonical plan, approval, journal, and
filesystem custody, and accepts only the operation, service order, definitions,
and artifact digests contained in that plan for the transaction's current
phase. The lifecycle request continues to carry only the closed typed operation
reference; request-supplied definition or Compose material cannot become host
authorization. Completed `verify` replays reload the plan and recheck current
evidence; other completed operations do not. A pre-terminal plan mismatch is
terminalized as failed, while an unavailable loader leaves a started receipt
for explicit recovery. A mismatch after a completed verify receipt fails
closed without changing that historical receipt. The bound material remains in
process and contains no lease credential or secret.

The general production dispatcher and transaction executor remain disabled
until real installed-state observation, idempotent apply effects, and crash
recovery have been implemented and qualified. Exact operations may be activated
individually only with their own durable side-effect observation.

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
pre-read plan rejection. The host composition module is the only production
importer, and the lifecycle handler selects it only for the exact receipted
`stage` operation after lease admission. Staging writes one immutable verified
bundle; it does not configure, apply, inspect, or operate services.

The lifecycle receipt boundary also accepts an optional typed observer for a
`started` receipt. The first observer implementation is intentionally limited
to staged artifact bundles: an exact validated bundle recovers completion with
its digest, while an explicit missing result continues to the ordinary
dispatcher path. Invalid, corrupt, conflicting, or unavailable observations
cannot dispatch or terminalize the receipt. The production host supplies this
observer only for exact `stage` work, so a matching immutable bundle can
terminalize a started receipt without a second write. It grants no recovery
authority for general apply, verification, rollback, or removal. The exact
SearXNG configuration observer described below is a separate closed exception.

Production code composes the exact verifier roots, immutable stage store, stage
dispatcher, and started-receipt observer against that fixed installer-owned
root. Composition validates the root without creating or repairing it, caches
only the exact `DATA_DIR` and extension-root binding, and accepts no request or
environment override. The host lifecycle handler calls the factory only for
the exact `stage` operation and passes both callables through the existing
plan-bound receipt path. Beyond stage, the exact immutable-image canary, the
paired reserve/release boundary described below, the paired data backup/restore
canary, and the exact SearXNG configuration canary, lifecycle operations remain
unavailable. Dashboard execution remains `None`.

The installer also provisions an owner-private
`data/assistant-first/resource-reservations` root for the next host boundary.
A dormant POSIX store records canonical, immutable reservation records bound to
the exact transaction, plan hash, service, action, host ports, and exclusive
resource claims. Cross-process locking prevents conflicting active claims from
both being published; exact active replay is idempotent, terminal records are
retained, and malformed, noncanonical, linked, replaced, or unsafe state fails
closed. This phase deliberately adds no production importer, transaction
adapter, resource projection, dispatcher, or Dashboard executor.

Consumed plan material now binds reservation claims. Plan-bound definitions
parse `resources.hostPorts` and `resources.exclusive` in their planner-canonical
form and carry them on the frozen `PlannedDefinition`; legacy definitions
without claim keys stay readable with explicit `None`. A `reserve:<serviceId>`
command requires present claims on its targeted definition and fails closed
with `lifecycle-work-reservation-claims-missing`; every other operation stays
readable, and valid reserves never depend on unrelated legacy definitions.

A reservation adapter closes the in-process handoff between the
bound plan material and the immutable reservation store. It re-proves the
exact command and LifecyclePlanMaterial binding, targets one non-noop
operation/definition and exact payload, requires claims before effect, converts
plan ports to store form and sorts them by (port, protocol), preserves
exclusive token order, owns a strict UTC-second clock, maps all
ReservationStoreError to the fixed value-free
`lifecycle-work-reservation-failed`, validates the exact returned
ReservationRecord binding/status/claims/hex evidence, returns `record_sha256`. Invoking it performs only the bound
reservation-store mutation: it calls `ResourceReservationStore.reserve`, writes
the owner-private reservation record, and has no process, network, service,
container, or Compose effect. A frozen fixed-root runtime composition joins the
store and adapter against the installer-owned root. The host activation below
preserves those fixed roots and narrow effects. The Dashboard transaction
executor and broader resource projection remain absent from production
composition.

A release adapter provides the atomic batch-release boundary. It
builds typed `ReleaseExpectation` records from plan-bound operations and
definitions, passing them into `batch_release` so that transaction, plan,
service, **action**, and claims bindings are all re-proved under a single
store lock and snapshot.  One atomic snapshot write transitions the exact
batch; a mid-batch failure cannot strand an untracked partial release.  If
the atomic publish may have succeeded before an error, the handler re-observes
the exact post-state and returns success only if every released record exactly
matches; otherwise a single value-free failure is emitted.  A replay where
every targeted record is already released returns the persisted records with
`duplicate=True` and deterministic evidence.  Mixed active/released state is
rejected without another write because it is neither a fresh atomic batch nor
an exact replay. The reserve and release adapters are activated together only
for their exact host lifecycle operations, after authentication, the feature
gate, request validation, lease binding, and mutation admission. They share one
cached host-owned reservation runtime; a missing or invalid runtime fails
closed and cannot fall back to the generic lifecycle dispatcher.

Reconciliation now releases a reservation only after compensation and restore
succeed and a fresh bound observation proves that no mutable service remains
applied. Any failed or ambiguous recovery terminalizes as
`manual_recovery_required` while retaining the ACTIVE reservation as collision
quarantine. Normal verified success still releases exactly once. Dashboard's
production transaction executor remains disabled, so this host boundary does
not yet make conversational extension installation live.

The first network-bearing host operation is now a closed
`download-and-verify` canary for the exact bundled SearXNG Manifest v2
definition. It accepts only that service, schema, built-in origin, definition
digest, image reference, and image digest from the owner-approved plan, invokes
Docker without a shell using only `reference@digest`, then reopens that exact
local reference before returning bound evidence. A paired read-only image
observer can recover a started receipt after response loss without another
pull. Missing, changed, non-canary, build-based, or unpinned material fails
before a subprocess call. This does not stage configuration, start a container,
enable Dashboard execution, or alter Full/Core/Custom behavior.

The paired `backup` and `restore` host operations are now activated only for
the exact bundled SearXNG Manifest v2 canary and its declared
`config/searxng` required-data record. Before configuration, backup captures
either the exact present tree or its exact absence in one canonical,
owner-private, first-write-wins snapshot bound to the transaction, plan,
definition, data schema, and lifecycle contract. Descriptor-relative walks
reject symlinks, special files, hard links, foreign ownership, unsafe modes,
concurrent changes, excessive depth or entry count, and per-file or aggregate
size excess. Publication uses an owner-private sealed temporary inode and a
hard-link no-replace step; recovery stabilizes a published link, removes only
sealed transaction-named crash orphans, and rejects external links.

Restore reads and validates the complete snapshot before touching the live
tree, then changes only `config/searxng`, preserving recorded ownership, modes,
and bytes. An absent snapshot state removes only that exact canary path.
Repeated restore is idempotent, and paired read-only observers recover exact
backup or restore completion after response loss without repeating a completed
effect. Receipts expose only bound evidence hashes, never backed-up bytes. The
runtime has no process, network, container, Docker, Compose, or secret-store
authority, and it cannot make conversational extension installation live by
itself.

For non-SearXNG extension data, the selected generic `backup` route now requires
a fresh active host lease and a pinned local-Docker witness before a new
snapshot starts and again before its temporary archive is published. Any
active scoped service, overlapping running container mount, unverifiable
Docker state, or lease loss refuses publication. A previously sealed exact
archive can still be replayed without another capture. This does not rule out
non-Docker writers or establish a cross-path point-in-time snapshot; generic
`restore`, apply, and conversational execution remain disabled.

The exact `configure` host operation is now activated only for that same
SearXNG Manifest v2 canary. Required `source: generated` string secrets are
created inside the owner-private host secret store during transactional
configuration. Dashboard sends only the generated key name to that boundary;
the durable transaction and every public response retain only sorted presence
metadata and an opaque transaction-bound reference. An exact retry reuses the
existing generated value. The secret value is never returned to Dashboard,
the assistant, a lifecycle request, a receipt, evidence, or generated settings.

Before publishing settings, the configuration effect reloads and re-proves the
current transaction state, approved plan hash, immutable SearXNG definition and
image, configuration schema hash, nonsecret port/default selection, opaque
secret reference, and current host-custody presence. It then writes one fixed
secret-free `config/searxng/settings.yml` through descriptor-relative,
no-follow opens and an owner-created mode-0600 temporary inode, fsyncs the file,
sets the final mode to 0644 for the container mount, atomically renames it,
fsyncs the directory, and verifies the exact bytes. Existing symlinks, special
files, hard links, foreign ownership, unsafe modes, races, or an unsafe path
fail closed. No path or file content can be supplied by the lifecycle request.

Exact replay leaves an already matching inode untouched. The paired started-
receipt observer reloads the same plan, transaction, and secret-presence
bindings and recovers completion only when the current settings bytes match;
this covers response loss after atomic publication without a second write.
Evidence binds the transaction, plan, configuration metadata, and settings
digest without containing the secret. This runtime does not invoke Docker or
Compose, inject environment variables, start a service, or claim health. The
later exact apply/verify/compensate boundary must supply the host-custodied
secret and selected port to the container. Dashboard's production transaction
executor remains disabled in this phase.

A dormant application-identity contract defines the labels a future apply
adapter must place on managed services. It re-proves one exact
`apply:<serviceId>` command against its immutable plan material and binds the
transaction, plan hash, request hash, planned action, version, definition
digest, optional Compose digest, and a deterministic identity digest. Parsing
those labels validates only identity; it does not claim that a service is
installed, running, healthy, configured, applied, or current. This phase has
no production importer, host observer, executor wiring, filesystem access,
Docker/Compose call, or service mutation. A later observer must combine
current Docker-inspect labels with fixed-root active definition/config
evidence and lifecycle receipts; receipts alone are historical evidence and
cannot prove present state.

A dormant application-observation contract now defines that correlation
boundary. It accepts only one plan-bound `apply:<serviceId>` command, a strict
canonical active record, current definition/Compose/configuration byte
digests, the exact expected container set and labels, and receipts re-bound to
the same command. It returns `ABSENT` only when every current mutation source
is absent and `APPLIED` only when all present evidence agrees; partial,
drifted, unavailable, unsupported, or contradictory evidence fails closed.
Container health does not gate `APPLIED`: an exited, restarting, or unhealthy
but exactly identified container remains a mutation that recovery must find
and compensate. Canonical record parsing rejects duplicate keys and
noncanonical or oversized bytes. This contract remains pure and dormant: no
active-record writer, transaction observer, or production executor is enabled
in this phase.

A separate, unselected host-side adapter now collects candidate current
evidence read-only under a caller-held active lease. It loads the exact plan,
active record, and receipt; hashes owner-held `manifest.yaml`, `compose.yaml`,
and nonsecret `configuration.json` in the fixed application root; and probes
both stopped and running Docker containers by managed identity, Compose
service, and expected name. It probes twice and rejects changed evidence,
unavailable Docker, links, malformed records, and unqualified file custody.
It then invokes the pure classifier and never treats container health as proof
of application success. The application file convention is not yet published
by a generic apply effect, the adapter is not selected by the production host,
and this source-only probe does not make extension installation live.

A dormant fixed-root active-application record store now provides the
owner-private persistence layer for the canonical records produced and parsed
by the application-observation contract.  One canonical snapshot file lives in
the pre-created `data/assistant-first/application-state` directory, protected
by a cross-process exclusive root lock.  Records are bounded in number, sorted
by `service_id`, and keyed by exact `record_sha256`. Public reads return the
complete immutable active record, not a digest-only projection. Public
operations support strict read, create with exact replay,
compare-and-replace by prior `record_sha256`, and compare-and-delete by exact
`record_sha256`; deleting an already absent service returns a proven `absent`
observation rather than claiming another mutation. The primary `publish` path
accepts the exact plan-bound `LifecycleWorkCommand`
plus config digest and expected containers and calls existing
application-identity/active-record producers; it never trusts a caller-
fabricated dict. It persists all four non-noop apply actions the planner can
emit: `install`, `enable`, `repair`, and `update`. Divergence, stale compare
values, malformed state, partial state, and unresolved I/O ambiguity all fail
closed with stable value-free codes. Results are frozen typed dataclasses with
explicit created/replayed/replaced/removed/absent outcomes; no paths,
configuration, or supplied bad values leak through.

The store is Linux/POSIX-only with controlled platform-unsupported failure on
non-POSIX hosts.  The snapshot file enforces exact euid/mode 0600/nlink 1/
bounded size, rejects duplicate keys and noncanonical JSON, uses unpredictable
O_EXCL temp names, complete write with fsync, identity recheck, atomic
`os.replace` under the held root lock, root fsync, and exact post-replace
readback.  A crash before replace leaves old state; after replace leaves the
complete new snapshot.  No fsync or response ambiguity is reported as definite
success without exact post-state proof and a successful root-directory fsync;
a replace that completed before an exception is reconciled only when that
durability and exact-state proof succeeds. Mutating inputs are cloned and
revalidated before locking. The store does not wire Docker, Compose, host
probes, the Dashboard transaction executor, installation, or runtime service
mutation.

The generated catalog now records a complete, content-bound Git-index tree
digest for each extensions-library definition, including supporting configs,
hooks, build contexts, and documentation. Manifest v2 library plans include
that digest in the exact owner-approved plan hash, and the typed host plan
loader carries it without accepting a caller-supplied replacement. Manifest
v1 library entries also gain catalog provenance but remain warning-only for
deterministic planning; this does not make those entries transactionally
installable. The existing one-click UI still copies the live library tree
using its legacy receipt and asynchronous start path. A future bridge must
re-prove or stage the exact indexed payload before calling that logic.

For a plan-bound library tree, the host artifact-stage verifier now hashes the
complete live definition before and after opening its manifest and Compose
bytes. A changed supporting config, hook, build context, or document refuses
stage publication rather than passing a manifest-only check. The live Linux
tree check also refuses files or directories outside the host owner's custody
or writable by another account. The immutable
artifact bundle still contains only the manifest and Compose bytes, not the
entire library tree. This is a pre-stage guard, not an apply bridge: a future
effect must consume an exact full-tree snapshot or re-prove the same approved
payload under host exclusion before changing active files or starting an app.
The host stage factory now selects the installer's secured
`data/extensions-library` as its library root, distinct from built-in services.

A dormant pre-effect bridge now closes the payload-custody gap without storing
up to 50 MiB in every immutable stage bundle. Under a future host-admission
window, it accepts only an attested Manifest v2 `applying` command, matches that
command to the complete immutable stage batch, and snapshots every approved
library file plus executable bits through no-follow, owner-custodied
descriptors. Its tree digest must equal the exact approved
`sourceTreeSha256`, and its manifest/Compose bytes must equal the staged bytes.
The returned object contains the complete in-memory payload for a later
materializer; that materializer must consume those bytes and must not reopen
the live library tree. Manifest v1 warning-only entries are refused. The bridge
is not registered and grants no Compose, hook, service, or health authority.

A dormant Linux install materializer now consumes that exact in-memory payload
for an `install` action only. It recomputes the bounded tree digest, takes an
owner-private lock in the fixed user-extensions root, writes through no-follow
descriptors, fsyncs the complete temporary tree, and publishes the service
directory with a kernel no-replace rename. Exact retries are read-only replays;
an existing mismatch is never overwritten, a definite pre-publication failure
removes only the bounded transaction temp, and any post-rename ambiguity remains
an explicit uncertain effect for later observation. Its canonical receipt keeps
the existing one-click compatibility fields while adding only secret-free
transaction, plan, service, tree, and count bindings. The materializer does not
reopen the library, evaluate Compose, invoke hooks, read secrets, start a
container, or claim health. It is still unregistered: host admission, lifecycle
dispatch, Compose project handling, installed-state observation, compensation,
and update/repair/enable actions remain later reviewed boundaries.

A dormant SearXNG Compose-effect substrate now consumes the exact plan-bound
application identity, immutable staged definition/Compose bytes, validated
configuration metadata, and an owner-private secret-use callback. It writes
four fixed, owner-private active files (the exact staged manifest and Compose,
the generated override, and canonical secret-free configuration metadata) and invokes a
fixed, no-shell `docker compose up` argv with the installation root as the
project directory. The active metadata binds the effective port and plan but
contains neither secret values nor references. A fixed owner-private per-service
lock serializes file publication and the Compose effect across concurrent
retries. The generated
override pins all nine identity labels and
records the bound port without publishing a second port; the staged Compose
file resolves the actual port from the temporary process environment. The
secret is provided only inside the host secret-store callback, and command
output is discarded so it cannot enter a receipt or response. File replay
still invokes Compose: matching files alone never establish `APPLIED` or
health. A failed or ambiguous runner leaves partial files for explicit
observation and compensation rather than claiming a clean absence. Existing
drift or unsafe custody of any of the four active targets is refused before
publication. The dormant
effect now raises a typed uncertain-effect error after the first possible file
publication, so a future receipted host dispatcher cannot terminalize that
partial state as a clean failure.

This substrate remains unregistered in the host agent. Before it can be made
live, the integration must re-prove the current transaction and configuration,
match the effective configured host port to an active reservation, observe
the generated override digest alongside definition/configuration/container/
receipt evidence, publish the active
application record last, and recover or compensate every partial effect.
In particular, a user-selected SearXNG port must not bypass a reservation
that still describes the manifest's default port 8888.

The approved Manifest v2 library pilot (Gitea, Miniflux, ntfy, and Ollama)
now has an exact host-side apply route, but the Dashboard production transaction
executor is still disabled. Before Compose can run, apply refuses any typed
host-port value not covered by the approved plan's reservation claims. The
host-side `verify` route rebinds all selected pilot services, including noops,
to the approved plan and current active records. Under an admitted lease it
double-samples owner-custodied active files, the generated Compose override,
the exact expected Docker service/container identities, container and image
IDs, complete loopback-only published ports, and Docker health around a bounded
host-facing HTTP probe. A drifted or unavailable observation cannot produce a
completed verification receipt, and replay checks fresh evidence again. This
is a source-level pilot check, not a claim that the app's real user journey or
the full extension library has been qualified.

Crash recovery moves a transaction from `applying` to `reconciling`. A separate
authenticated host route can rebind the original attested apply request under
the existing transaction-wide lease in `applying`, `verifying`, or
`reconciling`, then classify its current mutation as `ABSENT` or `APPLIED`.
The mutating apply identity and dispatcher still require `applying`. The
Dashboard observation client validates each exact response, and a source-only
observer assembles the complete applied-service prefix, rejecting unknown,
drifted, or non-prefix states. The host route selects no Compose effect or
receipt transition. It is currently limited to the reviewed one-service
extensions-library apply set; it has not qualified arbitrary extensions.
The production Dashboard executor remains disabled, so this is not yet a
working conversational install or recovery journey.

## Evidence boundary

The source contract checks resolver ordering, the exact candidate service set,
capability declarations, Compose rendering, and legacy graph equivalence.
These checks do not claim that an installed assistant journey has passed.
Installed download, readiness, idle-resource, chat, and lifecycle evidence is
required before Assistant First can become the recommended default.
