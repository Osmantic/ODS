# Architecture

Pixel 3 uses an octopus-style capability architecture. The core agent does not hold
source credentials or receive raw inbox, Calendar-description, or social-post content.
Dedicated limbs reduce each external source to a bounded projection, while separate
actuators handle approved writes.

Post-4.0 development adds a separate Deep Work boundary for long-running coding, data,
and research jobs. It deliberately does not reuse the fixed-action Operations runner or
the typed advisory Frontier path. A dedicated Work Broker compiles an immutable request
into an expiring single-use capability lease; an isolated runner gives OMP broad autonomy
inside a disposable workspace; and a separate verifier gates patches and artifacts. This
development is not enabled in the 4.0 candidate. See `DEEP-WORK.md`.

The optional owner-facing control page sits beside these model and broker paths. It is
served only on `127.0.0.1` by a dependency-free local process and exposes strict,
credential-free status and onboarding projections. Its only mutations are public-setting
saves and exact, expiring, single-use invocations of fixed commands. Three setup/health
actions are always available; a separate owner-only policy may enable public update
metadata checks, encrypted backup creation, and one-way emergency pause. It cannot
activate a deployment, approve work, restore data, or resume broker authority. The same
policy may opt into an on-demand, read-only view of pending sanitized Frontier capsules.

```text
Gmail API -------\
Calendar API -----+--> Pixel Source Broker (dedicated system user)
Social adapter --/          |-- only process with Google OAuth material
                            |-- no Pixel workspace, shell task, or memory access
                            `-- deterministic injection detection and redaction
                                             |
                                  typed, atomic JSON projections
                                             |
OpenClaw gateway (hardened system service, unprivileged client identity)
        |-- loopback + token authentication
        |-- unrelated home state hidden; explicit runtime/state bind paths only
        |-- one configured Pixel agent; deterministic plugin/tool allowlists
        |
        +--> Pixel Source Broker plugin (projection read only)
        |                 |-- no source credential
        |                 |-- no direct Gmail/Calendar/X network call
        |                 `-- Calendar proposals do not execute
        |
        `--> agent-scoped Docker sandbox (network=none, read-only root)
                    |-- Pixel workspace read-write
                    `-- CLI/file/process tools cannot see broker credentials

Operator confirmation --> one-shot Calendar actuator --> Google Calendar API

OpenClaw gateway --> Operations request plugin --> typed spool --> Operations Broker
                                                              |-- private policy/key
                                                              |-- pinned targets
                                                              |-- immutable plans
                                                              |-- grants/leases/budgets
                                                              |-- pause/audit/receipts
                                                              `-- bounded evidence
                                                                       |
                                                      local host / dedicated runners

private local model --> Frontier request plugin --> typed spool --> Frontier Broker
                                                              |-- private policy/key
                                                              |-- receipt-based router
                                                              |-- privacy compiler
                                                              |-- exact-hash approval
                                                              |-- budgets/cache/dedup
                                                              `-- tool-disabled Codex
                                                                       |
                                                       structured advisory result
                                                                       |
                                                    private local finalization
                                                                       |
                                                    content-free quality receipt
```

## Local control boundary

`control/server.py` runs as the deployment owner from a trusted Pixel source or release
tree. The browser receives a random process-lifetime HttpOnly session cookie and fixed
local assets. Host, origin, fetch-site, content type, message framing, duplicate JSON,
size, rate, queue, retention, runtime, and output checks fail closed. No CORS policy is
published and the content policy permits only same-origin static resources.

The process is in the local trusted computing base: it reads canonical private onboarding
to preserve advanced fields, but returns only the strict public schema. Confirmed
configuration runs from a private immutable snapshot. Optional operator actions bind an
opaque private-policy revision and remain disabled by default. Every preview also binds
the exact generated deployment revision. Action output remains in bounded owner-only logs
and the browser receives only a content-free result. Activation,
credential entry, broker approval, backup decryption/restore, broker resume, and a
generic file/process/command interface are absent. See `CONTROL-SURFACE.md`.
The status projection may read the Frontier Broker's separate content-free usage file,
but revalidates every provider enum, billing relationship, circuit flag, and budget
counter rather than passing the broker JSON through. Before broker activation it may
project only validated limits from the generated policy, with usage left unknown. A
malformed active usage file fails closed instead of falling back to prepared status.
Prompts, plan/job IDs, model names, accounts, credentials, paths, and arbitrary telemetry
or policy fields are never projected.

The separate Frontier-review endpoint is content-bearing by design and disabled by
default. When enabled, it reads only fixed-name result projections already shared with
the gateway owner, accepts only `awaiting-approval` records, revalidates the exact capsule
shape and canonical capsule hash, and rebuilds an allowlisted response. It reads no
broker-private plan, policy, approval, request archive, replacement map, cache, authority,
or credential file. At most 1,000 result names are considered, 20 reviews and 1 MiB are
returned, and files are single-link regular no-follow reads. The page cannot submit an
approval; the terminal boundary and broker's approval-time checks remain authoritative.
Unlike content-free endpoints, this endpoint additionally requires a random
process-lifetime bearer passed to JavaScript only in the terminal-printed URL fragment.
The fragment is not sent in the HTTP request and is removed from browser history state
before the page makes requests; the token is then supplied only in the review header.
A loopback session cookie by itself therefore cannot cross this content-bearing boundary.

## Source Broker boundary

`pixel-source-broker.service` runs as the dedicated `pixel-source-broker` system user.
Its OAuth file is mode `0600` beneath `/var/lib/pixel-source-broker/private`; the
OpenClaw gateway owner cannot read it. The service has no access to the owner's home
directory or Pixel workspace. It can write only the projection directory.

Raw inbound source bodies are held in memory only for the duration of one refresh. The
broker exhaustively paginates the configured Inbox and Sent queries up to a high explicit
safety ceiling and publishes page-count/completeness evidence. Sent message bodies are
never requested from Gmail, including messages carrying both Inbox and Sent labels; the
broker requests only bounded routing, recipient, subject, timestamp, thread, and label metadata. The broker
detects agent-targeting, instruction overrides, command execution, file access,
concealment, and persistence language. Suspicious fragments are removed before a
summary is written. Each record carries risk flags, a source hash, provenance, and an
explicit statement that it cannot authorize an action. Raw bodies, HTML, attachment
content, and event descriptions are never written to the projection.

Projection files are owned by the broker and group-readable by the gateway owner. The
directory is not writable by Pixel or the gateway. The projection plugin validates the
schema and boundary declaration before returning records to the model.

This is a one-way capability firewall, not a literal physical air gap. A sanitizer can
still make mistakes, so source records remain untrusted and the prompt-injection suite
is a release gate.

## Sensory limbs

- Email: read-only Gmail API ingestion; exhaustive paginated Inbox/Sent indexing by
  default, bounded tool pages, sanitized Inbox summaries, metadata-only Sent, and explicit
  query/page/completeness/truncation coverage.
- Calendar: read-only event ingestion; titles, locations, and descriptions are filtered
  as hostile input.
- Social/X: provider-neutral JSON adapter input; sanitized feed/search projections.
  The base deployment has no X credential and cannot post.
- Web: the existing policy-enforced Web Courier remains a separate untrusted-content
  limb for public pages.
- Operations: the gateway requests named fleet actions, workflows, downloads,
  transfers, cancellation, and break-glass plans. A separate broker owns policy and SSH
  authority. Policy v2 separates installed capabilities from scoped standing grants
  and externally issued temporary leases. Root-owned managed helpers verify and roll
  back bounded service, deployment, and package transactions. See
  `OPERATIONS-LIMB.md` and `OPERATIONS-AUTONOMY.md`.
- Frontier: Pixel completes useful reasoning locally and emits a content-free attempt
  receipt before it may submit a typed plan-review or failure-triage capsule. A separate
  broker deterministically keeps sufficient/retryable/context-blocked work local; rejects
  never-egress content; placeholderizes supported identifiers; binds the receipt,
  sanitized capsule, policy, provider, model, budget, and authority to an immutable plan;
  and starts one ephemeral, tool-disabled Codex process only when permitted. An exact
  private cache suppresses duplicate eligible calls. Structured advice is validated,
  critiqued and composed locally, then represented to the broker only by content-free
  finalization and quality evidence. Deployment-owned live qualification is a separate
  terminal path: a short-lived consent file prepares one fixed synthetic proposal, a
  second exact hash authorizes at most one provider turn, and a broker-private claim
  produces content-free recoverable evidence without exposing the response. See
  `FRONTIER-LIMB.md` and `FRONTIER-LIVE-QUALIFICATION.md`.

Additional sources should implement the same projection contract instead of adding
credentials or authenticated browsing to Pixel.

## Actuator boundary

Every Calendar write first becomes an exact proposal. The broker, not the model, then
classifies it. When the deployment enables bounded direct execution, only two reversible
shapes can proceed immediately: a private create with no attendees, or a time-only update
of one event using its current ETag. Deletes, attendee/invitation changes, content edits,
and recurring-series changes remain `pending-operator-approval`. Direct actions are
serialized and capped per hour, and time changes record bounded rollback data. The owner or operator
can inspect and approve one such consequential proposal outside Pixel:

```bash
./pixel source-show calendar-...
./pixel source-approve calendar-... PROPOSAL_SHA256 --confirm
```

Both paths start a single hardened actuator service under the broker identity. Direct
execution is awakened by a system-owned path watcher, so the gateway neither elevates
nor starts services, and independently revalidates the exact proposal against its fixed field policy;
the consequential path reads a protected snapshot rather than the gateway-writable
proposal and validates its hash. Updates use `If-Match`, attendee notifications are
disabled, one bounded result is journaled, and the service exits. Email sending and
social posting require their own actuators and are not implicitly authorized. SSH and
public downloads are available only through the Operations Broker; Pixel's sandbox and
plugin still have neither raw network nor execution credentials.

## Other boundaries

The Docker sandbox remains networkless with a read-only root, all Linux capabilities
dropped, a non-root identity, bounded processes/memory, and one writable Pixel
workspace. Its config is rebuilt from fixed safe fields, so an old config cannot retain
host binds, a setup command, environment secrets, or namespace-join flags. CLI tools
cannot reach the source token or projection host paths. Public web
rendering remains limited by the Courier's SSRF, method, download, size, DNS-rebinding,
and browser-isolation policies documented in `SECURITY.md`.

The Frontier Broker is also outside the gateway. Its API key or auth cache, policy,
request archive, sanitized plans, approvals, replacement maps, provider cache,
integration archive, provider runtime, and authority ledgers are unreadable by the
gateway identity. The gateway can publish a content-free finalization receipt but cannot
read the archive. This is a software-mediated egress boundary,
not a physical air gap: an allowed capsule crosses the provider network after policy
and, where required, operator approval.

Installed Pixel releases remain immutable directories selected through an atomic
`current` symlink. Broker identities, private tokens, policies, SSH keys, host pins, and
spool state are host state, not release contents. Major-version rollback across the 2.0 credential migration
requires an operator-reviewed credential export; the token is never copied back into
the gateway account automatically.

Each limb is independently selected at configure time. Disabled tools are omitted from
the active plugin surface and explicitly denied for Pixel; disabled services are not a
deployment prerequisite. Capability profiles are defaults, not extra authority.
Channel and custom plugins are a separate explicit `gatewayExtensions` allowlist. Pixel
does not inherit unrelated agents or cross-session history/send tools from the host.

Customization policy packs are signed data inside the same immutable limb tree, not new
gateway code. Local capability packs describe observe-only signed tools. Operations
action packs declare namespaced fixed-helper actions but acquire no target until private
onboarding maps their placeholder to an existing Operations policy target. Frontier task
packs are composed as restrictions over one of the broker's typed tasks; they cannot add
a task or widen classification, token, rehydration, or enablement policy. Configure checks
the no-follow file hash, containing manifest declaration, pack version/tree receipt, and
active extension path before generating broker policy.

Deep Work long-horizon goals sit above, and do not replace, the existing job boundary. An
immutable goal names a canonical acyclic graph of exact child-job hashes and aggregate
budgets. An owner-private hash chain records deterministic dependency dispatch, one active
child, terminal child evidence, completed milestones, and monotonic actual usage. A
recorded dispatch is recovered, never treated as permission to replay. Child plans,
one-use leases, disposable runners, and independent checkpoints remain the only execution
and verification path. The goal can therefore survive controller restarts and sequence
days of bounded work without becoming a credential, generic approval, scope-expansion, or
self-declared completion token.

The goal-to-Builder driver is a narrowing bridge, not a second runner. It revalidates the
complete goal/child/plan/lease/workspace context and maps the authoritative child state to
one existing Builder operation: execute a current iteration, resume the independent
verifier, or clean and close an interrupted worker. The underlying single-use claim and
atomic checkpoint decide the launch winner. Only a state code and checkpoint digest return
to the goal runtime; patch and verifier content remain in the existing private result and
checkpoint paths. A continuation retains the original prepared-input digest while its
lease binds the prior independently verified candidate checkpoint; those are distinct
lineage facts and are never collapsed into one mutable workspace claim.

The goal run-bundle ledger is the private capability-custody boundary that makes this
bridge process-restartable. Each append binds the immutable goal and job, exact compiled
plan, exact expiring one-use lease, original input snapshot, and previous bundle hash.
Because the record contains a live lease, it is owner-only and is never described as
non-authoritative; instead, it is constrained to add nothing beyond the embedded lease.
Pre-admission refresh requires the superseded lease to be expired and unclaimed. The
production resolver re-hashes the original content objects and recompiles the old reviewed
job only through this custody path; after identifier and time fields are removed, every
plan and lease authority fact must equal the prior record. This permits multi-day pursuit
without turning request age into policy, model, input, tool, budget, or scope drift.
Continuation requires exact prior consumption, independently verified checkpoint, and
cumulative-usage evidence. Goal scheduling still grants nothing, and a recovered bundle
still cannot replay a claim, widen inputs or budgets, authorize external effects, or
declare completion.

Admission is a permanent append in the same custody chain. It and a concurrent refresh
target the same next immutable sequence, so the filesystem append selects one winner.
After admission, refresh is forbidden and child-ledger recovery requires the marker. This
closes the check/initialize race without a crash-stale mutex. An admitted lease that
expires before child-ledger creation therefore requires explicit operator recovery rather
than automatic authority replacement.

Goal lifecycle control mutates only the parent scheduling state. Pause/resume transitions
must preserve every active-child, observation, progress, usage, failure, and safety fact;
the controller rereads that state just before another drive. Resume is exact-goal-hash
confirmed and relies only on the still-current child lease. Ready-state cancellation is
terminal and exact-confirmed. Cancellation with an active child is rejected until the
supervisor can stop and clean that child, so parent state never conceals an orphan worker.

Private context sessions are a separate non-authoritative custody layer. Creation binds an
exact plan, checkpoint, context, classification, state root, and lifetime into an immutable
owner-private capsule. Listing and inspection expose only bounded metadata; displaying raw
context is an explicit trusted-terminal operation. A fork creates a new job lineage under
the same private state root, cannot lower classification, and carries no lease, credential,
approval, external-effect, or completion authority. Removal transfers the exact inode into
verified hard-link custody before unlinking the live capsule, then records a content-free
tombstone. Interrupted removal remains visible as recovery attention and can resume without
guessing; unexpected links, substitution, cross-root lineage, and stale confirmations fail
closed. This lifecycle makes compacted long-goal context durable without turning narrative
memory into authorization.

Builder and candidate resolution first perform that exact just-in-time pre-admission
refresh when a delayed untouched milestone has expired. Builder continuation resolution
has no process-local lease pointer. The resolver starts
from the custody head, recovers the prior single-use consumption tombstone and verified
checkpoint, validates the controller-issued reduced lease, and atomically appends that
exact lease to custody. Future-issued custody is inert until the runtime's separate
just-in-time clock check passes.

Builder preparation is disk-derived as well. The goal adapter requires its complete
runtime context to equal private custody, then reconstructs continuation patch paths and
verification/cleanup recovery claims from the exact retained claim. These paths and
recovery objects are never accepted as controller or worker input.

The `work-cycle` surface is a single-reconciliation process, not a resident agent loop.
It reads one strict owner-private configuration, revalidates goal, jobs, policy, custody,
network separation, and pinned paths, permits one bounded controller pass, returns a
content-free receipt, and exits. Repetition belongs to an external service supervisor.

`work-service render` turns that boundary into a private, atomic systemd bundle. The
oneshot service uses a per-goal nonblocking lock, sanitized environment, read-only inputs,
disjoint writable state/workspace roots, a private network namespace, Unix-only address
family, empty capability sets, and an inactivity timer so long iterations cannot overlap.
The manifest binds goal, configuration, service, and timer hashes. Rendering cannot
install or enable the units; Docker-socket possession remains explicitly high trust.
`work-service inspect` verifies the private single-link bundle without side effects.
Installation requires Linux root plus the exact manifest hash, requires the bundle owner
to be the configured unprivileged service identity, copies into the root-controlled unit
directory, revalidates the current private configuration and goal against the rendered
hashes, verifies those destination bytes with systemd, and leaves the timer inactive.
Activation repeats the live binding check and is a second exact-hash-confirmed operation. Removal disables and verifies the
timer, stops the oneshot so cleanup can run, removes only byte-identical units, and retains
private goal state. Failed install and removal publication restore a complete inactive unit
set rather than accepting a partial lifecycle state.
Every service exit also runs a locked cleanup-only reconciliation. It does nothing unless
the exact admitted Builder, Scout, Researcher, or Data Lab checkpoint is `running` or
`cleanup-failed`; in that state it recovers the consumed claim, removes only claim-bound
Docker resources, requires positive removal evidence, and
durably closes the child as failed. Failed removal remains explicitly `cleanup-failed` and
may retry only that exact cleanup; ambiguous resource identity becomes terminal
`recovery-inconclusive`. It cannot launch a worker or replay completed cleanup.
Stopping is distinct from cancelling. `work-cancel` requires the immutable goal hash and
an exact terminal failed supported checkpoint; it then incorporates the child usage and
cleanup evidence into one final cancelled parent record. It cannot cancel a live child,
and a competing controller observes the cancelled head before any new drive.

The lower runner boundary now also supports cleanup-only rehydration for Scout,
Researcher, and Data Lab attempts. It starts from the stored single-use claim and exact
`running` or `cleanup-failed` checkpoint, verifies the original profile lease at issuance
time, derives only claim-bound Docker names, and can remove exact containers, networks,
and disposable volumes without materializing inputs or retaining an executor path. Cleanup preparations
cannot construct worker or volume-creation commands. Scout, Researcher, and Data Lab have a disk-derived adapter
that resolves exact admitted custody, drives one initial lease into a non-completing
semantic-acceptance checkpoint, or cleans one exact interrupted attempt. Its receipt is
content-free and repeated waits cannot relaunch the child. The supervised production
router now selects Builder, Scout, Researcher, or Data Lab only from the immutable child profile;
Researcher configuration requires an exact private courier queue and loopback broker,
while other goals reject unused research authority. Scout output is a strict proposal:
the controller reopens each normalized local evidence path and verifies the quoted bytes
before retaining a report, but explicitly records that this proves neither truth nor
semantic entailment. Full
maximum event-horizon crash/recovery and supported-host qualification are still release gates;
the calendar-based multi-day soak is optional appliance evidence.

After each successful supervised cycle, the controller rebuilds a bounded operator
heartbeat only from the immutable goal graph, admitted run custody, and validated
checkpoint chains. It publishes mode, state, progress/usage counters, artifact categories,
verification state, and fixed activity codes through keyed opaque identities; no objective,
prompt, path, filename, hash, tool argument, provider content, or work authority enters the
snapshot. The control surface independently validates and strips even those opaque
identities. If cycles stop succeeding, the unchanged snapshot becomes visibly offline.

A Scout/Researcher/Data Lab candidate becomes a completed child only through a separate local
semantic-attestation boundary. The attestation binds the exact candidate checkpoint,
criteria digest, artifact manifest, deterministic verifier digest, plan, claim, and one
all-pass criterion index set. It is published atomically in owner-private state before the
checkpoint moves from `waiting-authority` to `verified` and `completed`. The attestation
grants completion evidence and nothing else; it cannot execute, replay, widen, publish, or
cause an external effect. Concurrent attempts converge on the same record and chain.
`work-accept review` exposes only the hashes an operator must compare, and
`work-accept accept` requires that exact review hash before recording the attestation.
Both operations re-hash the complete retained candidate set; Data Lab additionally walks
the private derived tree and checks every byte and filename against the replayed manifest.

Deep Work capability packs are a separate, disabled execution substrate rather than an
extension of gateway authority. A pack binds one image, executable, MCP server identity,
tool schemas, accepted classification set, and resource boundary. A broker-issued grant
selects a subset for one job checkpoint; atomic claim and execution-start tombstones burn
it exactly once. The adapter has no network, host filesystem, credential, Docker socket,
SSH agent, browser, external-effect, scope-expansion, or completion authority. Only bounded,
schema-validated structured output returns to the controller. Signed declaration admission
is a separate canonical Ed25519 operation: inspect is explicitly untrusted, verify binds a
publisher identity and current trust root, and atomic private installation records that no
image was pulled, inspected, or executed. Installed declarations remain absent from the
controller catalog. Removal is bound to the exact inspected declaration and installation
receipt, transfers it to recoverable private custody, persists separate custody proof before
deletion, records a content-free no-residue tombstone, and prevents same-version replay.

Image admission is a second disabled phase. It accepts only an already-local canonical
repository manifest digest through an empty credential-free Docker client configuration.
The signed pack separately binds Docker's local image ID, Linux OS/architecture, labels,
the one exact safe builder PATH, absence of volumes/build hooks/health checks, and the
declared executable. Pixel validates all of them without starting the container: a hardened networkless
never-started container supplies one bounded tar stream, and the host verifies the exact
regular-file size and SHA-256 before proving forced container removal. A private
single-writer record serializes image inspection, admission revocation, and pack removal;
cleanup and revocation survive intent-only, post-move, post-custody, and post-finalization
crashes. Revocation retains the unowned host image. The admission receipt remains disabled
and begins `not-probed`.

An explicit confirmed health operation can start that exact admitted image in a separate
sterile disposable container. It performs only protocol discovery and signed-schema tool
listing: no grant is created, no tool is called, no client data is supplied, and no result
can enter controller state. Operation custody is durable before launch. Cleanup is mandatory
and independently proves container absence; chained content-free receipts survive crashes
before cleanup, after cleanup, during staging, or after final publication. Three consecutive
failures quarantine the signed version, and recovery can resume only the exact interrupted
operation. A permanent supported-host gate exercises a real networkless scratch image through
sign, install, admission, health, status, revocation, declaration removal, and exact cleanup.
The disabled supervised runtime then requires recent passing health, a separately issued
expiring single-use grant, and a checkpoint-bound continue decision. It burns the grant and
an execution tombstone before adapter entry, makes exactly one schema-bound call, returns
structured output only across the live caller boundary, and persists only a content-free
terminal receipt. Its cleanup custody binds the exact container, optional tmpfs workspace,
image, job, and claim; Pixel validates ownership before removal and proves absence afterward.
Recovery may finish cleanup or receipt publication but cannot recover output or replay work.
Health freshness and execution timeouts are validity/safety ceilings, not progress cadence.
The pure controller authorization compiler now sits before that runtime. It accepts no
ambient pack registration: the exact signed pack must appear in a private controller policy,
the normal OMP lease must already have a matching consumption tombstone, and the current
checkpoint must be the exact `running` checkpoint bound to that consumption. A job-level
authorization clamps the pack/tool/effect/classification/session/resource/watchdog envelope;
each private request is revalidated against the signed input schema and current hash-chained
event head before one grant is issued. A watchdog stop returns no grant. The supported-host
fixture proves the real tool call as part of the lifecycle. Durable request custody and event
settlement are now implemented internally: a
single-flight owner-private queue transfers the request out of the pending spool, chains
content-free custody records, stages transient structured output under job-only retention,
appends one exact watchdog event, and publishes one response. It recovers every recorded
phase. `authorized` may still proceed; `launching` is the no-replay horizon and can only settle
success from already-staged output or conservatively return `uncertain-no-replay` after exact
runtime cleanup/status inspection. Until profile routing, job-lifecycle accounting/retention,
and enabled end-to-end evidence are complete, this substrate exposes no enabled Pixel tool.

The OMP registration bridge is internal and disabled unless an exact reviewed capability
binding is present. The controller derives
a fresh catalog from one exact job authorization and signed pack; the catalog filename binds
its canonical bytes and the worker queue accepts exactly that catalog plus its request and
response directories. A trusted static extension registers only deterministic job-scoped
`pixel_cap_*` aliases, preserving each signed raw input and output schema. Calls are serialized
so the next request can name only the prior validated settled event head. A response must bind
the exact request, authorization, checkpoint, classification, lifetime, output schema, and
content digest. Any ambiguity after publication closes the extension session rather than
risk a stale-head retry. The extension itself has no network, credentials, child-process,
grant, completion, or ambient tool-registration authority. The supervised Scout, Builder,
Data Lab, and Researcher profiles now mount the exact catalog and queues only for an admitted
attempt; unrelated workers and independent verifiers receive none.

The Deep Work knowledge vault is also outside gateway memory. Its externally held master
key derives separate wrapping and search keys per vault/client; every source receives a
fresh authenticated-encryption key. Persisted search terms are keyed fingerprints, and a
request sees only its exact owner/client partition at or below its classification ceiling.
Retrieval is a single-use read at one vault-head hash and returns cited untrusted data, not
instructions. Source lifecycle state is atomic and recoverable; deletion leaves a
content-free tombstone. A disabled lifecycle foundation can atomically rotate the external
key hierarchy and reconcile the authoritative deletion ledger into a historical backup
without restoring deleted ciphertext. An opted-in controller may perform one exact local
retrieval for a running Scout, Builder, or Data Lab checkpoint and append the result as
quoted, untrusted, attempt-only prompt data. The worker receives neither the master key nor
the vault filesystem; the gateway receives neither the key, filesystem, nor plaintext.

The local portal is now shaped as the primary owner interaction surface rather than a
standalone status dashboard. Its chat adapter is deliberately narrower than the OpenClaw
gateway: it resolves one fixed executable, owner-private state directory, configuration,
and agent identity from private onboarding; writes each message to an owner-private transient
file; and invokes only the fixed `agent --agent ... --session-key ... --message-file ...`
form. The message is never placed in the process arguments. Browser requests cannot select
the launcher, environment, agent, session, path, tool, or command. Private conversations are
bounded, atomic, and hash chained; caller request identities are idempotency keys, and a
crash-visible running turn becomes `interrupted` on restart. A second narrowing projection
uses process-lifetime opaque handles and returns only bounded user/assistant text and
content-free state/tool counts. It strips raw launcher output, session metadata, tool names,
paths, hashes, credentials, and any authority created by the underlying agent.

Deep Work operator state crosses two narrowing boundaries. The controller builds a strict,
owner-private atomic snapshot from already-safe facts and replaces internal job/event
identities with keyed display handles. The loopback control service then performs an
independent exact-shape and semantic validation, removes the handles, applies a two-minute
freshness rule, and returns only fixed codes and bounded aggregates. The browser has two
separately policy-gated, fixed mutations: one-way future-scheduling pause and creation of an
inert private long-goal draft from server-fixed policy/catalog/object/draft paths. The
authoring view turns catalog IDs into process-lifetime keyed input handles and binds the
translated brief plus current private files into exact confirmation. It cannot admit data,
compile, stage, schedule, or run the draft. Cancel, resume, scope/budget expansion, egress
approval, private evidence, and artifact access stay outside the page. Goal-service and
knowledge-runtime wiring are implemented but remain disabled unless the reviewed private
controller and separate service lifecycle explicitly activate them.

The Codex Deep Work provider is a fourth separate boundary, not an extension of the
gateway, Operations Broker, or Frontier Broker. Its implemented compiler accepts one
private, local-attempt-bound request and one disabled-by-default private policy. It rejects
never-egress and credential classes, replaces declared and common identifiers, assigns
synthetic document identities, and emits an exact sanitized capsule plus a non-executable
private plan. The plan binds the local request, checkpoint, policy, capsule, structured
output schema, model, authentication/billing mode, token/byte/time limits, exact isolated
transport identity, and a privately salted replacement-map commitment. Only the capsule
and output contract are eligible for the provider stdin boundary; owner/client identity,
private request, mapping, credential location, and approval evidence remain broker-private.
The test foundation persists canonical owner-only
bundles, proves exact recompilation, consumes a synthetic authorization before a bounded
mock adapter, strictly validates structured output, rehydrates issued placeholders locally,
and emits a content-free no-authority receipt. Both mock authorization and mock execution
require explicit test flags. A separate single-use credential-free qualifier runs only an
empty-auth fake CLI with no inherited task data in argv, validates one bounded no-tool
JSONL turn and exact final message, kills the process tree on failure, and deletes the
sterile runtime. A separate production-shaped verifier accepts only one short-lived
policy-pinned Ed25519 password-plus-MFA assertion, writes its content-free replay tombstone
before authorization, and exposes no authentication secret. Credential custody validates
private single-link ChatGPT cache or API-key material and emits only pathless content-free
receipts; opaque handle state is not reflectable.

The production-shaped executor admits exactly one explicitly selected boundary: either the
test-only mock with a `test-mock` authorization or the single-use isolated CLI adapter with
an `external-signed-mfa` authorization. The latter validates a pinned runner image, Codex
binary and entrypoint, internal network, pinned non-root proxy, exact egress policy, and
resource/hardening configuration before opening the credential. It streams separated
credential and sanitized task fields in one canonical length frame to a non-root, read-only,
capability-free runner. The runner has no direct external network or DNS and addresses the
inspected proxy by its pinned private IP. The exact-host, port-443 CONNECT proxy binds its
loaded canonical policy at startup, rejects private/reserved DNS answers, and bounds
tunnels without terminating TLS. A consumed authorization is never retried after an
uncertain turn, and success requires verified forced-container removal before any ChatGPT
cache refresh is installed.

These paths are exercised with synthetic fixtures and locally built no-provider images.
The external identity issuer and enrollment, installed service identity and credential,
deployment-owned networks and proxy lifecycle, published image identities, supported-host
qualification, and authorized synthetic live turn remain unimplemented release gates.
