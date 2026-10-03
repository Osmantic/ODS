# Managed provider adapter: experimental, not activated

`plugin/provider-routing.mjs` implements the supported provider/hook interface
of ODS's pinned OpenClaw 2026.6.33. It is deliberately not registered by the
production plugin entrypoint. Saved Settings remain `not-applied` to normal chat.

The adapter freezes one host-supplied loopback lease per run/session binding.
Duplicate selection hooks await the same acquisition. It uses a unique model
reference for each run so model resolution, runtime-auth preparation and stream
wrapping all select the same lease without a mutable global current provider.
The outgoing wire model is `ods/pixel`; context, tool results and permissions
remain owned by the existing agent. No new agent/session is spawned per call.
Only short-lived loopback credentials enter runtime memory; upstream provider
credentials must remain with the host lease worker.

`provider-lease-worker.mjs` now supplies that private pipe transport. Its trusted
command launches `ods-pixel-route-lease`, which verifies the explicitly prepared
runtime's source/tree/interpreter identity and execs the private Python worker.
No credential enters argv or the environment. This launcher is internal transport,
not an owner-approval or activation endpoint. Never populate its command, directory
or request callback from untrusted chat content or incoming headers.

Each worker holds a random loopback listener and one of two OS-lock slots.
Exclusive, fsynced run-claim directories prevent replay independently of those
live slots. Even a capacity refusal consumes its valid run identity; incomplete
claims after a crash remain denied. Claims are not automatically deleted.
The worker has a bounded initial frame, lifetime deadline and parent-EOF/extra-byte
watchdog. It spawns no tools or child commands, so process death closes its sockets
and releases its locks. Parent transport has redundant bounded termination.

The optional private runtime now includes uvicorn in the same version range as
the sharing service. Existing prepared runtimes with different source identity
require the existing explicitly confirmed repair flow; no global install or
automatic service restart is performed.

## Findings from the actual pinned gateway

- HTTP chat run IDs have a `chatcmpl_` prefix; local agent runs may use UUIDs.
- Core catches selection-hook errors and continues. A thrown exception is not
  a routing denial. Managed activation must set `ods-policy/managed` as primary,
  remove native fallback routes, and retain an unavailable default endpoint.
- Setting stream `options.apiKey` alone does not replace prepared runtime auth.
  `prepareRuntimeAuth` must resolve the exact same model-bound lease.
- Non-bundled hooks require explicit conversation-access configuration and tools
  require manifest declarations. These grants are not implied by installing a file.

## Evidence and limits

### Run-scoped handoff candidate

The private lease request callback receives the exact `{runId, sessionId}`. An
owner adapter may supply `handoffProviderId` for that run only. The Python worker
requires that ID to match the configured handoff role, refuses capability
downgrades/cycles and freezes only that recipient's credential. It does not save
a new leader or include ordinary backups. A later request without the field uses
the saved leader. This field is internal transport, not public authorization.

Before inference, `before_agent_run` supplies the actual initial runtime prompt,
system prompt and complete message history. `handoff-approval.mjs` checks paired
tool receipts, builds an immutable preview containing run/session, workspace,
recipient/revision, scope and return action, and binds the approval to its digest.
The trusted `authorizeHandoff` callback must return an exact approval receipt;
missing/foreign/denied/late receipts and changed initial checkpoints block the run.
Cloud receipts additionally require explicit transfer and unknown-cost consent.
No callback is exposed to the model. Pending approval cannot dispatch inference.

The granted data scope includes conversation history and new tool results during
this run. Subsequent tool-loop calls therefore retain their context and permission
boundary without requesting approval for each tool result. This is a runtime
checkpoint, not a byte-for-byte preview of the final provider-specific wire body.
The approval wait is bounded (60 seconds by default, at most 120 seconds) and
counts against the already-running worker lease deadline. A too-short remaining
lease may fail closed; it is never extended implicitly while waiting for approval.

With `ODS_PROVIDER_WORKER_PYTHON`, the real pinned-gateway integration test also
performs leader work, waits for an independent test controller to inspect/approve
the handoff checkpoint, executes new handoff tool work, declines a later handoff
despite approval-looking prompt text, and returns to the saved leader. Both tool
results survive and each effect occurs once. All inference is synthetic loopback.
The initial file controller remains a test-only fixture. An owner API/UI and
durable private approval store are now implemented, but production routing and
handoff initiation remain dormant pending installed activation.

### Owner review and private publication

The Pixel toolbar's **Review handoffs** panel reads pending requests and complete
checkpoints through owner-authenticated `POST /api/pixel/handoff/list`, `/status`
and `/decide` routes. A decision carries only the exact run ID, checkpoint SHA-256
and explicit consent booleans. The host-agent revalidates custody, bytes, recipient,
live worker ownership, expiry and immutable decision state. No public endpoint
publishes a checkpoint or supplies an executable/credential to the runtime.

`handoff-owner-worker.mjs` publishes over a bounded private pipe to the fixed
stdlib-only `handoff_worker.py`. Owner API credentials never enter the gateway,
worker argv/environment or agent tools. POSIX state is private (0700 directories,
0600 files) under `pixel-providers/handoff-approvals`. Held process locks distinguish
pending from abandoned runs; expiry, EOF, extra input and process death cannot
replay an old approval. Terminal approval validates its matching immutable
decision. Retained terminal polling reads metadata rather than all transcripts.
The 4096 retained-claim cap fails closed; collection/recovery is not yet automated.

The panel shows the recipient, revision, endpoint, deadline, scope and full initial
runtime checkpoint as inert text. Cloud approval requires separate transfer and
unknown-cost consent. Browser persistence holds only an opaque run ID; reload
never approves automatically, and uncertain writes require a status reread.
Closing the panel neither approves nor cancels the run. An `approved` status is
permission, not evidence that inference completed or a delivery acknowledgement.

**Registration requirement:** use
`api.on('before_agent_run', bridge.beforeAgentRun, bridge.beforeAgentRunOptions)`.
The pinned runtime's default modifying-hook timeout is 15 seconds. The bridge
supplies a bounded approval-window-plus-five-seconds option, without extending
the independent worker lease or agent deadline. A real delayed owner journey
reproduced the old cancellation; the corrected path passes after 17-second waits.

Set `ODS_HANDOFF_OWNER_API=1` for real dashboard-router/host-agent/private-worker
integration. The optional `ODS_HANDOFF_BROWSER=1` and `ODS_HANDOFF_DASHBOARD` built
dashboard directory let an operator review, reload, approve and decline in the
actual UI. The fixture substitutes a synthetic owner cookie for the production
Edge session boundary; it does not qualify that deployed boundary. Model traffic
remains synthetic. The browser journey retains both tool results, executes each
effect once, denies without inference and returns to the saved leader.

Owner handoff initiation, durable task/conversation/new-task-default preferences
and explicit return state are now implemented; see `PROVIDER-SCOPES.md`.
`PROVIDER-BOOTSTRAP.md` defines owner-custody composition and parent registration.
Still required: protected worker source/runtime installation, reconciliation
with shared admission, and actual installed user journeys.
An agent run is not necessarily an entire user task; this run primitive does not
complete the requested handoff feature. It remains unregistered in production.

Unit tests cover binding, duplicate acquisition, invalid leases, pending-release
races, frozen snapshots, cancellation before IO and closed-route refusal.
The opt-in `runtime_provider_routing.integration.mjs` test starts a private
loopback gateway with a synthetic inference server and a harmless fixture tool.
It verifies native session/tool-history continuity, changed revisions on new
turns, denial without inference, overlapping per-run credentials, release counts
and absence of lease credentials in persisted runtime state. It retains evidence
under a unique temporary directory and stops only its own process group.

Set `ODS_PROVIDER_WORKER_PYTHON` to an absolute test Python with the provider
dependencies to additionally exercise actual per-turn Python workers and stored
provider credentials. On the qualified Linux fixture, setting
`ODS_PREPARE_LEASE_RUNTIME=1` explicitly allows a fresh private dependency download
and runs the same journey through the custody-checked launcher. This fixture uses
`/usr/bin/python3.12`; it is not native-platform qualification elsewhere.

Real process tests cover duplicate workers, incomplete claims, unsafe lock files,
capacity denial, supervisor/worker SIGKILL, EOF, extra input, deadlines and
in-flight upstream disconnect followed by replay denial. The bridge's bounded
cache can prune only successfully released entries when its host adapter provides
durable replay protection. Without that adapter, the 256-entry fail-closed bound
remains. Failed/unknown cleanup is never eligible for eviction.

Run the integration test explicitly on POSIX with the exact installed package:

```sh
OPENCLAW_PACKAGE=/absolute/path/to/openclaw node --test \
  extensions/services/pixel-agent/tests/runtime_provider_routing.integration.mjs
```

This is runtime-protocol evidence, not installed ODS/Pixel acceptance, a real
model qualification, production activation, cloud qualification or Full Access.

## Required before normal-chat activation

1. Wire the qualified host worker into installed lifecycle/source custody.
   `agent_end` is best-effort cleanup, not authoritative lifetime control.
   Preserve the independent watchdog and durable claims across gateway restarts.
2. Activation must bind an approved Settings revision, recipients/data scope,
   concrete execution identity and authoritative idle/admission state. Stage,
   verify and restore configuration transactionally without changing ODS_MODE.
3. Test cancellation, gateway/worker crash, stream interruption, real model
   failover, fresh installation and upgrades through the ordinary ODS chat UI.
   Handoff and Full Access retain their separate acceptance requirements.
