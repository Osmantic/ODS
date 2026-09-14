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

Every response is `Cache-Control: no-store`. Request JSON is size-bounded and
rejects duplicate keys, floating-point values, constants, coercion, and extra
fields. Destructive data purge remains a separate future action and approval.

## Runtime composition

The router performs no import-time filesystem work. It resolves one immutable
`TransactionRuntime` from FastAPI application state only after the environment
feature gate `ODS_ASSISTANT_TRANSACTIONS_ENABLED` is enabled. Tests inject the
same runtime seam. A missing runtime or executor fails with 503.

Execution provenance is rechecked inside the executor's service locks against
the current catalog revision, policy revision, normalized observed-state
revision, canonical stored plan hash, and selected definition digests.

## Why disabled by default

ODS does not yet have a production composition that supplies both an
authoritative observed-state provider and lifecycle adapters with durable,
synchronous completion evidence. Existing single-extension endpoints may
queue work, and a submitted or HTTP 202 operation is not completion. Enabling
mutation before those adapters and cross-path locks are qualified would create
false-success and collision risks.

The API therefore remains unavailable by default. Later phases may install the
runtime only after they add typed configuration, durable observation,
cross-path locking, backup/restore, strict offline behavior, and real Linux
qualification. Existing Full, Core, Custom, planning, and single-extension
routes remain unchanged.
