# Pixel Deep Work

Status: Pixel 4.1 post-4.0 implementation and qualification line. Scout, Builder, durable single-job
looping and dependency-ordered multi-milestone goal ledgers,
Researcher, the isolated exact-replay Data Lab, and a sanitized Codex work compiler with an
owner-private mock lifecycle, credential-free fake-CLI qualification, signed-MFA verifier,
broker-private credential custody, and an outer-isolated Codex runner plus exact-host
egress-proxy foundation have functional candidates; the shipped example policy remains
disabled and none is promoted until the complete Deep Work release gates pass. Local image
construction and no-provider smoke tests used no real credential or provider. Nothing here
grants a production credential path, provider call, merge, deployment, publication,
purchase, message, or other external effect. The Pixel 4.0 candidate and its evidence
remain frozen; `LIVE-AUDIT-4.1.0.md` is authoritative for this successor's release gates.

The DSV4 comparison line also treats GPU maintenance as authority. Formal Pixel and Codex
arms each start a fresh two-GPU backend and therefore cannot run beside the normal
all-GPU production container. The bounded campaign-maintenance controller binds one exact
resumable batch, stops only the reviewed production identity, requires exclusive GPU
custody and complete comparison-resource teardown, then restores and probes the same
service. It never turns “reserve a maintenance window” into an untracked manual stop or
an arbitrary shell grant.

## Goal

Deep Work lets Pixel turn a bounded coding, data, or research objective over to a
methodical executor for as many useful event-driven work, verification, repair, and
recovery cycles as the acceptance criteria require, without reducing that executor to
command-by-command approval prompts. Wall time is neither the scheduler nor evidence of
progress. The intended balance is broad autonomy inside a small disposable world and hard
broker-enforced boundaries at every consequential edge.

Oh My Pi (OMP) is the first planned local executor adapter. Pixel does not treat OMP, its
model, a repository, a dataset, dependency output, test output, or web content as an
authority. OMP may run its productive tool surface and headless subagents without
interactive prompts only after the Work Broker issues an exact, expiring, single-use
capability lease. The outer runner, not OMP's prompt or approval mode, enforces the
security boundary.

## Components and trust boundaries

```text
OpenClaw / Pixel gateway
        |-- may submit typed requests and read bounded projections
        |-- has no runner, provider, host, or broker credential
        v
Work Broker (dedicated identity, private policy and state)
        |-- validates request and immutable input snapshot
        |-- compiles exact plan and one-use capability lease
        |-- reserves runtime, model, token, network, artifact, and failure budgets
        |-- cannot merge, deploy, publish, purchase, or send external messages
        v
Runner supervisor
        |-- creates fresh home and disposable workspace
        |-- inherits no environment, descriptors, user config, OAuth, or agent state
        |-- mounts inputs read-only and artifact staging write-only
        |-- exposes only lease-named loopback/broker services
        |-- applies CPU, memory, process, disk, runtime, output, and network ceilings
        v
OMP adapter over bounded RPC
        |-- broad read/write/execute/debug/subagent autonomy inside the workspace
        |-- no host filesystem, Docker socket, SSH agent, browser session, or direct network
        v
Independent verifier
        |-- starts from immutable criteria and a separately materialized output snapshot
        |-- trusts neither worker assertions nor worker-generated test selection
        v
Artifact gate
        `-- releases only declared, hashed, size-bounded patches/artifacts and evidence
```

The Work Broker is a separate boundary from the Operations Broker. Operations executes
fixed, reviewed actions against pinned targets. Deep Work executes untrusted arbitrary
project code in a disposable target. Mixing these responsibilities would allow a hostile
repository to inherit operational authority.

The existing Frontier Broker also remains separate. Its plan-review and failure-triage
capsules do not become a generic repository or data egress path. The separately governed
Codex work-provider foundation has its own strict private request, policy, sanitized
capsule, output, authorization, claim, result, and preview-plan contracts. It can persist,
consume, validate, and rehydrate only an explicitly enabled mock lifecycle. A separate
one-use qualifier proves sterile command, environment, process-tree, transcript, usage,
timeout, flood, and cleanup handling against a credential-free fake CLI; it cannot enter
the executor. A separate verifier can convert one short-lived, policy-pinned, signed
password-plus-MFA assertion into one exact authorization, and custody checks validate a
synthetic private ChatGPT cache or API-key file without projecting it. Only an external-
signed-MFA grant can enter the separately enabled outer-isolated adapter. That adapter
pins a non-root, read-only runner image and a single exact-host CONNECT proxy, streams the
credential through a framed private channel, and returns only a schema-bound result and a
validated optional ChatGPT cache refresh. No production identity issuer, service,
credential, network, published image, or provider call is configured. See
`CODEX-WORK-PROVIDER.md`.

## Request is not authority

`schemas/work-job-v1.schema.json` describes what Pixel is asking for. It contains logical
input identifiers and hashes, never host paths or credentials. It can request capability,
but it cannot grant capability.

`schemas/work-policy-v1.schema.json` is private broker policy. Its example is disabled and
pins the exact OMP release URL, artifact digest, RPC version, runner image digest, profile
limits, retention, and mandatory security switches. `schemas/work-plan-v1.schema.json` is
the broker's immutable compilation of a valid request against that private policy and a
content-addressed input manifest. A plan contains no execution authority.

`schemas/work-capability-lease-v1.schema.json` is the broker's grant. It binds:

- one job, plan, input set, private policy, executor artifact, runner image, and RPC version;
- one local model provider, model identifier, backend image digest, context window, modality, and endpoint contract;
- exact tools and broker services;
- one disposable workspace shape;
- hard resource, iteration, model, token, network, artifact, and failure budgets;
- independent verification and no automatic merge/deployment;
- explicit false authority for host access, ambient credentials, arbitrary networking,
  external effects, acceptance mutation, policy mutation, publication, purchase, merge,
  deployment, and lease expansion.

The lease must be no broader than the request and expires within seven days. A request,
repository file, model response, web page, test result, checkpoint, or prior lease cannot
issue, extend, replace, or replay it.

The initial `compile-scout` broker command accepts only fresh Scout requests, private
owner-only policy and input manifests, and single-link content-addressed objects. It writes
one private plan/lease bundle atomically and refuses duplicate compilation. Execution and
lease consumption are implemented only inside the isolated profile runners and remain
disabled in the example policy until exact-candidate qualification passes.

`schemas/work-lease-consumption-v1.schema.json` defines the immutable replay tombstone.
The runner revalidates the policy, plan, lease, input set, executor artifact, runner image,
expiry, capabilities, and budgets; materializes the workspace; and only then atomically
consumes the lease. A crash after consumption cannot silently retry the same lease.

`schemas/work-lease-revocation-v1.schema.json` adds the conservative pre-launch
counterpart. Consumption and revocation compete for the same immutable per-lease tombstone
path, so exactly one can win. A revocation binds the exact plan, lease, parent goal
checkpoint, reviewed child checkpoint, and cancellation review without containing work
content or execution authority. Once stored, the normal runner cannot consume that lease.
The controller admits only that exact revoked run bundle, records an `authorized ->
cancelled` iteration-zero child with no worker, artifact, verifier, progress, or usage
evidence, and then reconciles the parent to `cancelled`. A crash after any boundary resumes
from the revocation and append-only ledgers. A consumed lease instead requires supervised
worker cleanup; the revocation alone can never make a started child look stopped.

## Capability profiles

| Profile | Productive freedom | Boundary |
|---|---|---|
| Scout | Read and structured search analysis | Read-only snapshot and one brokered local-model service; no external network; LSP deferred until its mutation surface can be mechanically removed |
| Builder | Full workspace write, shell, eval, debugger, LSP, and subagents | Disposable workspace; brokered local model and optional package courier |
| Data Lab | Builder tools plus local Python/SQL/data processing | Raw inputs read-only; local-only model; derived artifacts only |
| Researcher | Structured web, academic, and discussion research | Sanitized queries through the Research Broker; sources remain untrusted |
| Public Project | Builder with separately approved remote model eligibility | Public inputs only; exact provider/budget approval; still no external effects |

A normal authorized Builder job should run without a mid-job approval prompt. Pixel asks
again only if the worker needs a new input, service, data classification, budget, provider,
or external effect.

## Isolation contract

The runner must start with a sterile environment. It does not expose or import:

- the operator's home, `.env`, `.omp`, provider config, OAuth cache, shell history, Git
  credential helpers, SSH agent, browser profiles, clipboard, desktop, or unrelated workspaces;
- host PID, IPC, user, network, mount, or device namespaces beyond the qualified runner
  contract;
- Docker/container sockets, privileged devices, arbitrary Unix sockets, host service
  control, package-manager credentials, cloud metadata, or local network discovery;
- OpenClaw skills, MCP connections, plugins, memory, collaboration relays, or config
  discovery unless an exact future lease field and independent security review add them.

OMP's own workspace isolation, approval policy, secret obfuscation, and tool denials are
defense in depth. They are not substitutes for the outer boundary. A repository-controlled
configuration file must not be able to select providers, re-enable discovery, load an
extension, change approval semantics, add a workspace root, or expose a broker connection.

The OMP 17.2.12 adapter audit is pinned to release source commit
`45e12e5bb758198a920c6070e7e64cb33b21beac` and Linux artifact SHA-256
`6c75331bf09d5a9e9433bd592b3ee993d751a15d5b7450c1a334cc0684996f30`. The audit confirmed
that CLI tool selection alone is not a security boundary: project-local custom tool trees
can be discovered separately. The CLI's LSP tool also advertises mutating rename,
code-action, reload, and raw-request operations because its read-only switch is SDK-only;
initial Scout therefore omits LSP instead of relying on approval prompts and a read-only
mount to contain an unnecessarily broad tool schema. Scout accepts only strict POSIX ustar inputs,
rejects links, devices, extensions, traversal, collisions, malformed headers, and byte or
entry bombs, and moves agent/editor/VCS/environment control trees into `__pixel_inert__`
before OMP starts. Those files remain readable evidence without retaining their active
names. The RPC client additionally requires OMP to disclose exactly `read`, `grep`, and
`glob` before it receives the objective.

`deploy/work-runner/omp-runtime.json` binds the executable identity to its measured Linux
memory edge. The real RPC `read` probe was OOM-killed through 1,040 MiB and first survived
at 1,048 MiB; Pixel adds 488 MiB of operating headroom and therefore refuses Scout,
Researcher, or Data Lab workers below 1,536 MiB. Builder retains its independently
qualified 2,048 MiB code-intelligence floor. These are admission minimums, not estimates
of total host capacity, and every profile still remains subject to its exact upper budget.

The container command is constructed as an argument vector with no shell and no user data
in its process arguments. It binds an exact image digest and executor digest, runs as the
dedicated unprivileged runner identity with a read-only root and workspace, drops every
capability, enables no-new-privileges, disables logs and PTY/session/config discovery, and
applies process, memory, CPU, descriptor, temporary-storage, and time ceilings. Its only
network is a non-attachable internal per-job bridge containing one lease-scoped model proxy.
The proxy alone is temporarily attached to the model backend's separate internal bridge;
the backend must have no second network, host network, or non-loopback published port. The
backend container must also carry exact private-policy identity labels for its provider,
hashed model ID, artifact, backend version, accelerator class, prompt contract, and tool
schema. CPU backends receive no accelerator or direct-device grant; NVIDIA admission permits
one canonical Docker GPU request and rejects direct devices, ambiguous combined grants, and
unknown request fields. `./pixel work-model-backend inspect --config PRIVATE_JSON`
remeasures the exact artifact, rebuilds its launch, and projects only content-free readiness
after checking the actual Docker image, command, mounts, safe effective environment, resources,
device grant, and private subnet and has no mutation authority.
The companion `review` and exact-confirmed `render` operations measure the selected GGUF
bytes or canonical materialized vLLM directory, reject link/race/ownership substitution,
and write only a new owner-private inert pair of Docker argument vectors. They perform no
Docker call. The rendered stopped-container contract pins a read-only non-root boundary,
resource and private-shared-memory ceilings, disabled logging and pulling, identity labels,
an explicit internal non-attachable subnet disjoint from per-job networks, optional
loopback-only publication, and no accelerator
or one counted NVIDIA grant. `start-review`/exact-confirmed `start` and
`stop-review`/exact-confirmed `stop` are the only mutating lifecycle. They remeasure and
rebuild the same bundle, never pull, reconcile exact partial Docker state, require a real
health event within a monotonic safety deadline, attest final effective runtime, refuse
foreign resources or active worker peers, and remove only exact empty resources. Requested
loopback publication counts as ready only when Docker reports the effective loopback bind.
vLLM receives a bounded owner-mapped ephemeral
compile-cache tmpfs with offline Hub behavior and telemetry disabled; no shared host cache or
credential is mounted.
The example policy remains disabled until an exact real model, backend, and OMP profile run are
qualified for the candidate release.

## Network and research

`none` means no network service and a zero-byte network budget. `brokered` means the
worker can reach only named job-scoped services. Initial services are:

- `local-model`: a lease-bound local inference endpoint;
- `package-courier`: allowlisted dependency retrieval with hashes, size limits, caching,
  provenance, and installation-script policy;
- `research-broker`: sanitized search/retrieval with query, domain, source, download,
  retention, and citation bounds;
- `frontier-work-provider`: reserved for the separately governed Codex work executor; its
  compiler and private mock-lifecycle contracts exist, while the brokered live service
  remains unavailable.

Pixel's existing SearXNG and Web Courier form the reference research backend. Vane is an
optional adapter, not a trusted control plane or direct worker dependency. It must remain
loopback-only behind the broker, return source records, and beat the reference backend on
quality, citation correctness, reliability, and cost before entering the reference profile.
Web results and downloaded documents are hostile inputs and cannot authorize tools or alter
the job contract.

## Reliability and extension layer

The Dream Forge source audit at pinned commit
`ab565fc851d5de7bec2878b684e2665d77431b7d` identified useful product patterns without
changing Pixel's trust model. Its highest-value contribution is durable long-horizon work:
goals that retain exact acceptance criteria and lineage across many bounded worker turns,
measure real progress, recover from interruption, independently verify milestones, and
wait for authority instead of either stopping prematurely or widening themselves. Pixel
is independently adding that behavior together with empirical model qualification,
normalized event watchdogs, broker-issued hash-bound context capsules, durable session
lineage, isolated capability-pack/MCP adapters, a provenance-bound classified knowledge
vault, and clearer activity/service/artifact UI. A global full-auto bypass, direct agent
tool dispatcher, implicit recent-memory injection, model-name trust, and silent executable
JSON repair are explicitly out of scope. See `DREAM-FORGE-SOURCE-AUDIT.md` for the matrix
and gates.

The first reliability foundation now emits content-free capability receipts for one exact
model artifact/backend/accelerator/prompt/tool contract and exact benchmark/evaluator
hashes. Cases name the profiles they exercise, routing admits only profiles with complete
passing category coverage, and every admitted task remains inside the conservative tested
context and output envelope. Estimated usage, intent mutation, stale evidence, corpus or
evaluator substitution, and identity drift fail closed instead of being presented as
capability. The fixed credential-free runner now makes thirteen sequential calls only to an
explicit IPv4 loopback local OpenAI-compatible origin. Measurement supports llama.cpp,
Ollama, vLLM, and a generic local provider identity; contained runtime admission currently
supports only llama.cpp and vLLM. The vLLM adapter preserves bounded low reasoning while
suppressing reasoning disclosure and requiring exact streamed usage. OMP continues to see
only Pixel's pinned llama.cpp-compatible facade, never the backend directly. The suite tests
a 128 KiB long-context sentinel with
backend-observed token usage and a
sustained exact 1K-token structured-output floor rather than treating a requested ceiling as
observed capability. Its new owner-private receipt, no-follow input/output handling,
bounded strict responses, no redirect/retry behavior, and content-free summary are covered
by hostile and real-loopback tests. Private policy pins the receipt, corpus, evaluator,
weights, backend, accelerator, prompt, and tool contracts. Every fresh or continued worker
revalidates that evidence for its exact profile before workspace materialization; cleanup
recovery remains available without model launch. The per-job model proxy carries the
admission binding, requires exact backend usage, clamps output before inference, and rejects
over-context results before disclosure. A trusted-terminal review/apply/status workflow now
binds the receipt into a new private policy without hand-copying hashes, refuses overwrite
and linked inputs, changes no enablement or authority, and projects no private identity or
content. A distinct enable-review/enable-apply workflow then requires current exact
qualification for every already-enabled profile and writes a new policy that changes only
top-level enablement. It neither edits the reviewed profile/tool/budget/security envelope
nor installs the result, starts a model/service, routes a job, or grants an external effect.
The comparison adapter's destination-bound policy-review/policy-apply handoff then verifies
that enabled policy and its current DSV4 receipt, binds the old configuration, old and new
policy identities, and exact new destination into one confirmation, and writes a new private
system configuration that changes only `policyTemplatePath`. This removes the manual pointer
edit without mutating the current configuration or granting model start, task, network,
credential, publication, deployment, or other execution authority. Policy and receipt
readers use duplicate-key-rejecting strict JSON so alternate decoded interpretations cannot
cross the qualification-to-run boundary.
The enclosing pair configuration now crosses an equivalent destination-bound review/apply
boundary. It accepts only the exact successful Pixel system policy-binding receipt, binds
the current pair bytes, the resulting Pixel system bytes, a new pair destination, and a new
unused preflight destination, then writes a new private pair configuration changing only
those two paths. It rejects hand-edited Pixel configuration, forged or weaker transition
receipts, linked inputs, overwrite, stale preflight reuse, and post-review substitution;
it starts no model, task, service, tool, or network and grants no execution authority.
The contained Docker qualification confirmation also binds the exact owner-private Docker
configuration bytes, including the private receipt destination; substituting an otherwise
valid empty destination after review invalidates the confirmation before image inspection,
model start, or container creation.
Supported-host event-driven supervisor qualification is complete for the exact
candidate on Ubuntu 24.04 and Debian 12. An exact real-model/OMP profile run and first-time
operator usability remain release work; the receipt itself grants no execution. Its
preflight watchdog binds a trusted event-chain head, canonical tool/argument fingerprint,
checkpoint, effect class, and loop/failure/progress budgets before another call. Context
capsules are private owner-only immutable records bound to an exact plan and checkpoint;
forking starts a new job lineage, cannot lower data classification, and never carries a
reusable lease, approval, credential, or completion claim. These foundations remain
non-authoritative; their controller integration remains disabled until qualified by the
release gates below.

The owner-facing `./pixel work-context` lifecycle makes that foundation usable without
moving private context into the browser. Start from
`deploy/work-controller/context-session-input.example.json`. `create-review` binds an
owner-only plan, its exact checkpoint, the private context file, the private state root,
and a 1-to-365-day lifetime into one content-free confirmation. `create-apply` reopens all
of those files and atomically publishes one immutable capsule. Eight simultaneous applies
of the same confirmed review converge on that same capsule rather than creating parallel
sessions. `list` and `inspect` return only session identity, lineage, classification,
checkpoint state, time, and bounded counts. `show` is the sole explicit content-bearing
operation and prints the exact decoded context only in the trusted terminal.

`fork-review` and `fork-apply` additionally require the exact current parent capsule and
its trusted hash. The child must use a distinct job, cannot lower classification, and gets
neither the parent's lease nor any reusable approval. The plain-language
`./pixel work-context-guide create|fork|remove` wrapper explains the local effect and
requires `APPLY <last-12-hash>` before mutation. Removal moves the one exact capsule
through private crash-recovery custody, writes a content-free tombstone, and resumes safely
after interruption at every custody boundary. It does not claim storage-media erasure or
deletion from historical backups. `list` reports a content-free `recovery-attention` entry
whenever removal custody remains, including when the live capsule has already disappeared.
All records must remain owner-private regular files. A capsule is single-link except for
the exact, independently verified hard-link custody interval during removal; unexpected
links fail closed.

The OMP RPC boundary now also observes the real tool stream for every local profile. It
hashes canonical tool arguments and completed results into a session-keyed, content-free event chain,
stops after three consecutive identical successful outcomes, two identical failures, or
six exact A/B outcomes, and retains only the count and chain head in execution evidence.
The result is part of the fingerprint: rereading the same path after an edit is allowed
when its output changed, so the guard does not mistake demonstrated progress for a loop.
OMP emits `tool_execution_start` immediately before its internal execution; consequently
this guard can terminate after a repeated completed outcome, not pre-authorize an opaque
internal call. Controller-owned MCP calls still use the stronger pre-effect watchdog.
The outer runtime, tool-call, stream, and resource ceilings remain the final bounds.
The first turn is provider-forced to one leased evidence tool (`read`, or
`pixel_research` for Researcher) because the qualified Qwen/llama.cpp route does not
reliably emit structured calls under automatic selection. If a later assistant result is
exactly one JSON tool envelope naming an already leased tool, Pixel does not execute those
arguments. It gives OMP at most three chances to reissue the request through the observed
structured-tool channel; every other envelope, authority expansion, or exhausted recovery
budget fails closed.

The durable goal foundation adds `schemas/work-goal-v1.schema.json`,
`schemas/work-goal-checkpoint-v1.schema.json`, and
`deploy/work-controller/goals.mjs`. A goal binds an immutable, acyclic, canonically ordered
graph of exact child-job hashes, profiles, classification, dependencies, and aggregate
budgets. Its owner-private hash chain records one active child at a time, exact child
checkpoint observations, completed milestones, and monotonic aggregate usage. Recovery
never labels a recorded dispatch as replayable: the controller must recover or continue
the already named child, whose independent one-use job lease remains the actual execution
boundary. A milestone enters the completed set only from a valid child plan and a child
`completed` checkpoint with every criterion passing and independent verification evidence;
the goal enters `completed` only after every dependency-ordered milestone is in that set.
Child waiting-authority, failure, cancellation, budget exhaustion, and no-progress states
propagate without inventing retries. The goal record itself grants no execution, lease,
scope expansion, external effect, or completion authority. This is the durable scheduling
core. Production process supervision and the durable scheduler/service publication are
implemented, as are plain-language resume/cancel journeys. The event-horizon and
supported-host results are promotion evidence only when they bind the exact unchanged
candidate source; first-user acceptance remains a separately owned gate.

`./pixel work-input-pack --selection PRIVATE_SELECTION.json --output
NEW_PRIVATE_INPUT_BUNDLE` admits explicitly selected local directory trees without asking
an owner to calculate hashes or build archives. It rejects missing/symbolic source
components, links and special files inside a tree, cross-platform path collisions,
unsafe POSIX archive names, output/source overlap, source mutation, and per-input entry,
file, extracted-byte, and archive-byte limit breaches. Files are streamed into deterministic
uncompressed POSIX ustar snapshots with normalized ownership, modes, and timestamps.
Agent instructions, environment files, repository metadata, and other recognized control
files remain present only under the runner's inert projection. The command atomically emits
`objects/<sha256>.tar`, `input-catalog.json`, and a private path-free inventory manifest;
identical trees share one object. Its terminal receipt contains only counts, byte totals,
hashes, and false authority. It performs no network request and grants no future read or
execution right. Start from `deploy/work-controller/input-selection.example.json`.
For Data Lab input, the owner also names each dataset's relative file, stable identifier,
and format. Pixel derives the raw file hash and size from the exact streamed snapshot,
rejects missing, empty, disguised-extension, duplicate, or inert-control-path datasets,
and carries that evidence into the private catalog. The corresponding example is
`deploy/work-controller/input-selection-data.example.json`.

`./pixel work-goal-draft --brief PRIVATE_BRIEF.json --policy PRIVATE_POLICY.json
--input-catalog PRIVATE_INPUT_CATALOG.json --object-store PRIVATE_OBJECTS
--output NEW_PRIVATE_DRAFT` is the first guided long-goal authoring boundary. The owner
writes ordinary objectives, "done when" statements, dependencies, selected input names,
and a `quick`, `standard`, or `deep` effort choice. `inspect`, `build`, and `research`
map only to the already enabled Scout, Builder, and Researcher profiles;
`analyze-data` maps to the enabled Data Lab. Pixel derives the
exact tools, brokered services, outputs, independent verification, and budgets from the
private policy; requested effort is always clamped down to policy and never widens it.
Public research cannot receive private classifications. Every selected input is resolved
from a content-addressed private catalog and rehashed in the object store before any draft
is published.

The command atomically emits the exact child requests, one input manifest per child, the
goal declaration, and a private human-readable review bound to every source hash. The
terminal receipt is content-free. It creates no plan, lease, goal ledger, controller,
service, schedule, network request, or external effect. Unknown fields, dependency cycles,
disabled profiles, input substitution, policy drift, linked control files, and competing
publication fail closed. Sanitized starting shapes are
`deploy/work-controller/goal-brief.example.json` and
`deploy/work-controller/input-catalog.example.json`; the latter is the exact output shape
produced by `work-input-pack`. A complete private-data starting brief is
`deploy/work-controller/goal-brief-data.example.json`.

The loopback UI now provides the corresponding nontechnical authoring path without turning
the browser into a file picker or agent controller. After the operator fixes owner-private
policy/catalog/object/draft paths in `control/work-authoring.example.json`, enables
`actions.deepWorkDraft`, and launches `./pixel ui --work-authoring-config PRIVATE_CONFIG`,
the exact startup URL unlocks a process-lifetime view of enabled work types and generic
  opaque input summaries. The owner can build up to sixteen milestones with objectives,
  done-when checks, admitted inputs, effort ceilings, and an acyclic branch-and-converge
  dependency graph. Each new card safely defaults to waiting for the preceding milestone;
  the owner may instead select any unique set of earlier milestones, while forward links,
  cycles, self-links, duplicates, and noncanonical ordering fail closed. The controller
  still admits one deterministic eligible milestone at a time and advances only from
  durable independent evidence. Research is public and input-free;
Data Lab requires an admitted dataset. Exact confirmation creates only an inert private
draft under the fixed draft store. The browser cannot supply or learn paths and cannot
admit input, compile, prepare, stage, schedule, execute, approve egress, expand scope, or
declare success. Stale private policy/catalog/configuration, malformed catalogs, unknown
handles, classification downgrade, output races, and full retention all fail closed. The
confirmed brief, policy, and catalog bytes are copied into owner-private single-use
snapshots before the fixed drafter starts, so a same-user edit cannot silently change the
reviewed job structure during process launch.

The same process-lifetime token also unlocks a pathless retained-draft review at
`/api/v1/deep-work/drafts`. Unlike ordinary content-free status, this owner-requested view
shows the exact retained goal objective, milestone objectives and dependencies, done-when
criteria, policy-clamped budgets, tool and broker envelopes, verification method, and one
canonical review digest. It reconstructs every card from `goal-draft.json` only after the
complete draft directory and review shape pass strict private-file validation. One malformed,
linked, widened, duplicated, or unexpected retained draft makes the entire inventory
unavailable instead of returning a partial set. Host paths, input identifiers, job IDs,
credentials, and source hashes are not projected. The digest is evidence for the next
review boundary; the view itself cannot prepare, compile, stage, schedule, execute, accept,
or complete work.

An operator may separately install `control/work-launch.example.json`, enable
`actions.deepWorkPrepare`, and pass `--work-launch-config PRIVATE_CONFIG` to `./pixel ui`.
The fixed configuration binds the already shared draft directory, one owner-reviewed
controller environment, a private launch directory, and a retention ceiling. A review card
then offers one exact-confirmed preparation action using only its process-lifetime opaque
handle and canonical review digest. The server reopens the entire draft and fixed
configuration, snapshots the environment bytes, and invokes the existing
`work-goal-launch prepare` compiler. Its atomic output contains every compiled child and
expiring single-use lease, but remains `prepared-inactive`: it creates no ready goal ledger,
service, schedule, worker, provider request, external effect, or completion authority.
Already prepared draft digests cannot be prepared again through this surface. Changed
draft, policy, input, environment, configuration, full storage, malformed package, and
post-preview drift all fail closed. Dormant staging requires the next separately enabled
boundary; service rendering and activation remain later boundaries.

Enabling the distinct `actions.deepWorkStage` flag adds a second exact-confirmation button
to a validated package card. The card projects only an opaque package handle, exact manifest
SHA-256, child count, profile set, earliest lease expiry, and whether a matching private
stage receipt is retained. It exposes no goal ID, job ID, input identity, or path. Preview
and execution reopen the complete draft, package, compiled children, fixed environment, and
any existing stage receipt. Execution invokes only `work-goal-launch stage` with the fixed
private package and displayed manifest digest. Staging is idempotent and crash-recoverable:
it creates or revalidates exact child custody and one dormant ready checkpoint, but starts
no worker, timer, controller service, model request, provider call, external effect, or
completion action. Service rendering and activation remain separate boundaries.

Enabling the independent `actions.deepWorkServiceRender` flag, installing
`control/work-service.example.json`, and passing `--work-service-config PRIVATE_CONFIG`
adds a third exact-confirmation boundary. It is available only after Pixel revalidates the
exact launch package and matching private stage receipt. Execution invokes the fixed
`goal-service-cli.mjs render` command and atomically retains only the exact manifest, path
unit, watchdog timer, and controller service unit in a new private bundle. The projected
receipt is pathless and explicitly `rendered-inactive`; it identifies the event-driven
execution model and liveness-only watchdog role. Rendering cannot install or activate the
units, reload systemd, start a worker, schedule work, consume a lease, make a model or
provider request, produce an external effect, or claim completion. Changed configuration,
substituted stage custody, malformed units, duplicate goal bundles, full retention, and
post-preview drift fail closed. Installation and activation remain later trusted-host
boundaries.

`./pixel work-goal-assemble --draft PRIVATE_DRAFT --environment
PRIVATE_ENVIRONMENT.json --confirm-draft-sha256 REVIEWED_HASH --output
NEW_PRIVATE_ASSEMBLY` is the shortest safe bridge from that review to controller wiring.
One exact confirmation reopens the complete private draft, verifies its declaration,
children, per-child input manifests, review projection, and hashes, then prepares the goal
and controller as one atomic parent directory. The controller configuration is bound to
its final published paths even though the complete assembly is built off to the side, so
an interruption cannot publish a half-controller or leave stale temporary paths. The
result contains the retained `review.json`, `goal/`, `controller/`, and an exact
`assembly.json` manifest. It remains
inert: no child is compiled, no lease or ledger is created, and no worker, timer, service,
network request, or provider is started. Compilation, exact controller confirmation,
dormant staging, and service activation remain later terminal boundaries.

For ordinary guided goals, `./pixel work-goal-launch prepare --draft PRIVATE_DRAFT
--environment PRIVATE_ENVIRONMENT.json --confirm-draft-sha256 REVIEWED_HASH --output
NEW_PRIVATE_LAUNCH` removes the per-milestone compilation ceremony. It reopens the same
complete reviewed draft, atomically builds `assembly/`, verifies every content-addressed
input again, and compiles all one to sixty-four exact children into `compiled-jobs/`.
`launch-preparation.json` binds the draft, assembly, controller, private policy, and every
plan and expiring single-use lease. Publication is all-or-nothing under interruption and
concurrent attempts. This boundary contains dormant authority but creates no goal or child
ledger, starts no worker, service, timer, provider, or network request, and grants no
execution, scheduling, scope expansion, external effect, or completion authority.

`./pixel work-goal-prepare --declaration PRIVATE_DECLARATION.json --jobs
PRIVATE_JOBS.json --output NEW_PRIVATE_BUNDLE` removes the most error-prone manual authoring
step without giving a model scheduling authority. The declaration names only the overall
objective, exact classification, milestone identifiers, child-job identifiers, and sorted
dependencies. Pixel validates every already bounded child job, rejects missing or extra
children, classification drift, future-dated work, cycles, and noncanonical ordering, then
derives every child hash and the exact sum of runtime, model, token, network, artifact, and
failure ceilings. It atomically publishes owner-private `goal.json`, canonically ordered
`jobs.json`, and a content-free hash manifest in a new directory. Sixteen competing
preparers converge on one complete bundle. The command creates no plan, lease, controller,
service, or checkpoint and starts no work. A sanitized declaration shape is provided in
`deploy/work-controller/goal-declaration.example.json`.

The existing Work Broker compiler is also reachable as `./pixel work-compile`; it verifies
content-addressed inputs and private policy before producing one exact plan and expiring
single-use lease. Goal preparation consumes the immutable job requests, not worker output
or a conversational model's proposed hashes. The input packer and guided draft now cover
local-folder admission plus Scout, Builder, public Researcher, and replay-verified Data Lab
requests without letting a model invent authority.

`./pixel work-goal-controller-prepare --goal-bundle PRIVATE_GOAL_BUNDLE
--environment PRIVATE_ENVIRONMENT.json --output NEW_PRIVATE_CONTROLLER_BUNDLE` removes
the hand-written controller-wiring step. Pixel revalidates the complete prepared goal and
its hashes, checks every child against the enabled private policy, hashes the installed OMP
binary against its pinned policy identity, inspects Docker and every private storage root,
rejects writable/immutable path overlap, and requires Researcher queue wiring exactly when
the goal contains Researcher work. It atomically emits `goal.json`, `jobs.json`,
`controller.json`, and a strict content-free manifest. The command creates no state ledger,
plan, lease, service, timer, or worker and performs no network request. The reviewed
environment shape is in `deploy/work-controller/goal-controller-environment.example.json`.
On Linux, set `runtime.uid` to the exact UID of the unprivileged account that owns the
private controller bundle, executor, object store, and workspace root (for the standard
service, `id -u pixel-work`); normally set `runtime.gid` from `id -g pixel-work` as well.
The preparer rejects a mismatched worker UID before it creates custody or consumes a lease,
because an owner-only executor or workspace cannot be entered by a different container UID.
The opt-in job-scoped tool shape is separately shown in
`deploy/work-controller/goal-controller-capability-environment.example.json`; its IDs and
hashes are illustrative and must be replaced by the exact reviewed job and installed pack.
Sixteen competing preparations converge on one complete bundle.

The separate `work-goal-assemble`, `work-goal-prepare`, `work-goal-controller-prepare`,
and per-child `work-compile` commands remain available for expert/custom workflows.
Guided owners normally use `work-goal-launch prepare` after reviewing the draft hash, then
continue with the explicit staging and service boundaries below.

With the guided path, first run `./pixel work-goal-launch inspect --bundle
PRIVATE_LAUNCH`. Inspection reopens every assembly, plan, lease, live policy, and input
object and returns the exact package-manifest hash without mutation. Then run
`./pixel work-goal-launch stage --bundle PRIVATE_LAUNCH --confirm-manifest-sha256 HASH`.
Expert workflows may instead compile each exact child with `work-compile`, place its
two-file output at `COMPILED_JOBS/<jobId>/`, and call `work-goal-stage` directly with the
controller-bundle hash. Staging is the explicit authority-bearing setup boundary: Pixel revalidates each
plan and lease, safely materializes and immediately discards each input workspace to derive
its exact fingerprint, stores dormant single-use child custody, and creates the ready goal
checkpoint only after every child is present. It records a strict stage marker that is
reconciled against live goal and child ledgers on every repeat. Staging starts no worker,
timer, service, network request, or external effect, but its receipt clearly reports that
exact leases and ready scheduling state now exist. Forced mid-stage loss resumes without
widening, eight competing stage commands converge, and invalid compilation or input bytes
leave no goal state behind. Service rendering, installation, and activation remain separate.

The local `./pixel work-goal` command now exposes the disabled controller foundation for
exact `init`, `status`, `dispatch`, `observe`, `complete`, `pause`, `resume`, and `cancel`
operations. Every invocation
rereads owner-private single-link goal/job inputs; `observe` additionally requires the
exact private child plan and lease and then recovers the authoritative child ledger. Its
JSON receipt contains only state, bounded counters, usage, a checkpoint hash, and the next
fixed action. It never returns objective, criterion, file, artifact, model text, credential,
or a grant to execute the named child. This low-level surface is for qualification and
future service wiring, not an enabled end-user full-auto mode.

Pause is a durable scheduling stop: it preserves the exact observation, progress, usage,
failure, and safety facts whether recorded between milestones or while a child is active.
Resume deterministically restores `ready` when no child exists or `running` for the same
active child; it never consumes a job or creates a retry merely by pausing. The runtime
rechecks the goal immediately before every drive. It does not pretend to kill an iteration
that was already launched: that bounded step may finish and checkpoint after the pause,
but future cycles remain no-op and its result is reconciled only after exact-hash resume.
Resume requires the operator to confirm the immutable goal SHA-256 and merely allows the
existing exact lease path to continue; it creates no lease or retry. Cancellation also
requires exact checkpoint-bound confirmation. A goal without an active child can end
directly. An admitted but unlaunched child can end only after its one-use lease is atomically
revoked and a zero-use terminal child checkpoint is recorded. Once lease consumption wins,
Pixel refuses cancellation until supervised cleanup proves the worker stopped, preventing
an orphaned process behind a falsely cancelled goal.

`deploy/work-controller/goal-runtime.mjs` adds the restartable bounded reconciliation
loop behind that surface. It records dispatch before invoking a child driver, atomically
initializes an absent child checkpoint ledger, validates every just-in-time child
plan/lease against the immutable job, and rereads the authoritative ledger after the
driver returns. A crash leaves the goal pointing at the same child; a callback that returns
without a durable child transition yields instead of spinning or claiming progress. The
runtime propagates waiting and terminal child states, advances dependency-ready work,
reports whether the invocation produced a new durable checkpoint, and has a fixed 1..256
transition ceiling per invocation. The resolver/driver boundary is an
internal integration point, not a grant: actual child effects still require the separate
one-use lease and isolated profile runner. Installed service and production driver wiring
are implemented but remain disabled pending the promotion gates.

The runtime checks issuance and expiration immediately before first child-ledger admission
and again immediately before every nonterminal drive. An expired pre-admission proposal
returns a content-free wait and can be replaced just in time by a freshly compiled exact
plan/lease without redispatching or incrementing the goal's job count. Once a child ledger
exists, expired authority can never invoke its driver; its exact state remains available
for explicit cleanup or terminal recovery. Terminal historical evidence remains readable
after lease expiration, but it cannot recreate execution authority.

Builder's first execution boundary also closes the claim/checkpoint crash window. If the
single-use claim was durably consumed but the initial `running` checkpoint was not yet
written, recovery accepts only the exact stored claim while its lease is still current.
Competing controllers then race the atomic `running` append; only its winner may invoke the
worker. A consumed pre-launch claim is never recovered after expiration. Because worker
launch occurs strictly after that append, an `authorized` checkpoint plus the exact claim
proves there was no earlier launch through this controller path.

`deploy/work-controller/goal-builder-driver.mjs` is the first exact profile integration.
It accepts only the complete internal context emitted by the goal runtime, revalidates the
immutable Builder job, plan, lease, goal checkpoint, child checkpoint, workspace digest,
and authoritative child action, and then selects exactly one existing Builder path:
initial/continuation execution, independent-verifier recovery, or cleanup-only interrupted
worker recovery. Prepared work must match every digest before use. The adapter discards the
prepared workspace in all outcomes and returns only state plus a checkpoint digest; patch,
model, objective, criterion, and verifier content never cross its receipt. The goal still
cannot choose tools or tests, and all real effects remain behind the consumed lease and
disposable runner. A two-iteration integration proves that an independently verified
partial candidate issues a reduced continuation lease, preserves candidate lineage, runs
the declared final iteration, and completes the parent goal. Iteration ceilings are exact:
the final declared iteration may run, but no further continuation may be issued. Durable
on-disk plan/lease custody and continuous installed-service wiring are implemented. The
versioned live audit records the exact-source giant-project and supported-host gate state.

`deploy/work-controller/goal-builder-resolver.mjs` removes the remaining in-memory
continuation handoff. When the authoritative child checkpoint calls for another iteration,
the resolver recovers the exact prior consumption tombstone, independently validates the
already persisted reduced continuation lease, and appends it to run custody. Competing
resolvers accept only the identical append winner. A lease issued a few monotonic
milliseconds ahead may be placed into custody, but the runtime still yields
`child-authority-not-yet-valid` and cannot drive it until wall time reaches issuance. A
two-iteration restart test completes with no mutable `currentLease` variable.

`deploy/work-controller/goal-builder-preparer.mjs` closes the next restart boundary. It
requires the goal runtime's plan, lease, snapshot, job, and action to match durable custody,
then reconstructs the prior consumption tombstone and retained patch location itself.
Verifier-only and cleanup-only recovery are reconstructed the same way. A caller cannot
supply a patch path or recovery claim, and regression coverage exercises continuation,
interrupted-worker cleanup, retained-candidate verification, and injected extra fields.

`./pixel work-cycle --config PRIVATE_FILE` is the one-reconciliation diagnostic entrypoint.
The v1 configuration is explicitly enabled, owner-private, and single-link; it routes only
the immutable Builder, Scout, Researcher, or Data Lab profile recorded for each goal child;
it binds absolute goal, job, policy, object, workspace, executor, Docker, model-backend,
UID/GID, archive, and isolated-subnet settings. A goal containing a Researcher child must
also bind one owner-private courier queue and an exact loopback research endpoint; goals
without Researcher work reject that extra research authority. One invocation performs
exactly one bounded reconciliation pass, emits a pathless content-free receipt, and exits.
Paused, waiting, and terminal goals make future invocations no-op. This command does
not install, enable, or start anything by itself.

`./pixel work-continuous --config PRIVATE_FILE` is the production long-horizon activation
boundary. It immediately runs the next reconciliation whenever the preceding pass created
a new durable goal or child checkpoint; there is no time-based pause between productive
steps. It stops synchronously on terminal, pause, authority-waiting, unchanged/no-progress,
or safety state. A 256-reconciliation and six-hour process ceiling forces a clean handoff
without changing the goal, and the durable event watcher immediately resumes a productive
yield. Those process ceilings are supervision boundaries, not a schedule for the work.
The content-free operator heartbeat records this contract explicitly as
`progressModel: durable-events` and `watchdogRole: liveness-only`. Each admitted child
also reports only whether extra job-scoped tools are not configured, configured,
authorized for that exact attempt, or require recovery, plus a count. Tool identities,
arguments, results, pack identities, paths, and hashes never enter the operator view.

`./pixel work-pause --config PRIVATE_FILE` is the dedicated trusted pause entrypoint. It
rereads the owner-private controller configuration, records one fact-preserving transition
from `ready`, `running`, or `waiting-authority`, and republishes the content-free operator
heartbeat. Concurrent or repeated requests converge on the same authoritative paused
checkpoint. A pause between milestones consumes no job. A pause during an already-started
bounded step prevents future controller cycles but honestly reports that the current step
may still finish and checkpoint. The command grants no execution, cancellation, resume,
scope, or external-effect authority; resume remains a separate exact-confirmed terminal
operation.

Resume uses a separate two-step trusted terminal journey. `./pixel work-resume review
--config PRIVATE_FILE` returns a content-free description of the exact paused checkpoint,
whether it will return to `ready` or the same active `running` child, and a review hash; it
does not change scheduling. `./pixel work-resume apply --config PRIVATE_FILE
--confirm-review-sha256 EXACT_HASH` re-reads every private input and accepts only that
still-current paused checkpoint. The confirmed command starts no work itself, publishes a
new heartbeat, and merely enables a later supervised cycle. Concurrent repeats converge
without another transition, and a stale review from an earlier pause is rejected.

`./pixel work-service render` now produces the matching service/path/watchdog-timer set plus a
content-hashed manifest in a newly created private directory. It refuses privileged
service identities, home-directory inputs that conflict with `ProtectHome`, overlapping
writable roots, unsafe paths, rapid polling, non-private output parents, and overwrite.
The service is a locked oneshot running the bounded continuous entrypoint, so a long Builder
iteration cannot overlap its successor. The path unit watches only the immutable goal's
known goal/child checkpoint ledgers and resumes work on durable state change. The timer is
only a stalled/restart watchdog; it is not the work cadence. The service has no ambient Linux capabilities or IP
network namespace, but it does hold the Docker Unix socket; that is documented and treated
as a high-trust supervisor capability. Rendering does not install, enable, or start it.
The render command must run as the same dedicated unprivileged identity named by `--user`;
that identity must also own the private controller configuration and rendered bundle.

`./pixel work-service inspect --bundle PRIVATE_DIR` rechecks exact files, links, modes,
hashes, identities, and supervision anchors without installing anything. The receipt gives
the manifest hash to review. On Linux, root may then run `work-service install` with
`--bundle PRIVATE_DIR --confirm-manifest-sha256 EXACT_HASH`. Install rejects a bundle not
owned by its declared service identity, verifies the copied root-controlled units with the
host systemd parser, rechecks the live private configuration and goal hashes, and leaves
them inactive. `work-service activate` repeats that binding check and requires the same
confirmation separately before enabling the event watcher and watchdog and immediately
starting the first nonblocking activation. `work-service remove` disables and
verifies both, stops the service so `ExecStopPost` can clean an active child, and
removes only exact units; it intentionally retains goal, checkpoint, workspace, and audit
state. Install/removal publication failures roll back to a complete inactive unit set.
No lifecycle operation grants a work lease or expands the underlying goal authority.

Scout, Researcher, and Data Lab now share the same lower-level interrupted-attempt cleanup
contract as Builder: cleanup rehydrates only the exact stored consumption and `running`
or `cleanup-failed` checkpoint, derives profile-specific resource identities, and accepts
only claim-labelled containers, networks, and volumes. It deliberately omits input
materialization and launch paths, and launch/volume-creation builders reject a cleanup-only preparation.
Scout, Researcher, and Data Lab now also have a durable candidate loop: after the profile lifecycle returns
and proves cleanup, Pixel binds the exact retained report/manifest, deterministic
verification evidence, single-use worker claim, usage, and unchanged workspace to a
`waiting-authority` checkpoint. Citation presence or byte-exact replay leaves every
semantic acceptance criterion failing; neither is promoted to completion. Malformed
totals, semantic overclaims, dirty crashes, and exact-budget edge cases fail closed. A
crash before checkpoint publication never retroactively adopts the unanchored output. An
unproven removal is durably `cleanup-failed` with conservatively charged usage; exact retry
may only close that same attempt, while an unsafe recovery inspection becomes terminal
`recovery-inconclusive`. Neither state can launch work, and successful cleanup does not
charge the attempt twice. A disk-derived Scout/Researcher/Data Lab goal adapter resolves only exact
admitted custody, selects the registered profile preparer, drives one initial lease or one
cleanup-only recovery, strips worker content from its receipt, and refuses continuation.
The production profile router and controller configuration now select that adapter from
the immutable job profile. End-to-end tests cover separate process-style cycles, stable
waits with no relaunch, an exact locally re-opened Scout quotation, exact
cleanup/cancellation after an interrupted Researcher, and a confidential Data Lab
candidate with no research runtime. Release promotion requires the maximum event-horizon
crash campaign and exact-source supported-host evidence recorded in the versioned audit.

`schemas/work-semantic-acceptance-v1.schema.json` and
`deploy/work-controller/semantic-acceptance.mjs` close the semantic gap without pretending
that another model check is truth. Scout, Researcher, and Data Lab reserve private artifact budget
for one local human attestation. The operator must bind the exact waiting checkpoint,
immutable acceptance-criteria digest, artifact manifest, and deterministic citation/replay
evidence. Pixel atomically stores one owner-private, single-link record, moves the same
candidate through `verified` to `completed`, and records every criterion passing without
changing its worker, artifact, workspace, safety, or external-effect lineage. The prior
checkpoint permanently retains the deterministic proof; the new digest identifies only
the explicit semantic review. Wrong hashes, linked/tampered records, insufficient reserve,
and concurrent acceptance races fail closed or converge idempotently. The attestation is
honest completion evidence, so that one authority flag is true; execution, lease, replay,
scope, publication, deployment, policy, and external-effect authority remain false. The
command-line review path is wired. The local control page also has a separately disabled,
process-token-gated, read-only semantic review pane. The ordinary heartbeat remains
content-free. Only an owner who enables `deepWorkSemanticReviews` in the private control
policy and opens the exact `./pixel ui` launch URL can load the current candidate's
objective, acceptance criteria, report, bounded relative evidence references, limitations,
and an honest explanation of what the deterministic verifier did and did not prove. Each
load reopens and rehashes the complete retained candidate before projecting it; malformed,
linked, substituted, stale, oversized, or non-UTF-8 material fails closed. Browser output
is capped at 1 MiB, uses no host-absolute paths or credentials, is rendered as text rather
than markup, and is not retained in the content-free status file. The pane has no accept
action: exact semantic acceptance remains a fresh trusted-terminal operation, so review
cannot itself complete, publish, deploy, replay, expand, or externally affect the work.

`./pixel work-accept review --config PRIVATE_FILE` emits only the exact candidate,
criteria, artifact, deterministic-verifier, and review hashes. After reviewing the local
retained artifacts, the operator may run `./pixel work-accept accept --config PRIVATE_FILE
--confirm-review-sha256 EXACT_HASH`. A stale or substituted review hash is rejected. The
review and accept paths re-read the owner-private retained file set, bind every report,
verifier, evidence, manifest, recipe, and replay-inventory digest, and recheck every Data
Lab derived artifact against its manifest. Changed, linked, missing, or extra files block
acceptance. The accept command records semantic completion evidence only; it cannot execute, replay,
publish, deploy, widen scope, change policy, or create an external effect.

The unit's `ExecStopPost` invokes `deploy/work-controller/goal-cleanup-cli.mjs` under the
same per-goal lock after the main controller has exited. The command is cleanup-only: it
returns no-op unless durable custody shows one consumed `running` Builder, Scout,
Researcher, or Data Lab attempt, or its retryable `cleanup-failed` successor. When
needed, it reconstructs that exact claim, proves its containers/network/volume were
removed, appends a failed child checkpoint, and refuses repeat cleanup. The next normal
cycle propagates that failure to the parent. A regression test covers cleanup, replay
refusal, content-free receipts, and parent reconciliation.

Cancellation also uses a recommended two-step terminal journey. `./pixel work-cancel
review --config PRIVATE_FILE` changes nothing and reports whether the current exact goal
checkpoint is safely cancellable. A `ready` or `paused` goal with no active child can end
directly. An active child with exact run custody but no lease consumption can be reviewed
for pre-launch cancellation: apply makes revocation and worker consumption race for the
same immutable slot, and only a revocation winner may produce an iteration-zero, zero-use
cancelled child and terminal parent. A consumed or otherwise started child remains blocked
until its exact supported child has terminal failed cleanup evidence; the command never
claims a state flag can stop a worker. An active child without exact custody also remains
blocked rather than inventing revocation proof. If review returns a confirmation, `./pixel
work-cancel apply --config PRIVATE_FILE
--confirm-review-sha256 EXACT_HASH` re-reads the private source and binds that cancellation
to the same parent and child checkpoints. It then publishes the terminal heartbeat.
Concurrent repeats converge, while a stale review, live child, substituted lease, or
changed ledger fails closed. The review hash includes the exact plan and lease. If an
expiry refresh interleaves after an older lease is revoked, the durable replacement custody
remains recoverable but the stale review cannot cancel it; the operator must review the new
lease before cancellation can proceed. The earlier exact-goal-hash form remains available as a
candidate compatibility path only for inactive or cleanup-proven cancellation; pre-launch
revocation requires the checkpoint-bound review/apply journey.

The release suite also runs the documented terminal control journey through fresh
`./pixel` processes rather than importing command functions. It pauses an inactive goal,
rejects an incorrect resume hash, races eight exact resume processes to one durable
transition, races eight later pause processes to one new paused checkpoint, rejects the
now-stale first review, and then races eight exact cancellation processes to one terminal
checkpoint. The expected chain is exactly `ready -> paused -> ready -> paused -> cancelled`
with zero jobs started. Every receipt and failure is scanned for private objective and
path canaries, and a freshly read heartbeat must agree with the terminal ledger.

`schemas/work-goal-run-bundle-v1.schema.json` and
`deploy/work-controller/goal-run-bundles.mjs` provide that restart boundary. Before child
admission, Pixel stores the exact compiled plan, exact expiring one-use lease, immutable
input-snapshot digest, goal/job hashes, and explicit authority facts in an owner-private,
single-link, append-only hash chain. This is capability custody, not a harmless status
record: it contains the exact lease. The bundle adds no authority beyond that embedded
lease and grants no replay, scope expansion, external effect, or completion authority.
A fresh controller can resolve the chain and complete the already recorded child. The
production Builder and candidate resolvers now replace an expired, unclaimed first lease
just in time before admission, even when the reviewed goal is days old. They first re-hash
every content-addressed input object, recompile against the same private policy, and require
the new plan and lease to be byte-equivalent after removing only their identifiers and
times. Policy, model, runner, tool, input, budget, criterion, or authority drift fails
closed. A consumed or admitted lease can never be refreshed. Continuations must bind the independently verified prior
checkpoint, exact consumption record, cumulative usage, unchanged plan, and original
input snapshot. Recovery rejects gaps, hard links, tampering, self-consistent input or
budget expansion, and any job/plan/lease mismatch. A durable `admission` append now races
pre-admission refresh for the same next sequence, so exactly one can win; refresh is
permanently refused after admission, and a child ledger without that marker fails closed.
If an admitted lease expires before the child ledger is created, Pixel can now use the same
reviewed pre-launch revocation path to end it without silently replacing authority. If
consumption already won, it waits for supervised cleanup instead. Multi-day supported-host
qualification is optional appliance evidence; the event-horizon supported-host matrix is the
release gate.

Goal and child ledgers use staged, atomic publication: even under competing controllers,
the permanent path becomes visible only after every private directory and the validated
initial record are complete. A losing initializer cannot expose or adopt a partial ledger.
Deterministic pressure qualification drives the maximum 64 dependency-ordered milestones
through 256 synthetic controller crashes, one immediately after each durable child-state
transition, and reconstructs both chains from disk after every restart. It also races 24
controllers in repeated waves against one single-use execution claim per child. The exact
expected result is 64 jobs started, 64 independently evidenced completions, monotonic exact
aggregate usage, no repeated milestone, and no duplicate child launch.
The separate credential-free event-horizon probe closes the in-process-only gap. It launches
a new operating-system process for every bounded controller cycle, deliberately sends that
process an abrupt kill immediately after each durable `running`, `verifying`, `verified`,
and `completed` child append, and starts another process from only the retained private
records. The goal is a repeated-diamond graph, so independent branches must settle before
each convergence milestone can become eligible. The minimum cross-platform gate uses four
milestones, 25 fresh processes, and 16 forced exits; each supported-host lane uses eight
milestones, 49 processes, and 32 forced exits. The promotion gate uses the maximum 64
milestones, 84 dependency links, 21 branch points, 21 convergence points, 42 dependency
levels, 385 fresh processes, and 256 forced exits. Exact completion, checkpoint cardinality,
usage, graph metrics, and zero replay/duplicate credit are schema-bound. The worker receives
a sterile environment and the evidence records zero credentials, provider calls, network
requests, external effects, and production deployments.

Each supported-host lane also qualifies the actual systemd supervisor rather than
inferring it from rendered unit text. The lane installs the exact root-held candidate's
service, event path, and watchdog timer inactive for a deliberately paused goal with no
execution or lease authority; activates the supervisors explicitly; observes a successful
initial reconciliation; stops only the watchdog while leaving the event path active;
records an exact-confirmed terminal cancellation; proves that checkpoint event caused a
distinct successful service invocation; and removes all units
while retaining and revalidating private terminal state. The watchdog remains a liveness
fallback and does not define progress. This proof deliberately executes no worker or
model, so useful OMP/model task performance remains a separate capability gate.

This is an event horizon, not a time gate. A goal advances immediately after durable child,
verification, authority, cancellation, dependency, and recovery events. A short measured
restart gap proves that a fresh process boundary actually occurred; it is not credited as
work. Bounded deadlines and watchdog timers exist only to detect a missing event, expired
authority, stalled work, or required reconciliation. Promotion therefore depends on the
maximum exact-source event campaign rather than an arbitrary number of elapsed days.

The separate multi-day qualification controller remains available for optional appliance
endurance testing. It creates one private 24-milestone goal with delayed lease refresh,
runs a networkless hardened systemd oneshot on an exact UTC-hour calendar, verifies fresh
systemd invocation and Linux boot identities, and can require a controlled reboot across
49 append-only records. Its service lifecycle, tamper checks, and evidence finalizer remain
useful when a deployment owner specifically wants calendar/boot coverage. It is not a
promotion gate, and neither elapsed time nor timer delivery is treated as useful progress.

The maintainer-only sequence is intentionally explicit:

```bash
sudo -u pixel-work node scripts/deep-work-multi-day-soak.mjs initialize \
  --root /var/lib/pixel-deep-work/campaign \
  --source-commit "$(git rev-parse HEAD)" \
  --source-tree "$(git rev-parse 'HEAD^{tree}')"
sudo -u pixel-work node deploy/work-controller/deep-work-soak-service-cli.mjs render \
  --config /var/lib/pixel-deep-work/campaign/campaign.json \
  --output /var/lib/pixel-deep-work/soak-service \
  --install-root /opt/pixel
sudo -u pixel-work node deploy/work-controller/deep-work-soak-service-cli.mjs inspect \
  --bundle /var/lib/pixel-deep-work/soak-service
sudo node deploy/work-controller/deep-work-soak-service-cli.mjs install \
  --bundle /var/lib/pixel-deep-work/soak-service \
  --confirm-manifest-sha256 EXACT_INSPECTED_HASH
sudo node deploy/work-controller/deep-work-soak-service-cli.mjs activate \
  --bundle /var/lib/pixel-deep-work/soak-service \
  --confirm-manifest-sha256 EXACT_INSPECTED_HASH
sudo -u pixel-work node scripts/deep-work-multi-day-soak.mjs status \
  --config /var/lib/pixel-deep-work/campaign/campaign.json
# Optional appliance soak: reboot once between completed hourly cycles under its runbook.
sudo -u pixel-work node scripts/deep-work-multi-day-soak.mjs finalize \
  --config /var/lib/pixel-deep-work/campaign/campaign.json \
  --output /var/lib/pixel-deep-work/multi-day-evidence.json
sudo node deploy/work-controller/deep-work-soak-service-cli.mjs remove \
  --bundle /var/lib/pixel-deep-work/soak-service \
  --confirm-manifest-sha256 EXACT_INSPECTED_HASH
```

This optional soak sequence is not required for promotion. Run the service commands from
the exact installed source. The campaign and rendered bundle
must be owned by the configured unprivileged service identity; root only installs or
activates exact reviewed unit bytes. The installed source should be root-owned and not
group- or world-writable; initialization admits that exact root-owned Git checkout without
granting a general safe-directory exception. Never reboot a client or production host for this
gate: use a dedicated disposable qualification machine and retain the private evidence
outside Git.
An additional production-custody qualification recreates the controller on every cycle
while advancing a dependency chain across repeated virtual days. Each delayed child gets
exactly one equivalent pre-admission refresh, one admission, one worker launch, and one
independently verified completion; input or policy drift and sixteen-way refresh races
fail closed or converge on one immutable append.

The multi-goal host-admission foundation adds an immutable private fleet schedule and a
separate durable round-robin checkpoint chain. Every registration binds the exact goal,
ordered child-job set, private controller configuration, and the maximum CPU, memory,
disk, worker time, verifier time, five-minute cleanup reserve, and total wall-time envelope
of any one child. Each registration also binds its goal's complete retained-artifact
ceiling, and the fleet requires their sum to fit one cumulative host retention ceiling.
The initial supported discipline permits
exactly one active reconciliation turn. A selection does not grant execution or a lease;
the selected goal must still pass its ordinary custody and worker boundaries. Settlement
advances the cursor past that goal, so every currently eligible registration receives one
turn before a continuously eligible goal repeats. If the fleet controller crashes, the
active selection remains the only recoverable turn and blocks every other goal until exact
cleanup/reconciliation evidence is settled. Competing selectors and settlers converge on
one append, while altered registrations, understated envelopes, parent-budget expansion,
tampering, gaps, and hard links fail closed.

The first execution bridge now loads one owner-private fleet wiring file, revalidates every
exact per-goal controller registration, derives eligibility from each authoritative goal
ledger, and invokes only the selected existing one-cycle controller. The active fleet turn
binds the goal checkpoint present at selection. If the goal advanced but the process died
before fleet settlement, the next pass settles that prior turn from the new authoritative
head instead of running it twice. A failed cycle remains crash-held; a substituted or stale
content-free cycle receipt cannot release it. Deterministic tests cover fair rotation,
effect-to-settlement crash recovery, failure custody, and receipt substitution.

A fleet-specific hardened oneshot/timer renderer now puts both the bounded cycle and its
cleanup-only stop pass behind the same fleet-wide `flock`. Stop cleanup identifies only
the crash-held goal from the private fleet ledger, invokes that goal's existing exact
cleanup boundary, and releases the turn only after a content-free cleanup receipt matches
the authoritative goal head. A cleanup error keeps the turn held. The generated service
also refuses to start while any registered legacy per-goal service or timer is installed,
preventing the two scheduling disciplines from silently running together.

Fleet cadence now has the full review/apply lifecycle as well. Rendering atomically emits
an inert owner-private service/timer bundle and an exact hash manifest. Inspection is
side-effect free; root installation remains inactive; activation requires the exact
manifest confirmation, unchanged live fleet/controller contracts, reconstructable fleet
and goal ledgers, and absence of every registered legacy unit in the real systemd
directory. Removal disables the timer, runs stop-cleanup through the service, verifies both
units inactive, and then removes only exact installed bytes. Publication or reload failure
restores a complete inactive pair and never restarts work.

Every fleet command and lifecycle check now also requires the current record from an
owner-private hash-chained host-evidence ledger. The fleet configuration binds the exact
read-only probe policy and the ledger's genesis hash; later observations bind the prior
record, the same probe, and the immutable fleet without changing the service configuration.
The evidence subtracts an explicit operating-system reserve and every
declared shared-service CPU, memory, and disk ceiling from observed physical capacity.
Fleet CPU and memory must fit the remainder; disposable turn disk plus the cumulative
retained-artifact ceiling must fit simultaneously. Evidence is valid for at most 24 hours,
so stale headroom cannot silently authorize a multi-day controller.

The Linux evidence generator is read-only against services: it measures logical CPU,
physical memory, and free bytes on the declared disposable filesystem; hashes a local host
identity; and asks systemd for each exact shared unit's load state, active state, CPU quota,
and memory ceiling. A service must be loaded and active with a finite whole-core CPU limit
and finite memory limit no larger than the reviewed reserve. Its separately reviewed disk
limit digest is folded into the live limit hash. Publication is atomic and append-only.
A still-fresh head is reused without probing; once half its reviewed lifetime has elapsed,
the supervised pass probes and appends a new record before selecting a goal. Gaps,
rollback, genesis substitution, predecessor drift, host-identity changes, hard links,
concurrent duplicate appends, and expired heads fail closed. The receipt contains no
machine identity or path and grants no authority. A crash during atomic publication can
leave only a controller-reserved temporary link; refresh removes it after a five-minute
anti-race delay when it is either an unlinked orphan or the sole extra link to one exact
record. Any other shape remains fail-closed.

The fleet surface is available through Pixel's top-level CLI so an operator does not need
to invoke internal modules. It deliberately remains an advanced, private-contract workflow:
the current release does not infer objectives, generate goal graphs, choose resource limits,
or activate a service from conversational text. Given exact owner-private goal-controller,
fleet, host-probe, and fleet-controller JSON contracts, the sequence is:

```bash
# First create PRIVATE_STATE/host-evidence as an owner-only directory, then create genesis.
./pixel work-fleet-host-evidence create --config PRIVATE_PROBE.json \
  --output PRIVATE_STATE/host-evidence/0000000.json
# Put the receipt's evidenceSha256 in the v2 fleet controller as
# hostEvidenceGenesisSha256; bind the probe itself with hostProbeSha256.
./pixel work-fleet init --config PRIVATE_FLEET_CONTROLLER.json
./pixel work-fleet status --config PRIVATE_FLEET_CONTROLLER.json
./pixel work-fleet cycle --config PRIVATE_FLEET_CONTROLLER.json
./pixel work-fleet-cleanup --config PRIVATE_FLEET_CONTROLLER.json
./pixel work-fleet-service render --config PRIVATE_FLEET_CONTROLLER.json \
  --output NEW_PRIVATE_BUNDLE --install-root /opt/pixel
./pixel work-fleet-service inspect --bundle NEW_PRIVATE_BUNDLE
sudo ./pixel work-fleet-service install --bundle NEW_PRIVATE_BUNDLE \
  --confirm-manifest-sha256 EXACT_INSPECTED_HASH
sudo ./pixel work-fleet-service activate --bundle NEW_PRIVATE_BUNDLE \
  --confirm-manifest-sha256 EXACT_INSPECTED_HASH
```

`./pixel work-fleet-host-evidence refresh --controller PRIVATE_FLEET_CONTROLLER.json`
is the explicit renewal/recovery control. The fleet-wide timer performs that same bounded
refresh under its one fleet lock before every scheduled cycle; manual `cycle` and
`cleanup` remain qualification and recovery controls.
Read-only `status` validates every immutable contract and durable chain, then reports only
fleet state, settled/started counts, crash-held state, fixed goal-state counts, and current,
renewal-due, not-yet-valid, or expired evidence timing. It intentionally remains available
for an expired but otherwise valid evidence chain, while `cycle` still refuses to run.
`cleanup` can settle only the exact crash-held turn after its goal's supervised cleanup
succeeds. Removing cadence is another exact-confirmed command:
`sudo ./pixel work-fleet-service remove --bundle NEW_PRIVATE_BUNDLE
--confirm-manifest-sha256 EXACT_INSPECTED_HASH`. Removal retains private goal and audit
state. A deterministic eight-day restart simulation now proves renewal, exact chain
recovery, unchanged controller bytes, and fair rotation. Real-process crash endurance is
now a required supported-host check. The maximum repeated-diamond event-horizon campaign
is the separate Deep Work promotion gate; the genuinely multi-day supervised controller,
hardened cadence, lifecycle, and evidence contract remain optional appliance evidence.

This ledger constrains registered worker turns; it does not claim that the already-running
local model, Docker daemon, operating system, or unrelated processes fit the machine.
Supported status still requires the fleet cadence to pass the supported-host matrix and
the maximum exact-source event-horizon recovery campaign rather than relying on
deterministic fixtures or a clock soak. The legacy per-goal timers
remain single-goal only and must not be presented as host-fair scheduling; rendered fleet
service startup fails while any corresponding legacy unit remains installed.

The extension foundation adds signed, digest-bound capability declarations and separate
expiring grants. Claim and execution-start records are atomic, durable, and one-use, so a
crash burns the grant instead of replaying it. The initial MCP profile targets the
2026-07-28 newline-delimited stdio protocol and permits exactly one local, credential-free,
networkless call through an exact discovered server identity and exact signed tool schemas.
The adapter receives an empty host environment inside a read-only, capability-free,
no-new-privileges container with bounded CPU, memory, processes, frames, output, and time.
Optional workspace tools receive only a size-bounded disposable `nosuid,nodev,noexec`
volume with an explicit removal command. Server requests, schema drift, input-required
turns, non-structured output, text/structured-output disagreement, classification
downgrade, replay, excess output, timeout, or unclean lifecycle fail closed. Raw MCP text
is not returned; only schema-validated structured output and content hashes cross the
adapter boundary. The admission CLI now separately inspects an untrusted declaration,
signs or verifies its canonical bytes under the `pixel-work-capability-pack` namespace,
atomically installs a verified private copy, and revalidates installed copies against the
current trust root. Installation records that the image was neither pulled, inspected, nor
executed and leaves every authority flag false. Exact-hash removal moves the declaration
into private recovery custody, writes independent custody proof before deletion, records a
no-residue tombstone, reports any interruption, and refuses same-version reinstall.

The next image-admission phase is also implemented and disabled. It uses only `/usr/bin/docker`
with an empty owner-private credential-free configuration and `--pull never`; inspects one
already-local canonical repository manifest digest or exact signed local Docker image ID;
separately verifies the signed local Docker image ID and exact Linux architecture; creates a read-only, capability-free, no-new-privileges,
networkless resource-bounded container but never starts it; streams the signed executable
as a bounded tar archive; and accepts only one exact regular file with the declared byte
count and SHA-256. Every create attempt, including an ambiguous failure or timeout, enters
force-remove plus inspect-absent cleanup. Unproven cleanup remains durable and blocks every
other pack mutation. Exact-review revocation removes Pixel's admission while retaining the
host-owned image. A schema-bound single-writer record prevents admission, revocation, and
pack removal races and is recoverable across intent, custody, and finalization crashes.
The admission begins `health: not-probed`, remains absent from the controller catalog, and
grants no tool, data, network, execution, external-effect, or completion authority. A
separate explicit `health-probe --confirm` operation uses the exact admitted image in a
sterile disposable container for MCP `server/discover` and `tools/list` only. It creates no
grant or execution tombstone, calls no tool, receives no client data, and cannot register or
enable the capability. Durable operation custody precedes launch; forced removal and exact
absence proof are mandatory; and chained content-free receipts recover exactly across
cleanup and publication crashes. Three consecutive failures quarantine the signed version.
`health-status` is read-only and `health-recover` can resume only the exact interrupted
cleanup or staged receipt. The opt-in `PIXEL_LIVE_DOCKER=1 node --test
tests/work-capability-image-live.mjs` gate builds a real networkless scratch image and proves
sign/install/admit/health/status/tool-call/revoke/remove plus exact test-owned image cleanup
on the supported Linux host. Both supported-host systemd lanes now require this opt-in gate
and reject success if Node reports the test skipped.

The supervised tool-execution phase is implemented and remains lease-gated rather than
ambient. An exact admitted version must have recent passing health; a separate expiring
grant must name the job, checkpoint, classification, tool, and resource envelope; and the
controller-owned watchdog decision must bind that exact input and effect class. Pixel burns
the grant and durable execution tombstone before adapter entry, performs one call, returns
the schema-validated structured result only to the live caller, and stores no argument or
result content in its terminal receipt. Cleanup first verifies the claim-bound container and
optional tmpfs-volume ownership, then removes and independently proves absence. A crash after
intent, claim, cleanup, staged receipt, or final receipt can only abort or finish cleanup and
publication; it can never run the tool again. Stale health, concurrent execution, a foreign
name collision, custody or receipt tampering, uncertain cleanup, schema drift, timeout, and
tool failure all fail closed. The real supported-host scratch fixture now proves one actual
networkless call in addition to admission and health. There is intentionally no
general-purpose runtime CLI.

The pure controller authorization phase is now implemented. It will not compile capability
authority from a profile name or elapsed clock tick. It requires the normal OMP plan and
single-use lease, that lease's exact consumption tombstone, and the exact `running`
checkpoint whose worker-session hash names the consumption. A private policy then allowlists
one signed pack version and its exact tools, effects, classifications, per-call ceilings,
session ceiling, grant lifetime, and watchdog limits. The resulting job authorization grants
no call. Each private worker request is independently hash-bound to it, validated against the
signed input schema, and evaluated against the complete trusted watchdog-event head. Only a
`continue` decision creates one expiring single-use grant; a budget, repeat, failure,
oscillation, or verified-progress stop creates none. Request age and expiry only invalidate
stale authority; they never schedule work.

Durable request custody and event settlement are now implemented internally as a disabled
single-flight queue. A request is atomically transferred from the pending spool into one
active owner-private directory; an append-only content-free chain records claim,
authorization, launch, runtime return, and settlement. Successful structured output is
staged only in job-scoped private custody before its content-free watchdog event and exact
response are committed. A crash after authorization may continue because execution has not
started. The durable `launching` record is the conservative no-replay horizon: recovery checks
and cleans runtime custody, uses already-staged output when present, and otherwise settles an
`uncertain-no-replay` failure without calling the tool. Crashes after output, event, response,
or settlement resume the exact bytes and never append a second event. A watchdog stop writes
no grant or event and permanently closes that job authorization. Operator projection,
bounded retention/cleanup policy, first-user acceptance, and exact-source live qualification
remain gates.

The trusted OMP registration bridge is conditionally routed into Scout, Builder, Data Lab,
and Researcher. The controller derives one fresh job-scoped catalog from the exact authorization
and signed pack. Its filename binds its canonical content, its aliases are deterministic
`pixel_cap_*` names that cannot collide with builtins or other packs, and its input/output
schemas are the exact signed raw schemas. The extension accepts only the one private catalog
queue, registers no ambient tools, and serializes every call so its expected event head can
advance only from a validated settled response. Response identity, authorization, checkpoint,
classification, lifetime, schema, and digest are revalidated locally. Any uncertainty after
request publication permanently closes the extension session. It has no network, credentials,
child-process, grant, external-effect, or completion authority. The primary OMP worker alone
receives three exact mounts: catalog read-only, requests writable, and responses read-only.
Exporters, replay workers, inventory stages, independent verifiers, unrelated jobs, and
unbound profiles receive none.

Production goal controllers can opt in with one `capabilityRuntime` block in the reviewed
environment. Every binding names one immutable child `jobId`, one exact pack ID/version/hash/
tree, a uniquely sorted tool set, per-lease sessions, grant lifetime, and per-call limits.
Preparation copies the capability controller policy into the private controller bundle and
hash-binds both policy and bindings in its manifest. The trust root and installed declaration
are revalidated at execution, passing health must be recent, and the Docker client configuration
must remain an empty owner-only directory. Pixel does not derive authority from a profile name.
It waits until the ordinary lease has been atomically consumed and the matching `running`
checkpoint exists, then derives a deterministic exact authorization and catalog and publishes
content-free recovery custody before worker setup. A crash therefore recovers cleanup from the
original policy/pack/catalog bytes without executing again; a job without a binding remains
capability-free. Service inspection, installation, and activation additionally fail closed
unless every selected pack is still signature-valid, hash-exact, image-admitted, recently
healthy, and inside the controller-policy lifetime. Their inspection receipt reports only
ready/disabled state and job, pack, and tool counts. Guided selection, retention controls,
and live enabled OMP qualification remain closed gates.

The private-knowledge foundation admits only an exact, owner-approved local text source
into one owner/client/classification partition. Titles, chunks, and per-source keys are
encrypted at rest; a client-specific keyed lexical index contains no plaintext terms.
Retrieval is expiring, single-use, checkpoint-bound, classification-preserving, minimum-
relevance-gated, cited, and invalidated by any vault-head change. No match returns no
context. Returned text is explicitly untrusted and carries no authority. Exact deletion,
pre-approved retention purge, encrypted backup restore, corruption detection, and crash
recovery are covered. Exact private-credential loading, atomic full-vault key rotation,
interrupted-transaction recovery, and cross-key-generation backup/tombstone reconciliation
are also implemented and fail closed. Reviewed controllers can now bind exact local-vault
queries to one running local-only checkpoint; retrieved excerpts are quoted as untrusted,
attempt-only prompt data and never enter status or receipts. Goal services project the vault
key through systemd credentials, and the encrypted private-state restore hook now reconciles
the active deletion ledger and rotates historical wrapping before activation. A reviewed,
resumable first-time setup creates the external credential and atomically publishes a deeply
audited empty vault without displaying key material. A plain-language
`./pixel work-knowledge-guide` now wraps setup, ingestion, query, deletion, offline rotation,
and historical reconciliation in one review and one short exact confirmation phrase. The
browser remains content-free by design instead of gaining private-data or key authority.
Production service-account credential installation and first-user acceptance of the guide
remain release gates. A trusted-terminal `./pixel work-knowledge` foundation already provides fresh hash-bound
review/apply flows for ingestion, checkpoint-bound query output, exact deletion, offline
rotation, and offline historical reconciliation; the guide calls these same fail-closed
operations rather than implementing a weaker mutation path. See
`KNOWLEDGE-VAULT.md`.

The operator view uses one atomically replaced, owner-private, strict content-free
snapshot. Every successful production goal cycle now reconstructs admitted sessions from
the immutable goal graph, run custody, and checkpoint ledgers and publishes a fresh
controller heartbeat. The snapshot also marks at most one current milestone and reports
each immutable goal ceiling, settled-plus-active-observed use, and exact remaining jobs,
runtime, model requests, tokens, network, artifact bytes, and failures. The producer and
local-control server independently require `remaining = limit - used`, bind active usage
to that one current checkpoint ledger, and refuse a total behind settled goal usage. Raw
job and event identities are replaced with per-snapshot keyed
display handles before writing it; the local-control server validates the complete shape,
time order, progress arithmetic, artifact totals, verification state, service order, and
fixed privacy/authority declarations, then removes those display handles as well. The
browser receives only mode, fixed state codes, timestamps, bounded counters, artifact
categories, service health, verification state, and fixed activity codes. Any unknown
field, impossible total, future time, corrupt or linked file, stale snapshot, or status-
embedded authority flag fails closed or is shown as offline. The status endpoint is
deliberately read-only. Three separate disabled-by-default local-control actions can pause
future scheduling, resume the exact paused checkpoint, or safely cancel eligible settled
custody after an exact confirmation. They accept no caller path, checkpoint, reason, or
free-form parameter, bind the private controller configuration revision, execute from an
owner-private single-use byte snapshot, and remove that snapshot after success, failure,
or restart recovery. Resume and cancellation rerun their existing read-only review at
execution and require the same private review hash and transition mode. A step that already
started may still finish; cancellation cannot stop or conceal it without terminal
supervised cleanup evidence. Boundary expansion, egress approval, private evidence, and
artifacts remain unavailable to the browser. A missed or failed cycle naturally ages offline after two minutes;
snapshot publication cannot schedule, resume, retry, approve, or complete work. Stop-
cleanup also republishes the resulting failed or recovery-attention checkpoint. First-time
usability and supported-host heartbeat qualification remain release gates.
The release suite drives all three browser-facing lifecycle actions through the real
loopback HTTP service and real pause/resume/cancel CLI subprocesses against a durable goal.
It rejects cross-site and caller-supplied path/checkpoint attempts, rejects incorrect
confirmations without changing the ledger, proves the exact
`ready -> paused -> ready -> cancelled` chain, refuses replay, refreshes the content-free
heartbeat, and scans every public response for private goal, path, and review-hash canaries.

## Loop and verification

The durable goal controller, rather than any one model session, owns the loop. A job may
run for hours or days as a sequence of expiring leases and immutable checkpoints; no
worker receives timeless authority. Restarts reconstruct state only from the validated
event chain, exact artifacts, remaining budgets, and last independently verified progress.
The controller never treats elapsed time, token use, repeated edits, or a worker's own
claim as progress.

Within each bounded OMP turn, the RPC observation guard prevents a stuck worker from
spending the remainder of that turn repeating an unchanged outcome. Across turns, only
independently verified checkpoint evidence advances the durable goal. These are separate
controls: the inner guard detects local tool-loop symptoms, while the outer controller
decides whether a milestone made durable progress and may receive another expiring lease.

Longer objectives are represented as exact child-job graphs rather than a timeless model
session. Only a dependency-ready child may be recorded as active. A crash after that
record causes recovery of that child; it does not dispatch a duplicate. Aggregate goal
budgets must cover the sum of every child ceiling at admission, while actual usage is
added only from terminal child checkpoints. Static graph changes, criteria changes,
classification changes, replacement jobs, and new providers require a new reviewed goal
instead of being smuggled into a continuation.

Its state machine is:

```text
authorized -> running -> verifying -> completed
                    |          |
                    |          +-> running (measurable progress and budget remain)
                    +-> waiting-authority
                    +-> failed / cancelled / budget-exhausted / no-progress
```

Every checkpoint binds the prior checkpoint, plan, inputs, objective, immutable acceptance
criteria, workspace snapshot, worker session, artifact manifest, usage, progress, and a
bounded failure fingerprint. It contains no authority token or raw model transcript.
Detected safety incidents are valid evidence, but require a failed or cancelled state.

The verifier receives the immutable criteria from the broker, not from the worker. It runs
in a separate environment, records exact evidence, and rejects missing, selected-away,
rewritten, spoofed, or worker-only tests. A passing result requires every criterion to pass,
independent verification, no observed safety violation, no forbidden external action, and
at least one declared artifact.

## Required adversarial coverage

Before a write-capable profile is eligible for Supported status, qualification must cover:

- symlink, hardlink, traversal, nested-repository, mount, device, `/proc`, descriptor, and
  time-of-check/time-of-use escapes;
- `.env`, `.omp`, AGENTS/instruction, skill, plugin, MCP, provider, model, hook, Git, and
  package configuration injection;
- credential canaries, host sockets, SSH agent, browser state, clipboard, metadata service,
  DNS, IPv4/IPv6, proxy, redirect, and Unix-socket exfiltration;
- dependency confusion, malicious archives, install scripts, cache poisoning, digest drift,
  SBOM/provenance mismatch, and compromised executor/runner artifacts;
- fork/process bombs, runaway subagents, background orphans, CPU/memory/disk exhaustion,
  oversized RPC frames, output/artifact bombs, timeouts, and cancellation races;
- duplicate jobs, lease replay, stale checkpoints, crash/restart, partial artifact writes,
  partial ledger initialization, competing controllers, verifier tampering, fake success,
  changed criteria, repeated failures, and false progress;
- research prompt injection, SSRF, rebinding, citation forgery, source substitution, private
  query leakage, cross-client history/cache leakage, and malicious file parsing.

## Promotion gates

Deep Work remains unavailable by default until its exact release candidate has:

1. deterministic schema, semantic, broker, runner, RPC, lifecycle, and hostile-input tests;
2. two consecutive full release gates on the exact functional source;
3. two consecutive real-system matrices on Ubuntu 24.04 LTS and Debian 12;
4. zero unexplained code-scanning, dependency, secret-scanning, escape, or exfiltration findings;
5. clean install, enable, disable, upgrade, rollback, crash recovery, and removal evidence;
6. a capability-retention benchmark showing standard Builder jobs run without mid-job
   prompts, exercise the complete eleven-class public synthetic corpus, and retain at
   least 90% of uncontained OMP baseline success;
7. first-time non-implementer coding and data-job usability acceptance;
8. independent human security review and production signing-key custody.

Automated qualification uses no external credential, provider call, spend, client data, or
production deployment. A later live provider gate must be separately authorized and fixed.
No gate may be weakened, skipped, or relabeled merely to obtain green status.

The implemented v1 retention gate compares the same pinned OMP binary, synthetic workspace,
objective, local model fixture, Builder runtime, and proof selectors in two lanes: direct OMP
inside an independently hardened container and the full Pixel broker/proxy/Builder/verifier
lifecycle. Its eleven classes cover workspace read, search, discovery, write, targeted edit,
shell execution, debugging, language intelligence, local evaluation, bounded subagents, and
goal coordination. A development run reached 11/11 in both lanes (100% retention) with no
credentials, provider calls, client data, external network, or external effects. That result
is engineering evidence only; promotion still requires the same green result, emitted through
`tests/work-capability-retention-live.mjs`, on the exact clean candidate commit/tree and pinned
image digest.
