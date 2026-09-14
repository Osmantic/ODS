# ADR: Assistant-First installation and on-demand extensions

**Date:** 2026-09-11

**Status:** Accepted for public-beta implementation

**Initial qualification:** Linux hosts already supported by the assistant runtime

## Context

ODS currently recommends a broad installation even when a user initially needs
only a conversational assistant. Optional applications can therefore consume
download time, disk, memory, ports, and upgrade surface before the user asks for
them. ODS already has a bundled extension catalog and protected lifecycle APIs,
but the installer and manifests still encode several services as unconditional
or fixed dependencies.

The desired experience is a small first installation followed by deterministic,
owner-approved extension changes requested through either conversation or the
Extensions UI. The language model may help interpret intent. It must not decide
the dependency graph, approve its own work, receive secrets, or execute shell or
container commands directly.

## Decision

### 1. Add an opt-in profile before changing the default

`assistant-first` will be additive and opt-in for at least one public-beta
cycle. Existing `full`, `core`, and `custom` selections, command-line flags, and
saved `.compose-flags` remain compatibility contracts and escape hatches.
Promotion to the recommended default is a later qualification decision, not a
Phase 1 side effect.

### 2. Separate identity, service ownership, and profile activation

Public assistant name, avatar, and descriptive language are user-configurable
and product-neutral. Existing internal `pixel-*` service IDs may remain until a
separate compatibility migration because they are routing and ownership keys,
not public identity.

`config/core-service-ids.json` remains the reserved built-in namespace used to
prevent extension collisions. It does not mean every listed service runs in
every profile. The resolved Compose graph and manifest ownership determine
which services are active for a particular installation.

### 3. Define the candidate minimum explicitly

The candidate Assistant-First runtime is:

- the host assistant runtime, ingress, lifecycle executor, planner, and
  approval/receipt boundary;
- `dashboard`, `dashboard-api`, and the internally named `pixel-edge`
  compatibility service;
- exactly one provider satisfying a versioned `inference-route` capability;
- the bundled catalog and extension lifecycle machinery.

Basic chat does not require a `web-search` provider. Open WebUI, Perplexica,
SearXNG, remote-provider transport, voice, RAG, workflows, image generation,
observability, and other catalog applications are optional. `model-router` and
any other uncertain service remain in the candidate core until an installed
journey proves the assistant and lifecycle paths work without them.

### 4. Resolve capabilities deterministically

Manifests declare versioned `provides`, `requires`, optional requirements, and
`conflicts`. ODS code resolves those declarations against the catalog,
lockfile, policy, platform capabilities, and observed host-state revision. The
same inputs must produce byte-identical canonical plan bytes and the same
SHA-256. Stable ordering is defined by the solver, not by model output or
filesystem enumeration.

### 5. Keep authority outside the assistant

The assistant may search and inspect the catalog and request a plan through a
narrow typed API. ODS code performs dependency solving, compatibility and
resource checks, conflict detection, canonicalization, and execution. Every
composite mutation initially requires one external owner approval bound to the
exact plan hash, catalog revision, observed-state revision, actor, and expiry.
A changed or stale plan requires a new approval.

The assistant never receives the Docker socket, root access, a shell, secret
values, or approval authority. An awaiting-approval or running operation is
never represented as completed.

### 6. Give desired state and recovery one owner

The extension manager owns a versioned desired-state lockfile. The lifecycle
executor owns an append-only operation journal and idempotency keys. Execution
stages and verifies artifacts before changing the active graph, records
compensating actions where atomicity is impossible, and reconciles every
nonterminal transaction after restart. No second component independently
rewrites the lockfile or silently re-plans an approved operation.

The durable transaction sequence is `planned`, `awaiting_approval`,
`approved`, `reserved`, `downloading`, `staged`, `configuring`, `applying`,
`verifying`, and `committed`. Every failure enters `failed` and then
`reconciling` before reaching either `rolled_back` or
`manual_recovery_required`. Phase effects occur only while their matching
in-progress state is already durable. Restart recovery combines the journal
with strict host observation and never treats a queued request or HTTP 202 as
completion.

The host observation boundary uses small immutable lifecycle receipts rather
than a second transaction state machine. A started receipt binds the exact
transaction, plan, request, ordered service set, and closed operation key; a
terminal receipt adds a completed/failed outcome, evidence hash, and the exact
started-event hash. Receipts are canonical JSON with content hashes, bounded to
4096 bytes, and published create-if-absent through a same-directory hard-link
race. Divergent writers conflict, corrupt or path-rebound files fail closed,
and a terminal without its matching started receipt is never trusted. The
store root is a private `.assistant-lifecycle-receipts` directory created
directly under the host-agent data directory with POSIX mode 0700.

The initial receipt-store phase is deliberately inert: it has no startup hook,
lifecycle or lease call, replay loop, cleanup, or production importer. Phase
5G-B exposes the store only through authenticated host-agent routes
(`/v1/extension/lifecycle-receipt/begin`, `/finish`, `/snapshot`) gated by
`ODS_ASSISTANT_TRANSACTIONS_ENABLED`; the routes publish and observe receipts
and perform no lifecycle mutation. Snapshot requests and responses carry the
exact requested transaction and plan hash. An absent snapshot is a negative
lookup before the first durable binding; once any receipt exists, a different
plan hash fails closed. The Dashboard production executor remains
disabled until a later adapter can bind each durable transaction transition to
lease custody, one synchronous host mutation, terminal receipt publication, and
strict observation without treating HTTP 202 as completion.

Phase 5G-C adds a dormant Dashboard-side client for those fixed receipt routes.
Its shared transport accepts only HTTP 200 with bounded `application/json`,
strict UTF-8, one object root, unique keys, integer-only numbers, and no trailing
data. The client then requires exact response shapes and revalidates every
transaction, plan, operation, request, ordered service, outcome, evidence, and
started-event binding, including the complete snapshot receipt chain. It does
not retry. An uncertain `begin` or `finish` is explicitly ambiguous and must be
reconciled through `snapshot`; read-only snapshot unavailability is retryable
and never reported as a possible mutation. No production module imports the
client, and the production executor remains disabled.

Phase 5G-D adds the dormant `ServiceLockFactory` seam for host lease custody.
The context acquires one lease over the executor's complete canonical service
set, supervises renewal in memory, and exposes the latest redacted grant only
to the exact binding and owner thread. Exit stops the renewer before release;
an ambiguous shutdown poisons the factory so a possibly live renewal worker
cannot overlap another client call. The factory performs no lifecycle or
receipt operation, has no production importer, and leaves the production
executor disabled. A later receipt-bound host adapter must obtain custody from
this exact scope rather than reacquiring a per-operation subset.

Phase 5G-E adds that dormant receipt-bound lifecycle seam without supplying a
production host worker. Each synchronous call first proves exact-binding lease
coverage, hashes its complete canonical operation payload, reconciles an
existing receipt snapshot, publishes a started receipt, accepts only a typed
terminal evidence hash from the injected worker, and publishes the matching
terminal receipt before reporting completion. A completed terminal receipt is
replayable without another worker call. A started receipt found at entry is
never replayed blindly because the prior worker may already have run; it stops
with explicit recovery-required ambiguity until durable host observation can
decide the result. Begin and finish ambiguity use bounded snapshot convergence,
and the worker is never rerun merely because terminal publication was
uncertain. An ambiguous worker result also leaves the started receipt
non-terminal for observation instead of publishing a false failed outcome;
only a proven worker failure publishes a terminal failed receipt. Empty
mutation batches are receiptless no-ops. Production still has no adapter
importer and retains `executor=None`; host mutation and observation adapters
remain required before activation.

Phase 5G-F adds the dormant synchronous Dashboard-side host-work client that
can satisfy the receipt-bound adapter's worker seam. It accepts only a typed,
hash-recomputed request from the closed lifecycle operation set, verifies that
the in-memory transaction lease covers every requested service, and submits
the credential only to the fixed `/v1/extension/lifecycle-work` route. The
transport requires HTTP 200 with bounded, duplicate-free strict JSON and an
exact terminal binding before returning only the evidence hash; HTTP 202,
timeouts, protocol failures, unexpected responses, and server failures are
classified as possible mutation rather than success. Operation timeouts are a
closed client policy, not caller input. The client has no retry, persistence,
logging, host route implementation, or production importer, so execution
remains disabled until the host work and durable observation boundaries exist.

Phase 5G-G adds the matching dormant host boundary and its pure validation
core. The authenticated, feature-gated route accepts one strictly framed,
bounded JSON object, recomputes the canonical request hash, enforces the same
closed operation and ordered-service grammar, requires the request binding to
match an exact active lease, and holds that lease's mutation window around one
injected synchronous dispatcher call. The dispatcher receives no lease token
and success exposes only the exact terminal evidence hash and immutable request
echoes. Queries, duplicate keys, fractional numbers, malformed UTF-8, stale or
mis-scoped leases, concurrent use, dispatcher exceptions, and unverifiable
results fail closed without private details. Production intentionally injects
no dispatcher, so the route returns unavailable and cannot perform lifecycle
work until concrete operations and durable observation are reviewed.

Phase 5G-H binds that dormant host boundary to the existing two-receipt chain
without adding a second host transaction journal. The Dashboard must publish
the exact started receipt before submitting work. The host verifies that
durable transaction, plan, operation, request, and ordered-service binding,
runs at most one injected dispatcher call, and publishes the matching terminal
receipt before returning. A matching completed terminal is replayed without
redispatch; a matching failed terminal is never retried. If transport becomes
ambiguous, the Dashboard reads the same receipt snapshot: a host-published
completed or failed terminal decides the result, while a started-only snapshot
remains explicitly recovery-required and is not overwritten with a guessed
failure. This closes the response-lost-after-terminal-publication window but
does not claim that a crash inside future concrete host work is observable.
Production still injects no dispatcher and retains `executor=None`; concrete
idempotent operations plus durable observation of their side effects remain
required before activation.

All selected services are locked in canonical order before the final
provenance check and before lifecycle work. Artifacts are downloaded and
verified before apply. The first pre-transaction backup is replay-safe,
operations apply in plan order, and compensation follows the durably observed
applied prefix in reverse order. A failed health or representative functional
check cannot transition to `committed`.

Owner approval is a separate authentication scope. Owner, guest, and admin
session scopes are covered by the session-cookie HMAC. Legacy cookies remain
valid for ordinary authenticated access but are never approval-capable. The
API-key session exchange mints only an admin scope. Only an owner magic-link
redemption can produce the hashed, non-secret approval identity bound to an
exact transaction; the assistant-facing lifecycle protocol contains no
approval operation.

### 7. Preserve data by default

Disable and remove are distinct from data purge. Removal preserves extension
data by default. Purging named data is a separate destructive action with its
own exact plan and owner approval. Backups, lockfile revisions, definition
digests, configuration references, and data ownership metadata participate in
update and rollback.

### 8. Qualify capabilities, not distribution names

Initial installed qualification targets supported Ubuntu 24.04, Debian 12, and
already-qualified WSL2/systemd environments. Existing ODS operating-system
paths are not removed. Additional hosts qualify by lifecycle, secret-storage,
service-management, backup, update, and rollback capabilities rather than by a
marketing or distribution-name shortcut.

## Consequences

- Phase 0 adds a read-only measurement receipt and this contract; it does not
  change installer or runtime behavior.
- Phase 1 must make optional services structurally absent from the resolved
  graph so image discovery cannot pull them accidentally.
- Later phases add Manifest v2, canonical composite planning, transactional
  execution, typed configuration, lockfile/adoption/offline behavior, and real
  installed qualification.
- Source tests and CI are necessary but cannot substitute for fresh installed
  assistant, extension, recovery, update, and rollback journeys.

## Rejected alternatives

- **Let the model compose shell or Docker operations.** This bypasses
  deterministic policy, approval, and recovery boundaries.
- **Treat every reserved built-in ID as always-on.** Collision protection and
  profile activation are different concerns.
- **Hide an optional service only in the UI.** If it remains in the resolved
  Compose graph, image download and runtime collision risks remain.
- **Delete data during ordinary removal.** Destructive purge requires a
  separately named and approved operation.
