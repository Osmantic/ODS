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
