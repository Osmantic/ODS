# ADR: Assistant First Transaction API Boundary

**Status:** Accepted for source integration; disabled by default

## Decision

ODS exposes a separate, fail-closed HTTP boundary for composite extension
transactions:

- `POST /api/extensions/transactions` accepts only a six-field request intent
  and an idempotency key. The server supplies the catalog, observed host state,
  policy, actor, and timestamps, rebuilds the canonical plan, and persists it
  in `awaiting_approval`.
- `POST /api/extensions/transactions/{id}/approval` accepts only the exact plan
  hash and requires an owner-scoped, HMAC-signed browser session. An API key,
  admin/guest/legacy cookie, assistant, or model cannot approve.
- `GET /api/extensions/transactions/{id}` requires the API key and returns the
  reviewable plan plus durable state. Approval output is limited to a boolean,
  timestamp, and hashed owner-session audit identity.
- `POST /api/extensions/transactions/{id}/execute` requires the API key and
  accepts only the exact plan hash. It calls an injected synchronous executor;
  operations, configuration values, secrets, shell or Docker commands,
  approval material, and purge intent are not request fields.
- `POST /api/extensions/transactions/assistant-plan` accepts only one bounded
  `action:extension-id` request plus an idempotency key. It supports the
  current planner's install/enable projection, delegates catalog and host-state
  inspection to production ODS code, persists the same authoritative
  transaction, and returns a narrow proposal without configuration or secret
  names, raw operations, definitions, revisions, actor material, or approval
  authority. Unsupported lifecycle actions fail closed until the planner owns
  those semantics.

Assistant proposal requests use the same lowercase, hyphenated service-ID
grammar as the extension manifest and planner. Broader legacy IDs may still be
inspected through the read-only inventory path, but they cannot cross into a
plan. Host-state projection normalizes both legacy integer ports and Manifest
v2 `{port, protocol}` declarations; invalid installed-service port metadata
fails closed instead of disappearing from conflict detection.

Every response is `Cache-Control: no-store`. Request JSON is size-bounded and
rejects duplicate keys, floating-point values, constants, coercion, and extra
fields. Destructive data purge remains a separate future action and approval.

## Runtime composition

The router performs no import-time filesystem work. It resolves one immutable
`TransactionRuntime` from FastAPI application state only after the environment
feature gate `ODS_ASSISTANT_TRANSACTIONS_ENABLED` is enabled. Tests inject the
same runtime seam. Production startup now installs a proposal/configuration
runtime from the generated catalog, its verified revision, product policy, a
bounded local host observation, the durable transaction store, and host-owned
secret custody. A missing runtime fails with 503.

The production runtime deliberately has no lifecycle executor yet. Capability
discovery reports `execution: false`, and the execute route fails with 503.
Assistant First fresh installs enable the proposal/configuration gate; other
profiles remain disabled unless an operator explicitly enables it.

On first production-runtime startup, an absent desired-state lockfile is
initialized as one canonical empty baseline. `lastCommittedTransaction` is null
only on this zero-extension, zero-history record; a sentinel transaction is not
invented. Initialization is serialized, owner-private, and idempotent, and it
never replaces an existing lockfile. The first committed extension transaction
links its `priorLockfileHash` to that baseline. A missing baseline therefore no
longer makes the first real transaction a special or unchained commit, while the
bootstrap record still adopts or claims no ambient extension state.

Execution provenance is rechecked inside the executor's service locks against
the current catalog revision, policy revision, normalized observed-state
revision, canonical stored plan hash, and selected definition digests.
After the exact stored hash is validated, the executor creates one immutable
binding from the transaction ID and SHA-256 plan hash. Every lifecycle adapter
call receives that binding, and every synchronous completion result and durable
host observation must echo both values exactly. Missing or mismatched binding
evidence fails closed and enters the existing reconciliation path. Execution
receipts return the stored plan hash from the executor result rather than
independently reflecting request input.

Single-extension Dashboard mutations, the composite executor, and Assistant
First source update/rollback now share one file-lock implementation and namespace
under `<ODS_DATA_DIR>/.extension-operation-locks`. Mutations acquire one global
guard before any service locks; update and rollback retain its open descriptor
across process replacement for their complete mutation and recovery window.
Service IDs are validated before path creation, filenames are SHA-256-derived,
symlinked or multiply-linked files and unsafe ownership/modes fail closed, and
composite lock sets are deduplicated and acquired in lexical service-ID order
before caller code can run. A bounded acquisition unwinds every already-held
lock if any later lock times out.
The existing global `.extensions-lock` remains an additional legacy filesystem
mutex; it is not the Assistant First transaction collision boundary.

`ServiceLockFactory` receives the same immutable `(transaction ID, plan hash)`
binding as lifecycle adapters and observations. The local file-lock factory
accepts but does not need those fields; a future host-owned lease factory must
bind its grant, renewal, mutation calls, and evidence to both values. A lock
factory that sees only service IDs is not sufficient for transaction custody.

A dormant, transport-neutral host lease core defines the next boundary without
activating it. It acquires the host agent's existing per-service locks as one
canonical set, stores only a SHA-256 digest of a one-time lease token, and
binds acquire, renew, use, release, and public evidence to the exact
transaction ID and plan hash. Invalid grants unwind their held lock prefix.
Expiry releases an idle lease, while an in-flight mutation pins the lock set
until that mutation exits; expired lease IDs cannot be renewed or replayed.
Only one mutation may be active under a lease at a time, and it may address
only services covered by the exact grant. The global source/extension guard is
orthogonal to that transaction-bound host lease: it prevents cross-process core
and extension mutation, while the lease preserves per-plan host custody.

The host agent now exposes authenticated, transaction-feature-gated POST
operations to acquire, renew, inspect, and release this lease. Requests use a
dedicated bounded strict-JSON parser; reject duplicate keys, floating-point
values, constants, coercion, unknown fields, and query data; and return only
`Cache-Control: no-store` responses. Status is token- and binding-protected.
Service existence and manageability are validated before the host agent's
`defaultdict` is indexed, so an arbitrary name cannot mint a private lock.
The server's existing maintenance cycle releases abandoned idle leases after
expiry, while the lease core continues to pin an active mutation until exit.

This is still a custody boundary, not a lifecycle adapter. Compose toggles,
configuration sync, start/stop, and core recreation can enter `use` under an
exact lease, but the combined install path remains an asynchronous HTTP 202
operation and no complete lifecycle adapter is present. An inert Dashboard
renewer can now keep one in-memory grant alive for a future synchronous
operation. It calls only `renew`, stops after the first failure, bounds shutdown,
and surfaces background failures; it has no runtime call sites and cannot
activate execution. Legacy direct extension requests contend on the exact same
host lock objects. Production execution remains disabled until every transaction
mutation uses the non-reentrant lease protocol with synchronous durable evidence,
renewal supervision, and crash/restart recovery qualification.

## Why disabled by default

ODS does not yet have production lifecycle adapters with durable, synchronous
completion evidence. Existing single-extension endpoints may queue work, and a
submitted or HTTP 202 operation is not completion. Enabling mutation before
those adapters and installed cross-path lock behavior are qualified would create
false-success and collision risks.

Lifecycle execution therefore remains unavailable by default and in the
production runtime. The next phase must add the host-owned synchronous adapter,
wire the lease client and renewer only around complete operations, add
backup/restore and strict offline behavior, and pass real Linux qualification
before advertising execution. Existing Full, Core, Custom, and single-extension
routes remain unchanged.

The Assistant First installer now prepares the transaction store's immediate
`data/assistant-first` parent before Dashboard API can initialize it. The data
root is rejected before child writes when it is a symlink or wrong type, while
the private state and mutation-lock directories require the installing owner and
exact mode `0700`. This closes the fresh-install bootstrap gap but does not
enable the dormant production executor or constitute installed qualification.

Manifest and Compose provenance now share one host-importable canonical document
digest primitive with catalog generation. It rejects duplicate mapping keys,
non-portable YAML values, cycles, non-string keys, invalid Unicode, and non-finite
numbers before emitting sorted compact JSON plus one newline for SHA-256. This
refactor must reproduce the shipped catalog byte-for-byte; it adds no artifact
reader, dispatcher call site, lifecycle effect, or execution authority.

Every newly generated catalog and approved plan also binds the definition
origin (`builtin`, `library`, or future `user`) and the exact relative Compose
filename into its canonical planning material. Host plan binding carries those
values without consulting filesystem precedence, while older stored plans keep
them explicitly unknown instead of receiving an unhashed inference. This is a
provenance prerequisite only; no host artifact is opened and the production
executor remains disabled.

The next boundary provides a standalone Linux host artifact verifier. It opens
only the plan-bound definition root through `dir_fd` and `O_NOFOLLOW`, verifies
`manifest.yaml` and the exact optional Compose filename against the shared
semantic digest primitive, and returns the exact bytes it checked. Legacy plans
with unknown origin fail before filesystem access. The verifier has no
production caller, discovery fallback, dispatcher, or lifecycle authority in
this phase.

Verified definition bytes can next be sealed as one immutable, ordered staging
bundle bound to the transaction, plan hash, and complete mutable service set.
The host staging store accepts only a bound `stage` command, never reopens the
definition source, includes no source path or timestamp in the durable bundle,
and publishes create-if-absent without rename or overwrite. Exact replay is
idempotent; divergent bytes conflict, while corrupt, writable, linked, replaced,
or non-canonical bundles fail closed. The bundle hash is suitable as terminal
stage evidence. This remains a dormant storage boundary: it creates no root,
has no discovery or cleanup API, and does not evaluate Compose or enable the
production dispatcher.

The verifier and immutable store are now joined by a dormant callable adapter
for the exact `stage` command. A pure selector validates the plan material,
state, transaction, hash, and full mutable service order before filesystem
access. Every selected definition must verify successfully before the adapter
passes one ordered tuple to the store; it returns only the validated bundle
SHA-256 expected by the receipted lifecycle-work boundary. Binding,
verification, staging, and post-write evidence failures remain distinct,
value-free protocol outcomes. The adapter is not imported by production code
and adds no dispatcher, installed-state observer, retry loop, or effect.

A separate dormant started-receipt observation seam now permits one narrowly
proven recovery: when an exact immutable stage bundle already exists for the
transaction, plan hash, and ordered service IDs, its validated digest may
terminalize the matching `started` receipt without invoking the dispatcher.
Only an explicit missing-bundle result permits the normal dispatch path.
Corrupt, conflicting, malformed, or unavailable observations fail closed and
leave the receipt started for later inspection. The host agent does not inject
this observer yet, and no other lifecycle operation receives recovery
authority in this phase.
