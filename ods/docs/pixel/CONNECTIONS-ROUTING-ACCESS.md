# Pixel connections, provider roles and access modes

Status: development in PR #3818, stacked on ODS PR #3385. The Settings UI,
POSIX credential vault, inference-only sharing perimeter and host owner controls
are implemented, including guided host sharing and an isolated POSIX client CLI.
Turn-scoped provider runtime activation is available for the isolated client.
Default Pixel/UI provider activation now has a Linux host/dashboard API and
confirmed Apply/Deactivate/Recover controls in source; the full installed UI
journey remains unqualified. Advisory/handoff escalation, Full Access and complete
cross-platform guided onboarding remain pending. Sharing is disabled by default.

## Independent settings

- Connections share an inference-only endpoint with another device. Inference
  credentials do not authorize agent execution or model/file administration.
- Provider roles select a leader, ordered availability backups, and explicit
  advisory/handoff escalation. The same provider can serve different roles.
- Access mode governs tools on a named execution host. A remote inference model
  does not move tools to its server or gain additional permissions.

The existing `ods/current`, `local` and `default` aliases, global `ODS_MODE`,
owner model/memory choices and existing installations must remain unchanged
until the owner explicitly activates a compatible Pixel-scoped configuration.
Missing configuration preserves existing behavior; corrupt configuration is an
error, not permission to silently reset to defaults.

## Contracts under development

`bin/pixel_provider/` contains shared Python domain contracts for profile/role
validation, private revisioned persistence and fail-closed activity assessment.
These are host-side building blocks, not an alternate agent or work-provider
authority system. Inference route policy grants no tool or operating-system
privilege. Credentials are represented only by opaque server-side references;
public projections expose credential presence, not references or values.

## Independent provider configuration and runtime controls

The owner-authenticated dashboard routes are `GET /api/pixel/providers` and
`POST /api/pixel/providers/save`. Saves accept the exact envelope
`{ "expectedRevision": 0, "document": <complete public configuration>,
"credentialChanges": { <provider id>: { "action": "set", "value": <key> } } }`
and use a
compare-and-swap revision; stale edits return 409. Malformed requests return
400, oversized requests 413, unavailable storage 503. The host-agent paths are
fixed and require its separate owner control credential.

The public document is the same shape returned by GET: providers contain
`hasCredential`, never `credentialRef`. `credentialChanges` is optional; omitted
entries retain keys by the same provider ID under the same revision lock.
`{"action":"remove"}` explicitly detaches a key. Unknown provider IDs and
client-supplied references are rejected. Presence hints never grant or remove
keys. Changing a keyed provider's endpoint or kind requires explicit replacement
or removal; an ID change is a new identity, not an inferred credential transfer.

The Settings form supports profiles, ordered backups, leader/advisor/handoff,
capability/token limits and cloud opt-in. Keys are write-only and cleared at
submission, including on failure. Conflicts and ambiguous write outcomes require
an explicit reload; writes are never automatically retried. System Refresh keeps
unsaved provider edits mounted. No key is put in a URL or browser storage.

Configuration responses do not inspect runtime: `runtime.status` is
`not-inspected`, with reason `runtime-status-separate`. Older configuration
envelopes remain readable; the UI uses the separate strict runtime endpoint.
No save restarts a model or service, edits
`ODS_MODE`, updates the Pixel pin, or grants filesystem/administrative access.

`GET /api/pixel/providers/runtime` inspects the fixed Linux controller; POST
accepts exactly `{operation, revision, providerRevision}`, where operation is
`apply`, `deactivate`, or `recover`. Host-selected paths, binding identity, and
recovery journals are not supplied by the browser. Requests require owner auth,
changes are bounded to 2048 bytes, and model lifecycle conflicts prevent changes.
Runtime status distinguishes not-applied/applied/saved-changes/inactive/pending/
unavailable. Native Windows/macOS adapters remain unavailable, not implicitly
qualified by Linux tests. Registration verification is not inference health.

The UI requires confirmation tied to the current inspection and saved revision.
Cloud-enabled Apply additionally requires a fresh acknowledgement. Save/edit/
reload are locked during a change. A successful POST must be corroborated by a
fresh GET matching the entire activation binding; malformed/lost responses are
never retried automatically. A timeout may mean the controller is still working.
Recovery remains accessible when saved configuration cannot be read and can
restore an older binding without deleting saved preferences or credentials.

Host state lives below `ODS_DATA_DIR/pixel-providers/` in an owner-only 0700
directory, with 0600 files. POSIX uses a permanent flock inode, revision checks,
bounded strict JSON, descriptor-relative operations, and fsync/atomic replace.
Unsafe or corrupt existing state fails closed. A post-rename durability failure
is reported as uncertain; it is not falsely described as a successful rollback.
This protects cooperating writers, not against a malicious same-UID process.
Windows returns unsupported until a native ACL/locking adapter is implemented;
the optional module must not prevent the rest of the host agent from starting.

Keys are stored separately as immutable, owner-only files. New key files and
their directory entries are fsynced before the configuration commit. Failures
before commit remove only files created by that transaction; an uncertain
post-commit failure retains them. Old key files remain for recovery after
replacement/deletion until explicit reference-aware collection is implemented.
Detaching a local key does not revoke it at its provider.

`hasCredential` means a reference is configured, not that its key is valid or
passed a provider handshake. Host-only credential resolution checks the exact
revision, custody, size and key contents. Guided scoped-connection metadata
verification is now implemented in source as described below; actual installed
onboarding/inference acceptance remains pending. Do not provision real cloud keys
into this draft for runtime use yet.

Activity proof distinguishes busy, idle and unknown and binds observations to
a runtime epoch and freshness interval. Every chat, API, cron and background
run counts. Unknown activity blocks access transitions in either direction.
Live transition integration must prevent new admissions while draining and
must verify effective permissions before showing a confirmed mode.

## Delivery gates

- [ ] Inference-only sharing, pairing, revocation and a real laptop-to-tower task.
- [ ] Authenticated provider Settings and durable migration/rollback.
- [ ] Pixel-scoped runtime route with bounded failure/cancellation handling.
- [ ] Backup after partial progress without repeating completed tool effects.
- [ ] Explicit advisory and leader-handoff escalation with approved data scope.
- [ ] Coordinated Full Access configuration/service/guard transition and restore.
- [ ] Explicit administrative privilege and native-platform adapters.
- [ ] Current-source fresh installs, upgrades and independently verified live
      user journeys on the applicable native OS/backend/client combinations.

Full Access permits whatever the selected OS account can do. Restoring sandbox
policy does not undo host changes, transmitted data or persistence. WSL access
is not proof of Windows Administrator or native desktop control.

## Implemented inference sharing perimeter and guided host setup

The optional `pixel-inference` service ships as `compose.yaml.disabled`. It is
not included in normal fresh installs. Its sole host binding is loopback port
4005 (configurable with `PIXEL_INFERENCE_PORT`), regardless of `BIND_ADDRESS`.
Reach it over an explicitly authenticated SSH tunnel or separately configured
TLS ingress; do not expose the model router, Pixel gateway or dashboard owner
credential to clients. The service needs owner-provisioned private state first.

Host-owner endpoints:

- `GET /v1/pixel/inference-sharing`: public grants and verified active identity;
  absent state is disabled, with no migration or automatic directory creation.
- `POST .../issue`: `{expectedRevision, settings}`. Settings contain `label`,
  `catalogId`, `runtimeModelId`, `ttlSeconds`, `maxConcurrent`, `maxOutputTokens`,
  `deadlineSeconds`, `requestsPerMinute`. The identity must match the host's
  current verified local route. The 256-bit device key is returned only here.
- `POST .../enable`: `{expectedRevision, enabled}`. This changes the admission
  switch, not Compose/service activation; enabling requires verified identity.
- `POST .../revoke`: `{expectedRevision, deviceId}`. Revocation and disabling
  remain available when no model is active. Stale revisions return 409.
- `POST .../start` and `POST .../stop`: `{expectedRevision}`. Explicit asynchronous
  service lifecycle (202); start requires a live grant for the verified local
  model. Only the optional sharing service is built/started/stopped. No model
  router restart, provider-mode change, or external port exposure is performed.

All use the existing host-owner authentication, bounded strict JSON and
`Cache-Control: no-store`. Dashboard-owner equivalents are under
`/api/pixel/inference-sharing`. Settings includes device creation/revocation,
confirmed start/stop, actual service state, and one-time connection-bundle
copying. The copied `/v1` URL must be HTTPS or local loopback through an
operator-created SSH tunnel; ODS does not automatically create network access.
Keys are not persisted in browser storage, and ambiguous writes require a fresh
read before another attempt. The source now supports guided client import and
metadata inspection; installed onboarding and inference acceptance remain pending.

### Guided connection import (source-only qualification)

On the client ODS host, Settings > Connections accepts the private connection
bundle copied from the sharing host. Review is local and does not send its key.
The user must explicitly confirm the displayed endpoint before checking metadata.
Loopback refers to the ODS host, not the browser; an already-trusted SSH tunnel or
verified HTTPS transport must exist. This UI does not open ports or create tunnels.

The owner-authenticated `POST /api/pixel/providers/connection-probe` accepts only
`{bundle: <original JSON string>, confirmedEndpoint: <reviewed URL>}`. Its fixed
host-agent counterpart `/v1/pixel/providers/connection-probe` validates the exact
existing connection schema, including duplicate keys, expiry and expected model
identity, before starting a disposable 20-second child. Only one such child is
allowed per host-agent process. The scoped key travels on stdin, not argv, logs,
environment or disk. The existing single-hop transport makes only a metadata
`GET /v1/models`: no redirects, proxy environment, inference or tool execution.
Timeout kills and reaps the exact child. The strict response contains no key.

A successful check offers an explicit Add to provider draft. It creates a new,
disabled ODS-peer provider using the verified endpoint and declared context/output/
tool/vision metadata, with reasoning off until separately configured. A declaration
of `tools:false` stays false. Metadata matching is not proof of successful inference
or tool behavior. Existing providers, credentials, roles and runtime stay unchanged.
The user must separately review roles/capabilities, Save and Apply. Pasted keys are
cleared on submission/failure/discard; a verified key is held only in memory until
added to the existing write-only provider form. Cancel/unmount rejects late results.
No automatic save, apply, retry, cloud opt-in or grant revocation occurs.

Activation validates the resolved Compose security settings and requires an
already healthy, installation-owned model-router. The port defaults to 4005
and remains loopback-only even when customized. An activation-specific nonce
and verified immutable Docker ID bound failed-start cleanup to that operation;
existing or replaced containers are not name-based cleanup targets. Admission
closes on failure while preserving concurrent key revocations. Lifecycle and
enable operations serialize; revocation stays available during a slow build.
The optional Compose template is restored after failure only if unchanged.
POSIX private state is required; native Windows is reported unsupported.

Device keys authorize only `GET /v1/models` and `POST /v1/chat/completions` with
model `ods/shared`. They do not authorize agent execution, model management,
file access or key issuance. Requests cannot supply arbitrary destinations,
authorization headers, external media URLs or backend template overrides.
Function tools and inline image data are permitted; execution stays with the
client. Keys are stored only as SHA-256 hashes in owner-private state, separate
from outgoing provider credentials. Expiry, revocation, admission/rate limits,
an output cap and a total request deadline are enforced. Rate/concurrency
counters are single-process admission controls, not durable billing quotas.
The process admits at most eight concurrent requests and caps each response at
2 MiB. Full responses are not persisted; provider output-token caps remain
backend-enforced rather than inferred from SSE byte counts.

Model/route preconditions are checked again after the router's queue drains.
A model change fails with 409 rather than silently running a queued request on
the replacement model. Legacy callers without pins keep their alias behavior.
Pinned requests also reject a backend-reported model identity mismatch instead
of hiding it through public alias rewriting. A mid-stream mismatch terminates
the stream; already delivered output cannot be withdrawn.
Disconnect and revocation cancel both the façade request and its downstream
router request; response ownership releases sockets and admission even when a
stream never starts or a close fails. Backend cancellation is cooperative:
closing the inference transport cannot undo work already performed remotely.

The container mounts the entire owner-private state directory read-only, so
atomic revocation updates remain visible. It drops capabilities and runs under
the ODS owner's numeric UID/GID. Build-context allowlisting excludes install
data, `.env`, models and unrelated services. This does not isolate the grant
store from a malicious process already running as that same OS owner.

Disposable source integration has completed real JSON and SSE inference on
Tower2's GLM model, model-identity checks and post-call revocation. The routing
state in that test was synthetic. This is not installed Pixel, laptop pairing,
production deployment or native-privilege acceptance.

An isolated real-Docker fixture additionally qualified service build/start,
health, reading private grants through the non-root read-only mount, atomic
revocation, and explicit stop without restarting its router dependency. Its
router was synthetic: this proves lifecycle wiring, not installed Pixel or a
real client inference journey. All fixture containers were removed by verified
run-owned IDs; evidence and failed fixtures were retained.

## Development validation

### Isolated Pixel client (Linux / WSL guest)

`bin/ods-pixel-connect` prepares a **new** private client using the exact Pixel
commit pinned by ODS (`70f44c90`, v4.3.24) and its canonical onboarding/config
renderer. It requires an existing OpenClaw 2026.6.33 installation, Node, Python
3.11+, Git source containing that Pixel commit, and the installed Pixel sandbox
image. It does not install or upgrade these prerequisites, change a production
Pixel, activate a service, or change access mode. Native Windows is explicitly
unsupported by this POSIX adapter; WSL evidence is not native Windows acceptance.

Export the one-time connection bundle from Settings into an owner-private file
(0600 under a 0700 Linux directory, not an NTFS shared directory). Select its
endpoint deliberately: the device credential is sent only after explicit
`--confirm-endpoint` agreement. The probe refuses redirects, proxy environment
variables, unexpected identities and unbounded metadata responses. HTTPS uses
normal certificate/hostname verification; HTTP is restricted to literal loopback.

For a remote peer, keep sharing bound to its loopback port. An already-trusted
SSH alias can forward it without opening the inference listener to the LAN:

```sh
python3 ods/bin/ods-pixel-connect tunnel --ssh-target tower2-lan \
  --remote-port 4005 --listen-port 4005
```

The tunnel is foreground, loopback-only, limited to eight connections, with
strict existing host-key trust and no agent forwarding or remote shell. A
listening tunnel is **not** proof of authentication or model readiness. In WSL,
an explicitly selected `--ssh-bin /mnt/c/Windows/System32/OpenSSH/ssh.exe` can
reuse the owner's Windows SSH alias and trust without copying private keys.
The exported bundle's base URL must match the chosen local tunnel address.

From another terminal, using private files and a previously installed runtime:

```sh
python3 ods/bin/ods-pixel-connect probe \
  --connection-file /private/connection.json \
  --confirm-endpoint http://127.0.0.1:4005/v1
python3 ods/bin/ods-pixel-connect prepare \
  --connection-file /private/connection.json \
  --confirm-endpoint http://127.0.0.1:4005/v1 \
  --directory /private/new-client --pixel-repository /source/Pixel \
  --openclaw-bin /absolute/path/to/openclaw --reasoning off
python3 ods/bin/ods-pixel-connect run \
  --directory /private/new-client --message-file /private/task.txt
```

Reasoning support is an explicit choice, not guessed from a model name or an
agent benchmark flag. Each client gets a unique agent ID and private state,
workspace, canonical `.env`, onboarding answers and rendered config. Digests
detect configuration changes before a turn. The installed runtime version is
rechecked; a new probe catches revocation/identity changes before starting an
agent. Preparation reports `prepared-not-activated`. Run reports process exit
and private evidence, **not** an unsupported claim that the user's task succeeded.
SIGINT/SIGTERM and deadlines reap only the owned agent process group. They do
not undo tools already completed or remove persisted sandbox containers.

The minimal client keeps sandbox mode `all` and optional limbs disabled. Its
pinned runtime can warn about optional searxng/llamacpp plugins absent from fresh
state; these warnings do not qualify those plugins as installed. Full installer,
plugin setup, reconnect/repair UI and native adapters remain delivery work.

An isolated laptop Ubuntu 24.04 WSL guest completed two real Pixel write/read
turns using physical Tower2 GLM inference. Tool receipts and laptop artifacts
were independently checked; Docker inspection confirmed a nonprivileged,
network-none, read-only-root sandbox. Existing Pixel configuration stayed
unchanged. After key revocation, both probe and run were denied, and no new run
directory was created. The temporary routing policy was synthetic, not a
production route migration. The run-owned sandbox and tunnel/server processes
were removed; private evidence and workspace artifacts were retained. This
qualifies this isolated client journey only, not full feature or release readiness.

### Explicit per-turn provider policy

An existing isolated client can use the owner-private provider Settings directory
for one explicitly approved turn. Install the optional runtime dependencies from
`extensions/services/pixel-inference/requirements.txt` in the Python environment
used to launch the helper. The default paired-client path does not need them.

```sh
python3 ods/bin/ods-pixel-connect run --directory /private/new-client \
  --message-file /private/task.txt \
  --provider-directory /path/to/ODS/data/pixel-providers \
  --provider-revision 4 --confirm-provider-policy
```

The directory must be on the execution host and owned by the caller. This is not
permission to copy server credentials onto other devices. Remote clients can
instead use scoped inference connections as configured provider endpoints.
If automatic roles include cloud providers, `--allow-cloud-transfer` additionally
confirms that this turn's conversation and tool history may reach those configured
recipients. No paid cloud call is part of the default behavior or qualification.

The runner freezes the selected revision and credentials, creates a private
per-turn configuration overlay and loopback `ods/pixel` inference route, and
starts the existing pinned Pixel agent. It does not rewrite canonical client
configuration, change global ODS aliases, restart production services, or grant
tools/access. A stable private admission lock prevents overlapping helper turns;
the agent inherits the lock so a supervisor crash does not immediately admit
another helper turn. A turn-unique provider identity avoids runtime-cache reuse.
This lock is not the cross-entry-point production admission controller.

Only the leader and ordered backups participate automatically. Advisor and
handoff roles remain stored but are not executed by this path. Eligible backups
must support the required tools/media/reasoning and at least the leader's
configured context and requested output budget. No history or tool result is
trimmed to make a smaller backup fit. This conservative compatibility rule is
not a tokenizer-based estimate of the request's precise size.

Recovery is per model request, not a replay of the agent turn. Selected transient
statuses (429/500/502/503/504) and transport failures share one attempt/deadline
budget; an unavailable endpoint has a 30-second per-turn cooldown. Authentication,
configuration, TLS verification, redirects and policy refusals do not trigger
automatic provider switching. Exhaustion stops the lease and returns a
non-retryable structured error, preventing SDK retries from multiplying calls.
SSE may retry before response bytes are committed, never after. Partial-stream
failure remains an interruption; no other provider's stream is spliced in.

Private run evidence includes the effective public policy revision and content-free
provider transition events. `completed` in these events means a transport response
completed, not that the task succeeded. Runtime shutdown is separately recorded.
The current adapter speaks OpenAI-compatible chat completions; a configured
LiteLLM-compatible endpoint can provide translation, but its own hidden retries
must be disabled. Direct vendor-specific protocols, durable crash checkpoints,
cost budgets, default-runtime/UI activation and full guided repair remain pending.

A real isolated laptop Pixel completed one write, then a fixture leader returned
503. Physical Tower2 GLM received the preserved tool result and continued with
read/final output, with exactly one write. Repeated with a turn-unique provider
identity. Separate real Pixel partial-SSE and 401 fixtures produced no tool calls
and no backup calls; both returned failure. Production config remained unchanged.

### Manual advisory request (candidate, not leader handoff)

Pixel's **Ask for advice** panel sends only an explicitly written capsule and a
fixed advisory instruction to the saved advisor. It never copies the chat,
files, memory, tools, or credentials for other providers. The selected advisor,
model, endpoint and saved revision are displayed before submission. Cloud advice
requires both explicit capsule-transfer consent and acceptance of unknown cost;
this is not a dollar budget or a zero-cost claim. Paid cloud is not qualified.

One attempt is allowed, with no fallback, a 16 KiB capsule limit, conservative
context checks, a configured output cap and a deadline. Responses containing
tool calls are rejected. Plain-text advice is marked untrusted; the owner can
explicitly paste it into the chat composer without sending. The leader and
execution permissions are unchanged. Automatic escalation and leader handoff
are separate, unfinished integrations.

The authenticated dashboard endpoints are `POST /api/pixel/advice/start`,
`status` and `cancel`, forwarded to the host-agent's corresponding
`/v1/pixel/advice/` endpoints. A request UUID is claimed durably before model
admission. Private POSIX files retain public request metadata and the result,
but not the submitted capsule or plaintext provider key. Two stable OS-lock
slots bound concurrent jobs across cooperating host-agent processes. A retry
with the same UUID cannot replay a model call; interrupted jobs remain
interrupted. Reusing a UUID with a different request is rejected.

Only the opaque job UUID is persisted by the browser component. Reload checks
that job rather than submitting again. Closing the panel does not cancel the
request; **Stop advice** is explicit. Unknown start/stop outcomes require checking
the tracked job. Forgetting tracking does not stop a request or erase host
evidence. Cancelling discards a late answer; it cannot undo upstream work or
charges. Host evidence requires an explicit future retention/deletion workflow.

The host-agent stays stdlib-only (Python 3.9+). Advice now requires a separately
prepared, owner-private Python 3.11+ worker. Preparation is explicit; saving
profiles or starting ODS never installs packages or changes the host interpreter.
From the installed ODS directory, run as the same OS user as the host agent:

```sh
python3 bin/ods-pixel-advice-runtime status --provider-directory /absolute/ods/data/pixel-providers
python3 bin/ods-pixel-advice-runtime prepare --provider-directory /absolute/ods/data/pixel-providers \
  --python /usr/bin/python3.12 --expected-revision 0 --confirm-install
```

Replace the paths with the actual provider directory and an already-installed
Python 3.11+ supporting `venv`. Use the **runtime** revision returned by status,
not the provider Settings revision. The confirmed command downloads compatible
FastAPI/httpx dependencies from PyPI into a new private venv, records resolved
versions/archive hashes, runs an offline import check, and atomically publishes
its revisioned pointer. It never runs global pip, upgrades Python, or restarts
existing services. Repeating preparation at the current ready revision is a
no-op. A missing/unsafe Python or unavailable `venv` needs operator remediation;
the command does not install OS packages or acquire administrative privileges.

The source bundle, installed tree and interpreter are hashed before use. Drift
blocks new calls; explicitly reprepare after an ODS source/interpreter update.
Failed/old runtime directories stay inactive and retained, not automatically
deleted. This is owner-private custody, not protection from a malicious process
with the same OS identity. Resolved dependencies are recorded for each prepare;
different prepares may resolve newer versions within the supported ranges.

One bounded private pipe carries the approved capsule and selected credential;
neither goes in argv, environment, or runtime files. No new listener is opened.
The worker inherits job/concurrency locks; cancellation reaps its process group,
and parent loss closes stdin, cancelling upstream work with a two-second forced
exit fallback. Core dumps are disabled in the worker. No interrupted job replays.

The **Ask for advice** panel now shows runtime readiness on the ODS service
host. If missing or drifted, select a server-discovered Python and explicitly
confirm the private PyPI dependency installation, then choose **Prepare private
runtime** or **Repair private runtime**. Clients send a verified candidate ID,
source hash and runtime revision, never executable paths or shell commands.
Missing Python/venv requires operator remediation; no OS packages are installed.

Preparation is a durable UUID job with a single cross-process installer slot.
Reload resumes observation, not installation. Stop terminates the owned process
group; if the runtime pointer already committed, it truthfully reports completion
instead of claiming rollback. A separate watchdog survives installer-worker exit,
retains admission locks, and kills remaining descendants when its supervisor's
private pipe closes. Old candidates and interrupted job evidence remain retained.
Only an intact, matching published runtime is accepted as setup success.

Default agent provider activation and advisor-to-leader execution remain pending.
Linux checks do not qualify native Windows/macOS, and optional runtime preparation
does not complete whole-product installation acceptance. Existing installs are
not changed by saving profiles.

An isolated actual dashboard/host fixture completed a real Tower2 GLM advisory
request, recovered its result after reload without another call, and pasted it
without sending. A controlled delayed upstream demonstrated cancellation after
fixing a disconnect-consuming test middleware; the failed observation is
retained. A real-TCP regression separately checks upstream socket closure.
These checks are not a production deployment or native-platform qualification.

### Automated checks

Run the focused, service-independent contract tests from `ods/`:

```sh
python3 -m unittest discover -s tests -p 'test_pixel_*.py' -v
```

Sharing tests run separately with pytest and the inference service requirements:

```sh
python3 -m pytest tests/pixel_inference -q
python3 -m pytest extensions/services/pixel-inference/tests -q
python3 -m pytest extensions/services/model-router/tests -q
```

The CI contract job covers Python 3.11 and 3.12, including disconnect before
headers, JSON/SSE bodies, both queue states, response/disconnect races, key
revocation and model changes while queued. No paid provider is contacted.

Dashboard tests use the API's existing runtime and test requirements:

```sh
cd extensions/services/dashboard-api
python3 -m pytest tests/test_pixel_providers.py tests/test_pixel_sharing.py tests/test_pixel.py tests/test_host_agent_client.py tests/test_settings_env.py tests/test_model_routes.py -q
```

The host tests start only a disposable loopback HTTP fixture, use temporary
private state, and do not install Pixel or change a running fleet service.

Frontend validation uses `npm ci`, `npm test` and `npm run build` in
`extensions/services/dashboard`. The local preview was also exercised against
the actual dashboard router and host-agent in an isolated Linux fixture:
add a provider, select a leader, save, reload and retain the selection. This
proves configuration persistence, not inference or Full Access operation.

These tests are necessary but are not installed-runtime or release acceptance.
