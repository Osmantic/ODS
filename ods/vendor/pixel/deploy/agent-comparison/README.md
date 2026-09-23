# Pixel outcome comparison laboratory

This directory contains mechanics for controlled harness comparisons. It is not
itself product-capability evidence.

## Admissible lanes

Formal claims use the private portal-outcome task, run, comparison, and campaign
contracts. A Pixel result is admissible only when it traverses the complete Pixel
product boundary. A direct `openclaw agent` invocation is a raw harness probe and
cannot establish Pixel capability, safety, usability, or Codex parity.

The comparison sequence is:

1. `same-model-harness`: DSV4 Flash 0731 through complete Pixel and the pinned
   Codex reference harness with matched tools, authority, inputs, inference, and
   fresh runtime state.
2. `frontier-ceiling`: the frozen DSV4 Pixel candidate against SOL 5.6 Extra High
   Codex. This lane records both matched-budget efficiency and maximum-quality
   results; extra Pixel deliberation is allowed but must be measured.

The historical `serial-pair.py` script is retained only to reproduce exploratory
raw-harness measurements. Its output is non-admissible because it bypasses Pixel
product layers, uses ambient executable discovery, runs in fixed order, and does
not provide fresh-runtime or immutable campaign evidence.

## Full Pixel arm

`pixel-arm.mjs` is the same-model Builder adapter. It does not call an agent
binary directly. It converts an admitted neutral task into the production Work
job contract, requires the private Work policy to match the exact DSV4 model,
inference, tools, verifier image, executable allowlist, and resource envelope,
then traverses Work compilation, disposable source preparation, single-use
lease claim, Docker Builder execution, model-proxy accounting, independent
verification, retained-artifact hashing, and cleanup. A non-terminal result is
not collapsed into a one-pass failure: the adapter follows the production
verifier-governed Builder controller through reduced continuation leases until
the verifier accepts the work, measured no-progress or failure limits stop it,
or the admitted resource budget is exhausted. Each continuation receives only
the retained patch, checkpoint, prior claim, and remaining authority needed for
the next iteration; it cannot enlarge scope or acquire a second authorization.
The adapter revalidates the terminal verification receipt against the immutable
compiled plan, rejects narrated or forged completion, and reports cumulative
model, token, tool, network, and artifact use for the complete path rather than
worker time or the final iteration alone.

`portal_outcome_pixel_orchestrate.py` composes that result into the same private
run schema used by the Codex arm. A Pixel run is rejected unless its runtime
receipt binds fresh DSV4 Flash 0731, the exact model artifact and backend image,
the Work policy and harness digests, real tools, no Qwen product substitution,
and no cross-run state. Independent Work evidence is revalidated again before
it can satisfy a journey assertion.

`portal_outcome_materialize_battery.py` converts the curated coding battery and
the explicitly labeled user-trial rehearsals
into deterministic ustar source snapshots and exact request, environment, tool,
verifier, model, inference, and task bindings. Every writable task carries a
fresh networkless semantic verifier. Canary inputs such as `.env` and `secrets/`
are immutable, and exact-response tasks are scored from a content-free final
reply digest rather than agent narration. Multiline controller-selected Python
checks are transported as bounded base64 argv chunks and reconstructed inside a
direct `python3` invocation. This keeps the Work boundary's control-byte and
no-shell rules intact while permitting realistic independent semantic checks.

Materialization has two explicit evaluation regimes. `matched-budget` is the
default and the only regime that may create the frozen same-model baseline. It
gives both arms the same conservative wall-time, iteration, model-request,
token, tool, artifact, network, process, failure, and concurrency ceilings.
`maximum-quality` preserves the exact same task, source, tools, services,
model/inference binding, scope, external-write count, and dangerous-authority
denials while raising only recorded resource ceilings. That larger envelope is
intended to let Pixel spend more time on decomposition, bounded specialist work,
independent critique, verification, revision, and acceptance-based stopping; it
does not permit host access, ambient credentials, unbounded networking, external
effects, merge, deployment, or policy mutation. The materializer revalidates
those invariants per task, so a resource experiment cannot quietly become an
authority experiment.

The rehearsal tasks use `agent_comparison_fixture_tool.py`, copied with its exact
fixture into an immutable `.pixel-scenario/` prefix. It provides typed,
stateful calls and content-free receipts for stale-state refresh, cited frozen
research, unknown-outcome reconciliation, scoped fleet inventory, sanitized
spillover, inline approval, and event-driven recovery. These cases compare
harness reasoning and safety mechanics without live credentials or effects.
They are always recorded as `deterministic-non-promotional-rehearsal`; their
results cannot satisfy a live-provider, product-path, acceptance, or promotion
gate. The corresponding exact-system journeys remain separately required.

## DSV4 qualification image

`Dockerfile.dsv4-vllm` derives from the exact DSV4-capable vLLM image used on
Tower2, verifies the Apache-2.0 workspace patch by SHA-256, fixes the resolved
quality-relevant runtime environment in the image, and replaces the configurable
launcher with the exact vLLM executable entrypoint. The model is never copied into
the image; it is an owner-custody, read-only `/model` bind mount verified against
its private directory manifest before launch.

Build context requirements:

- `Dockerfile.dsv4-vllm` copied as `Dockerfile`.
- `dsv4-serve.sh` copied from this directory. Its SHA-256 is pinned by the
  Dockerfile and it fixes the known-good DSV4 server mechanics while leaving
  controller-bound model identity, context, resource, and parser arguments in
  the separately hashed launch vector.
- `workspace.py` with SHA-256
  `50b5a02e83a419e6da309efb2f78580b72e5b04c57babf6d34854ef3d3fb6dbe`.

Do not use an ordinary `docker build` as reproducibility evidence. Qualify the
exact owner-reviewed patch with the pinned BuildKit path. The qualifier stages
only the three declared inputs, builds twice without cache, rewrites timestamps,
compares the loadable image archives byte-for-byte, and reports the one shared
immutable image ID without starting a container or mounting the model:

```sh
bash scripts/qualify-dsv4-image.sh \
  --workspace-patch /absolute/owner-reviewed/workspace.py
```

## Exact DSV4 capability qualification

The comparison policy cannot become prepared from a model name, a running
server, or a legacy Qwen receipt. `model-qualification-docker.mjs` is the
credential-free DSV4-capable path. Its owner-private configuration binds the
reviewed model-backend configuration, fixed synthetic qualification input, the
reproducibly built Work runner image ID, and a new empty private evidence
directory.

Run `review` first. It measures the model directory through the normal backend
preparer and returns one content-free confirmation hash. A later explicitly
confirmed `run` starts only that fresh exact backend, attaches one hardened
qualification container to its internal Docker network, tests the fixed 13-case
suite over a loopback-only bridge, writes one private receipt, and removes both
containers and the network. It refuses a pre-existing backend rather than
adopting or stopping it. The qualification bridge forwards only
credential-free `/v1/chat/completions` requests to the fixed
`pixel-local-model` alias and has no external route.

```sh
node deploy/work-controller/model-qualification-docker.mjs review \
  --config /etc/pixel-work/model-qualification-docker.json
node deploy/work-controller/model-qualification-docker.mjs run \
  --config /etc/pixel-work/model-qualification-docker.json \
  --confirm-qualification-operation-sha256 REVIEWED_SHA256
```

The backend's physical context window and Work's per-request context grant are
separate. DSV4 may remain configured for its full physical window, while
`maxRequestContextTokens` must fit inside the empirically measured receipt. This
prevents an advertised capacity from becoming untested agent authority and lets
stronger qualification raise the usable envelope without rebuilding the model.
The fixed sustained-output case proves an 8,192-token response envelope; no
short-output receipt can silently prepare the comparison policy.

## Full Pixel system adapter

`pixel-system-cli.mjs` and `portal_outcome_pixel_livesystem.py` are the concrete
bridge from the common outcome task to Pixel's real Work Broker, disposable OMP
Builder or public-only Researcher, model proxy, exact backend lifecycle, and independent verifier. The
owner-private `pixel-system.example.json` configuration names three reviewed
templates and a disposable runtime root. Every run materializes unique private
state, backend identities, workspaces, object storage, and artifacts; it refuses
model, inference, launch-vector, resource, tool, or verifier drift, then removes
the exact backend and private run state during teardown.

After qualification and the separate model-policy enablement review, do not
hand-edit `policyTemplatePath`. `pixel-system-cli.mjs policy-review` accepts the
current system configuration, the newly enabled policy, and the intended new
configuration destination. It verifies current exact DSV4 qualification for
every enabled comparison profile and returns one operation hash bound to the
source configuration, both policy identities, and the destination. A matching
`policy-apply` writes a new owner-private configuration that changes only the
policy pointer. It neither replaces the current configuration nor starts a
model, service, task, tool, or external effect; choosing that new configuration
for a later run remains a distinct operator action.

The enclosing paired-run configuration has the same transition discipline.
`portal_outcome_pair_system.py review` requires the exact successful Pixel
system policy-binding receipt, binds the current pair bytes, the new Pixel
system bytes, a new pair-config destination, and a distinct unused preflight
destination into one content-free operation hash. `apply` recomputes those
bindings and writes one new owner-private pair configuration changing only
`pixelSystemConfigPath` and `preflightPath`. It refuses overwrite, linked or
non-private inputs, receipt/configuration mismatch, reused preflight evidence,
and post-review substitution. It starts no model or task and grants no runtime
authority.

```sh
python3 scripts/portal_outcome_pair_system.py review \
  --config PRIVATE_PAIR.json \
  --pixel-system NEW_POLICY_BOUND_PIXEL_SYSTEM.json \
  --pixel-binding-receipt PIXEL_SYSTEM_POLICY_APPLY_RECEIPT.json \
  --candidate NEW_PRIVATE_PAIR.json \
  --preflight NEW_UNUSED_PREFLIGHT.json \
  --output PAIR_BINDING_REVIEW.json
python3 scripts/portal_outcome_pair_system.py apply \
  --config PRIVATE_PAIR.json \
  --pixel-system NEW_POLICY_BOUND_PIXEL_SYSTEM.json \
  --pixel-binding-receipt PIXEL_SYSTEM_POLICY_APPLY_RECEIPT.json \
  --candidate NEW_PRIVATE_PAIR.json \
  --preflight NEW_UNUSED_PREFLIGHT.json \
  --confirm-operation-sha256 REVIEWED_SHA256 \
  --output PAIR_BINDING_APPLY_RECEIPT.json
```

Formal DSV4 arms require the same two GPUs used by the normal production model, so a
campaign must not be started beside that service. `model-campaign-maintenance.mjs`
provides the exact batch boundary. Its private configuration binds the clean candidate
commit and archive, materialization, pair config, ready preflight, output destination,
partition, cold/warm condition, timeout, maximum pair count, production container, and
current exclusive GPU state. `review` is content-free and non-authorizing. A matching
`run` stops only that reviewed production container, executes only the fixed local
`portal_outcome_battery_campaign.py` command with a sanitized credential-free
environment, and restores production after either successful progress or a cleanly
contained campaign failure. Any residual `pixel-outcome-` or `pixel-work-` container,
network, or volume,
or any competing GPU container, keeps production stopped for manual attention rather
than creating an OOM or cross-run collision. The changed production start identity makes
the confirmation one-use; a resumed batch requires a fresh review but not one approval
per task.

```sh
node deploy/work-controller/model-campaign-maintenance.mjs review \
  --config /var/lib/pixel-outcome/private/configs/campaign-maintenance.json
node deploy/work-controller/model-campaign-maintenance.mjs run \
  --config /var/lib/pixel-outcome/private/configs/campaign-maintenance.json \
  --confirm-campaign-maintenance-operation-sha256 REVIEWED_SHA256
```

`portal_outcome_product_path.py` prevents a successful Builder surrogate from
being reported as a full-product Assistant, Researcher, or Controller result.
It binds every admitted journey to one exact Pixel surface: portal chat and
typed brokers for Assistant work, the Work Broker Builder or Researcher runner
for those profiles, and the durable goal controller for Controller work. It
also binds the exact evidence vocabulary each surface may claim. A missing
adapter, missing emitter, extra evidence type, profile substitution, or unknown
evidence type fails before scoring. The Work Builder and public-only Work
Researcher comparison adapters are registered. Researcher results retain a
URL-free, content-free broker provenance receipt and independently prove exact
citation presence; source-quality preference, freshness labeling, and semantic
entailment remain explicit failing assertions until separately verified rather
than being inferred from citation presence. Assistant and Controller routes
remain explicit release blockers rather than silently downgraded test cases.

`portal_outcome_trial_coverage.py` binds the owner-held transcript by exact
SHA-256, byte count, and 535-line identity, then maps every sanitized trial
journey through its declared product profile, battery task, and available Pixel
and Codex adapters. It classifies full product proof, exact-profile rehearsal,
profile-surrogate rehearsal, and missing coverage separately; none of those
states is inferred from a task merely appearing in the battery. The current
ledger is intentionally red: all 14 journeys have a deterministic rehearsal,
five now use their exact Builder or Researcher profile, nine still run through
a surrogate profile, and none has immutable full-product paired evidence. The
ledger never stores transcript content and exits nonzero until the complete
product proof is real.

`research-revision-review.mjs` defines the next Researcher quality layer without
turning DSV4 into its own benchmark judge. It requires two ordered clean-context
critic passes, distinct context-isolation and inference receipts, exact bindings
to the candidate report, deterministic citation verification, DSV4 model and
inference contracts, and a fixed review-prompt contract. A conservative
controller reduction turns any disagreement, unsupported claim, undisclosed
secondary-source reliance, missing freshness label, or acknowledged unknown
into a revision request. Even unanimous critics can mark a candidate only
`ready-for-independent-evaluation`; the record sets both independent scoring and
backend-output-as-score to false and grants no acceptance or completion. The
isolated critic containers and the bounded revise/recheck loop are wired into
the product lifecycle and preserve every proposal, deterministic verification,
and review as an append-only history. Unit and adversarial contracts are green;
the mechanism still provides no parity credit until a live DSV4 campaign and a
separate independent evaluator demonstrate that the retained final output is
actually better.

`portal_outcome_pair.py` is the formal one-task paired runner. Its owner-private
`pair-system.example.json` contract binds the full Pixel configuration, immutable
Codex and inference-boundary image IDs, the exact DSV4 directory manifest, and a
separately reviewed server-argument vector. It keeps each arm in a distinct
private directory, derives all seven scores from controller and independent
verifier receipts, and emits only a content-free pair index. The Codex harness
digest is recomputed from the resolved image, `/usr/local/bin/codex`, hardened
configuration, inference-boundary image, and exact orchestration source; a
caller-supplied placeholder cannot satisfy it.

Every measured arm must also declare either `cold-first-request` or
`warm-neutral-probe`. The shared `portal_outcome_runtime_control.py` module first
proves a freshly started DSV4 backend has served exactly zero inference
requests. Cold runs begin measured work from that state. Warm runs perform one
fixed, content-free local DSV4 probe, retain only request/response hashes and
separately accounted token usage, then begin measured work. The warm probe has
no tools or task authority, never enters measured latency or usage, and does not
preserve prompt-cache or cross-run state. Both arms call this same mechanism;
pair and campaign receipts bind the condition and reject mixed conditions.

The Codex container derives its CPU, memory, swap, private scratch, bounded
writable workspace, and worker PID ceilings from the same admitted backend-neutral environment
that Pixel compiles. Its dedicated comparison image
pins the same Python 3.11, GDB, debugpy, Pyright, SQLite, DuckDB, and Polars
toolchain available to Pixel Builder; the separately governed advisory Codex
image remains narrow. A credential-free black-box qualifier verifies the actual
Codex 0.147 tool catalog, not Dockerfile claims or labels. The writable half of
the disk envelope is a tmpfs-backed named volume held by a no-network keeper,
with byte-exact bounded copy-in/copy-out; the other half is private scratch.
Fixed convenience limits and unlimited host workspace binds are forbidden:
silently narrowing or widening one arm would turn a runtime difference into
misleading harness evidence.

Run `portal_outcome_pair_preflight.py` before reserving a GPU maintenance
window. The source task supplies the exact admitted DSV4 and inference
contracts; the resulting runtime attestation is reusable only by same-model
tasks carrying those exact contracts. The preflight remeasures the complete DSV4
directory against its private manifest, verifies the launch vector and runtime
executable, materializes and reviews the complete Pixel policy/backend binding,
purely compiles the exact admitted task through the Work Broker against its
declared tools, services, verifier, budgets, and private profile ceilings,
requires owner-private capability-retention evidence showing the contained
Builder externally retained all standard read, search, discovery, write, edit,
execute, debug, LSP, evaluation, subagent, and coordination tasks under one job
authorization with no mid-job prompts, binds that evidence to the exact Pixel
runner image,
resolves every Pixel, verifier, Codex, boundary, and model image by immutable
image ID, and recomputes both harness contracts. It starts no model, creates no
network, runs no task, removes its temporary Pixel review state, and writes one
owner-private content-free readiness receipt. Its output must equal the
`preflightPath` declared by the pair configuration. Pair and battery execution
require that receipt, bind its byte digest into every immutable pair index, and
revalidate configuration, manifest, launch, contract, image, executable, and
harness identities. Pixel's fresh-start receipt is checked against the reviewed
policy, runner, verifier, backend, launch bundle, and harness before any task is
allowed to run. Task, admission, configuration, capability evidence, and preflight bytes are checked
before and after both arms, so mid-pair substitution fails closed.

The admitted task timestamp is immutable corpus metadata. Pixel creates a fresh
Work request timestamp at execution and binds that derived request into the
compatibility receipt; copying an old corpus timestamp into a live Work request
would correctly fail the Work Broker's replay/staleness gate.

```sh
python3 scripts/portal_outcome_pair_preflight.py \
  --root /opt/pixel \
  --task /var/lib/pixel-outcome/materialized/code-edit/task.json \
  --config /etc/pixel-outcome/pair-system.json \
  --output /var/lib/pixel-outcome/private/pair-preflight.json
```

`portal_outcome_battery_campaign.py` runs or resumes the full materialized
battery. Assistant, Builder, and Researcher tasks materialize into separate profile-bound
campaigns and must use a matching preflight; mixed campaigns and implicit
Builder fallback are refused. Researcher tasks bind the actual
`cited-current-research` journey, brokered-public tools, and exact citation
checks rather than the generic Builder journey. The shared Researcher collector
can validate either Pixel- or Codex-named artifacts without accepting cross-arm
substitution. The concrete Codex live-system Researcher adapter now exposes
Pixel's exact split-queue Research Broker as one bounded internal-only MCP tool,
binds it to the same immutable plan, lease, claim, policy, DSV4 and inference
contracts as the Pixel arm, stops tool transport before accepting the untrusted
structured proposal, and uses Pixel's deterministic report finalizer and
citation verifier under a host authority that emits completion only after
exact-run cleanup. The outer runner cross-checks readiness, final authority,
MCP, broker, report, verification, provenance, stable environment, and per-run
environment identities. Source-level composition, substitution,
failure-lifecycle, and complete local release gates are green; this is
implementation evidence only. A newly qualified runner image and real
counterbalanced DSV4 campaign remain required before any parity or
product-capability claim.

Campaign state records the evaluation regime and re-resolves every task's
environment and tool policy before execution. A `maximum-quality` campaign is
mechanically forbidden from freezing or disclosing held-out tasks before a
separate frozen `matched-budget` baseline exists. This prevents the quality
lane from overwriting the same-model control, learning the held-out corpus, or
claiming an efficiency result obtained with a larger budget. The future
frontier-ceiling campaign must bind both reports to that frozen baseline rather
than treating maximum-quality output as a replacement for it.

Outcome runs also prove non-neutering mechanically. Each arm is launched once in
`single-admission-noninteractive` mode, retains its measured completed tool-call
count, and records a content-free interaction receipt from the harness-owned
conversation, checkpoint, controller, researcher, or Codex JSONL lifecycle. The
receipt distinguishes actual operator input after admission from requests for
operator attention, approval, or scope expansion; it cannot be supplied or
self-reported by the worker. A run cannot pass a
`real-tool-outcome` assertion without at least one measured tool call. Paired
comparisons do not require equal tool counts—different efficient strategies are
valid—but any additional Pixel operator intervention or request for operator
attention is classified as `capability-blocking`, just like an unnecessary Pixel
safety block. This includes an inline approval that the user never answers; a
closed-input run cannot mislabel silence as autonomy. Complete campaign receipts
aggregate tool use, actual intervention, requested attention, approval and scope
friction, and unnecessary blocks, so a safety-green result cannot hide a harness
that simply stopped doing useful work or repeatedly handed control back to the
user.

Before creating or resuming campaign state, the battery runner performs a
model-off Pixel system review for every materialized task. Each exact admission,
request, source identity, environment, tool policy, verifier, budget, and profile
must compile through the current qualified Work Broker, and every global runtime
identity must equal the immutable pair preflight. The first incompatible task
aborts before any pair or model start, so a policy/tool mismatch cannot consume a
GPU maintenance window or be misclassified as DSV4 quality failure.

An owner can run the same complete compatibility pass before DSV4 qualification
with `portal_outcome_policy_compatibility.py`. This separate planned-policy lane
keeps the private policy disabled and unqualified while still checking every
task's exact DSV4 identity, inference contract, profile, tools, services,
budgets, verifier, inputs, and output envelope. It produces neither a Work plan
nor a lease, starts no model, container, network, task, or tool, and cannot be
used as readiness or quality evidence. The resulting content-free report binds
one receipt hash per task plus the exact Pixel harness and auditor source hashes,
and refuses mixed policy, environment, backend, model, inference, or harness
identities:

```sh
python3 scripts/portal_outcome_policy_compatibility.py \
  --root /opt/pixel \
  --materialization /var/lib/pixel-outcome/materialized-builder \
  --config /etc/pixel-outcome/pair-system.json \
  --temporary-parent /var/lib/pixel-outcome/private \
  --output /var/lib/pixel-outcome/private/builder-policy-compatibility.json
```

A Researcher materialization additionally requires the selected Work policy's
Researcher backend and the Pixel system environment's `researchRuntime` to be
configured. `researchCourierQueueRoot` must be an existing owner-private queue
outside the source repository and `researchEndpoint` must be the exact local
broker endpoint. Omitting either is treated as accidental capability loss and
fails the structural review; providing the binding does not start or qualify the
broker and does not grant public-network authority.

The campaign writes each task to a new immutable attempt directory, revalidates
all completed evidence before resuming, and enforces the arm order predeclared
by the task's immutable corpus index. Warm runs reverse every cold arm order, so
each task sees Pixel-first once and Codex-first once across the two conditions.
Before the tuning freeze, the campaign validates and compatibility-reviews only
tuning task payloads. The content-free inventory commits the held-out task hashes,
but held-out prompts, workspaces, and verifiers are not opened until the exact
freeze is present and revalidated. The freeze records the held-out commitment and
the held-out progress record binds the exact freeze hash. Progress reports separate real disposable
workspace pairs from non-promotional rehearsal pairs so a synthetic result can
never be counted as product proof. The current curated battery is a baseline
capability campaign: its recovery dimension deliberately earns no credit because
this runner does not inject a process crash. Recovery credit belongs to the
separate declared fault campaign and requires dedicated recovery evidence.

The launch-argument file has this private shape (the argument array must hash to
the shared model contract):

```json
{
  "schemaVersion": 1,
  "operation": "pixel-portal-outcome-dsv4-launch-arguments",
  "arguments": [
    "/models/model",
    "--served-model-name", "DeepSeek-V4-Flash-0731",
    "--host", "0.0.0.0",
    "--port", "8080",
    "--max-model-len", "1048576",
    "--tensor-parallel-size", "2",
    "--max-num-seqs", "16",
    "--gpu-memory-utilization", "0.984",
    "--dtype", "auto",
    "--no-enable-log-requests",
    "--no-enable-log-outputs",
    "--no-enable-log-deltas",
    "--disable-uvicorn-access-log",
    "--reasoning-parser", "deepseek_v4",
    "--enable-auto-tool-choice",
    "--tool-call-parser", "deepseek_v4"
  ],
  "boundary": "Owner-private exact DSV4 server argument vector only. It grants no model start, container, device, network, provider, credential, external-effect, completion, publication, deployment, acceptance, or promotion authority."
}
```

The qualifier's fixed epoch, timestamp-rewritten Docker archives, pinned
BuildKit image, and build-time bind mounts keep build time and source-file
timestamps out of the image identity. Its two clean archives and loaded image
IDs must match before the image is admitted. The resulting image must be
addressed by that `sha256:` image ID in the private model contract. Tags,
mutable registry references, ambient cache contents, manual one-off builds, and
a currently running host process are not admissible runtime identity.

Every controlled run uses fresh isolated client and backend Docker networks, a fresh model
container, fresh writable cache mounts, a read-only root filesystem, no restart
policy, a pinned model and inference contract, and immutable per-run evidence.

The admitted source snapshot is projected through the same production `safe-tar`
materializer used by Pixel Work. Control files such as `AGENTS.md`, `.mcp.json`,
and harness-specific dot-directories are made inert before either arm receives a
copy. The original projection stays read-only. A mutable disposable copy is used
for coding work, then independently collected after the worker exits as a bounded
content-identity manifest and unified patch. The host source is never mutated and
an in-container success message is not accepted as proof of a code change.

Each task also binds a schema-validated backend-neutral tool and authority
envelope. It declares the disposable workspace mode, logical tools, typed broker
services, delegation ceiling, and explicit absence of host, ambient credential,
external-effect, merge, deployment, and policy-mutation authority. A harness must
refuse a task whose native adapters cannot implement the declared envelope; it may
not silently omit a broker or widen a tool surface.

## Exact inference boundary

The model backend is attached only to a fresh backend network. The active
harness is attached only to a separate fresh client network. The disposable
`inference-boundary.mjs` container is the sole dual-homed component and exposes
the model-facing alias used by either harness. This prevents a capable coding
worker from bypassing policy with its own shell or HTTP client.

Pinned Codex 0.147 enters through `/v1/responses`; the boundary strictly maps
that tool/message protocol to the canonical DSV4 `/v1/chat/completions` backend
contract. Pixel's product path remains Chat Completions. The v2 adapter admits
documented messages, ordinary function tools, Codex's `apply_patch` custom Lark
grammar, the exact `multi_agent_v1` namespace, call/output history, and strict
JSON-schema responses. Custom and namespaced tools are flattened only across the
Chat boundary and restored with their original identity in Responses events.
Unknown hosted, custom, namespace, returned, or retained-response surfaces fail
closed. The complete backend stream is validated and buffered before disclosure.
Its receipt records both wire protocols, the adapter version, and backend/client
byte counts without recording prompts, tool payloads, or model output.

Unknown models in Codex 0.147 otherwise fall back to a 272k maximum context. The
comparison runner supplies an owner-private, per-run catalog entry with DSV4's
contract context and compaction threshold while preserving the byte-exact
Codex 0.147 base instructions in `codex-0.147.0-prompt.md`. The pinned prompt
hash and catalog generator are part of the Codex harness contract, so changing
instructions or silently truncating the 1M DSV4 contract changes the harness
identity and invalidates prior comparison evidence.

For DSV4/vLLM the boundary overwrites every admitted request with the exact
temperature, top-p, top-k, min-p, repetition penalty, seed, single-candidate,
EOS, minimum-output, reasoning-visibility, streaming, and output-ceiling
contract. Alternate client ceilings, beam search, penalties, stop sequences,
token filters, chat-template overrides, and reasoning disclosures are removed.
The boundary forwards no client credential headers and persists only a
content-free receipt bound to the exact policy SHA-256. A formal arm fails if
that receipt is missing, widened, reports a backend failure, or disagrees with
the model server's request counter. Streaming backend output is buffered until
its exact usage record has been parsed; a response that would exceed the admitted
aggregate request, input-token, or output-token ceiling is withheld rather than
partially disclosed. Transcript usage, boundary usage, and the model-server
request counter must reconcile before a run can be assembled.
