# Pixel Edge

`pixel-edge` is ODS's internal OpenAI-compatible adapter between Open WebUI and a host-installed Pixel. It exposes no host port and never receives Pixel's OpenClaw operator token.

The trust chain is deliberately split:

1. Open WebUI authenticates here with the generated, ODS-scoped `PIXEL_OPENWEBUI_KEY`.
2. This container accepts only `GET /v1/models`, `GET /v1/activity`,
   `POST /v1/chat/completions`, `POST /v1/chat/cancel`, and authenticated
   `GET /preview/<site-id>/<path>` requests from Dashboard nginx. Chat fixes
   the model to `pixel/default`, strips browser credentials and every
   `x-openclaw-*` header, and connects to the private ingress Unix socket. The
   preview route connects to a separate read-only host Unix socket and relays
   only immutable content-addressed files with a script-capable opaque CSP
   sandbox. The activity route exposes only an authenticated count so ODS can
   refuse a model switch while Dashboard or Open WebUI Pixel work is active.
   The separate owner-service transition API below provides actual exclusion
   against new edge chat admission; an activity observation alone does not.
3. The host `pixel-agent` integration owns that socket and is the only component that reads and injects Pixel's full gateway credential.

Pixel is a single-owner agent runtime. The default route is therefore intended for the ODS owner surface, not an untrusted multi-user Open WebUI deployment. Pixel is ODS's core conversational experience; Hermes, OpenCode and ordinary Open WebUI models remain available in parallel while Pixel matures. Open WebUI startup does not depend on Pixel health.

Both socket directories are mounted read-only and have no TCP publication.
`PIXEL_INGRESS_GID` grants the non-root container process access without making
either socket world-readable. Preview relay calls use the Dashboard API key as
a distinct server-injected credential; it is never sent to the browser.

Chat requests keep bounded 33-minute total and no-first-byte budgets. This is
one minute longer than the private host ingress and three minutes longer than
OpenClaw's ODS-managed provider timeout, allowing CPU-only first-turn prefill
without making any intermediate proxy the first timeout authority.

Run the focused offline tests from this directory:

```bash
python3 -m unittest discover -s tests -v
```

## Durable transition gate

This is the ingress coordination primitive for a later authenticated ODS
access-mode controller. It does not itself enable Full Access, change Pixel
configuration, inspect the host runtime, or provide a Settings toggle. Status
always reports `host_runtime_verified: false`. A held gate excludes all valid
new `/v1/chat/completions` requests at this edge, including Open WebUI and
Dashboard requests; the existing `/v1/activity` response is unchanged.
Cancellation remains available while admission is blocked.

The gate API uses the existing server-injected `PIXEL_PREVIEW_PROXY_KEY`
(Dashboard API credential), not `PIXEL_OPENWEBUI_KEY`. These credentials must
be distinct when the gate is configured. Never expose this credential or the
gate endpoints through a browser-facing proxy, model tool, or generated site.
Token validation is independent of chat content and model instructions.

By default, no state directory is configured. Authenticated
`GET /v1/transition` then reports `capability: disabled`, with
`reason: durable_state_not_configured`. Ordinary chat still works; acquire,
release, and recover return 503. Configuring an absent, unsafe, unsupported,
already-locked, or corrupt state location instead reports `unavailable` and
blocks new chat with 503. It never recreates missing state or silently resets
an existing gate.

To provision the capability on a qualified POSIX installation:

1. Create a durable ODS-owned state directory, owned by the edge container UID
   (currently 1000), with mode 0700. It must not be in `/tmp`, a tmpfs, or the
   model workspace; do not give the model sandbox this mount. All path
   components must resolve without symbolic links.
2. Before starting the edge with this capability, run
   `python3 transition_gate.py --initialize /absolute/state/directory` as that
   UID, using this module from the qualified source or edge image. Provisioning
   creates mode-0600 state and lock files and refuses to overwrite either.
   A partial provisioning failure requires offline owner inspection, not an
   automatic reset. Reinstall and upgrade must preserve these files.
3. Set the compose variable `PIXEL_TRANSITION_STATE_DIR` to that host directory
   and explicitly include `compose.transition-gate.yaml.disabled` as an
   additional compose override alongside the qualified Pixel fragment. It
   mounts only this directory read-write at `/pixel-transition-state` and sets
   the container variable. `create_host_path: false` prevents Docker silently
   creating an incorrectly owned directory. The ordinary disabled fragment
   and installer remain unchanged until the host/controller integration is
   qualified. This override is not automatically discovered or activated.

The single edge process holds an exclusive POSIX lock for its lifetime. A
second process sharing the directory fails closed. Deployments with multiple
independent edge replicas or alternate ingress paths require coordination at
those ingress paths before a host transition can be considered safe; this
primitive alone does not establish a global host lock.

Owner-service protocol (all POST bodies are JSON with exactly `token` and
`revision`; each is a lowercase 64-character hexadecimal string):

| Request | Meaning |
| --- | --- |
| `GET /v1/transition` | Content-free capability, phase, revision, stream count, and admission status; no binding token or hash. |
| `POST /v1/transition/acquire` | Supply a fresh cryptographically random owner-generated token and the observed revision. Acquisition only succeeds when the actual edge admission counter is empty. |
| `POST /v1/transition/release` | Supply the exact acquisition token and revision after the owner controller has verified its host operation and runtime state. |
| `POST /v1/transition/recover` | Claim an interrupted-stream gate using its current revision and a fresh token. Recovery moves to **held**, so new chat stays blocked while the owner controller inspects/repairs the host. It does not clear the gate. |

The owner controller must durably retain the acquisition token and revision
before sending acquire, then acquire before checking/changing the host. It
must inspect all actual runtime sessions, scheduled/background work, and
other ingress paths while the gate is held. Successful HTTP completion or an
empty edge counter does not prove the upstream agent stopped. Release is
owner-service authority to resume admissions, not runtime verification.

Exact acquire retries succeed while held. Exact release retries succeed only
while the persisted release receipt remains current. Release rotates the
revision; old acquire requests cannot replay against the next transition,
and an old release cannot unlock a newer transition. Wrong tokens, stale
revisions, active streams, and unnecessary recovery produce specific 409
conflicts. Responses contain no submitted commands, file paths, or secrets.
Bindings are hashed at rest and are never returned by status. There is no
automatic expiry: an owner crash cannot silently resume chat mid-transition.
A lost held-gate binding requires offline owner recovery after inspecting the
host; the API deliberately has no force-unlock operation.

Every first admitted turn writes a durable busy marker under the same mutex
that protects gate acquisition and the actual stream counter. Normal final
stream completion clears it. A process crash or shutdown during a stream
preserves an interrupted marker, requiring explicit recovery into held state.
Acquired gates survive edge restarts unchanged. Corrupt, missing, replaced,
or unwritable state produces a sticky unavailable result and rejects new
admission. File fsync, atomic replacement, directory fsync, and a lifetime
lock assume a local filesystem that honors those POSIX guarantees; hostile
same-UID or root modification and unreliable remote filesystems are outside
this primitive's protection.
