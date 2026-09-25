# Owned Pixel measurements

Measurements are off unless the owner prepares a private, expiring authorization
and arms the existing model-router capture scope. No request ID is added to model
text. Native request bodies, tools, output allowances, and permission decisions
are unchanged. Missing diagnostics never cause a task retry or task failure.

The current native route uses `openai-transport-stream-P3cLoEh2.js` and its guarded
OpenAI fetch. The standard bundled provider is a separate fallback. Both adapters
wrap the existing fetch implementation; native transport, redirect, SSRF,
cancellation, proxy and TLS policies remain in force. Four exact OpenClaw
2026.6.33 repair manifests retain the existing backup/restore custody protocol.

## Admission

The owner must prove that ingress and gateway resolve `ODS_PIXEL_PROBE_ROOT` to
the same private directory. If unset, each resolves
`$OPENCLAW_STATE_DIR/ods-measurement`, or `~/.openclaw/ods-measurement` when the state
directory is unset. The directory must already exist, belong to the service owner,
have mode0700 and no symlink ancestry. Files must be regular, single-link,
owner-controlled mode0600 and at most8192 bytes. No directory is created by a
request. The directory must stay outside model-accessible workspace mounts.

Only the existing history-snapshot route qualifies. The supervisor writes
`authorize-SHA256(user + NUL + requestId).json` containing schemaVersion1,
agentId`pixel`, ownerUid, user (the ingress's existing hashed user identity),
requestId, exact existing sessionKey/sessionId, provider/modelId/baseUrl,
scopeId, signingKey, expiresAtMs, maxAttempts and incomingHmac. The root-managed
signing key is a64-character lowercase hex string, used as UTF8 HMAC key bytes.
The authorization expires within1800 seconds and caps at16 attempts.
`incomingHmac` covers the complete incoming parsed body using recursive sorted-key
JSON canonicalization with UTF8. The helper `odsAdmissionCanonical` defines the
encoding; callers must not guess Python/JavaScript serialization equivalence.

Ingress ignores caller probe headers. After normal history admission, it matches
the exact request ID, user and incoming body HMAC. A no-overwrite hard-link claim
followed by unlink elects one consumer. It records a private transfer containing
the exact sanitized gateway JSON body SHA256, and emits one internal signed
admission header. The SDK gateway verifies the transfer, exact parsed/re-serialized
body, agent and session key, then consumes it once. It writes a run-specific lease
containing the generated native run ID and the already authorized session ID.
The provider checks actual run/session/provider/model/loopback route again.
Changed or rotated sessions become unobserved; they are never guessed or adopted.

Raw duplicate HTTP headers, replay, malformed signatures, concurrent identical
request IDs, expired transfers and JSON serialization disagreement do not admit
capture. All reserved probe headers are stripped before model/provider forwarding,
except the freshly signed exact-body correlation header on the managed local
model-router route. That correlation is stripped by the router before backend
delivery. External/cloud relays never forward it.

## Evidence and cleanup

Provider records are allowlisted IDs, invocation counts, body length and clocks;
router records contain keyed component fingerprints, lengths, actual send timing,
numeric backend usage/timings when reported, and first nonempty SSE content/tool/
reasoning-delta timing. Router admission and route readiness are separate from
unknown backend queue time. SSE delta timing is not useful visible progress or
engine time-to-first-token. Component differences are not tokenizer offsets.
Missing counts/timings remain null; native, provider and router retry layers remain
separate. Standard usage cache counts must retain their engine-specific definition.

The supervisor keeps a private exact-path manifest for each authorization,
`.claimed` file, transfer, `.consumed` file, run lease and its bounded events file.
It must collect numeric/hash receipts, prove exact request and physical backend
settlement, disarm/expire the router capture and remove only those owned files.
It records their hashes/modes and removal results without secret values. A crash
between link/unlink leaves a two-link file that readers reject. A crash after
consume but before lease completion is also unobserved. Neither is auto-retried:
the owner reconciles the exact manifest after settlement, including both names of
any interrupted hard-link operation. Never glob-delete another campaign's state
or remove a lease while its native request can still execute. Root receipt files
and all original failed attempts remain preserved.

`scripts/pixel_measurement.py` generates six deterministic paired blocks (three
orders each), pins task/checkpoint byte-and-mode identity and performs exact
correlation joins. It grants no launch authority and never labels a fresh chat
as cache-cold. Timed admission additionally requires actual fleet locks, current
source/model/serving proof, native/backend idle, reviewed oracles, matching task
checkpoints, explicit cache condition and no competing work. Frontend paint and
usable-artifact timing are collected independently; no speedup is claimed here.

The offline `scripts/pixel_probe_cleanup.py` helper accepts explicit filenames
only and binds the manifest to exact settlement/disarm receipt hashes reviewed by
the supervisor. Root approves the manifest digest before invoking cleanup; this
is not another signing protocol. It validates all present file scopes, hashes,
permissions and inode identities before unlinking. Interrupted cleanup can be
repeated: missing names are recorded as `absent-unattributed`, never claimed as
removed by this invocation. Both names of interrupted hard-link elections must
be included. Completion proves only that listed names are absent, not that the
entire directory is empty. Validation followed by unlink is not atomic against a
same-UID external writer; retain the owned lane and private directory custody.

`tests/probe_inactive_overhead.mjs` measures the no-active-lease wrapper with six
counterbalanced CPU-only blocks and a deterministic no-network fetch. It is a
separate microbenchmark, not a model/task speed measurement. Both live timing
arms must retain the same measurement implementation and scope policy.
