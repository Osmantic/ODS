# Pixel roadmap

Pixel advances only when a milestone has implementation, operator documentation,
deterministic tests, adversarial tests, clean-room lifecycle coverage, and a recorded
release audit. A feature is not considered complete merely because its happy path works.

## 4.1 — Deep Work foundation

Status: the contracts and functional Scout, Builder, local-model proxy, durable single-job
controller, dependency-ordered multi-milestone goal ledger, independent verifier,
Researcher, exact-replay Data Lab, reliability, extension, knowledge, and optional remote
provider foundations are implemented on the separate Pixel 4.1 candidate line. The
example policies remain disabled. Exact-source automated evidence and every external
promotion gate are tracked in `LIVE-AUDIT-4.1.0.md`; none of this is part of or rewrites
the qualified Pixel 4.0 candidate.

Goal: add a brokered long-time-horizon executor without weakening either Pixel's privacy
boundary or OMP's useful autonomy.

- Add immutable job, private policy, compiled plan, capability-lease, checkpoint, result,
  and verification contracts. The contracts, lease consumption, profile runners, and
  verifier are implemented; the versioned audit is authoritative for release status.
- Add a read-only local OMP Scout, then a full-power Builder inside a disposable outer
  runner with sterile environment, broker-only services, resource limits, and patch-only
  return.
- Add durable progress checkpoints, no-progress and repeated-failure stopping, crash
  recovery, cancellation, and independent acceptance verification. Exact multi-milestone
  goal contracts, dependency scheduling, child-checkpoint recovery, aggregate budgets,
  single-winner appends, evidence-only completion, and a content-free local controller CLI
  are implemented. A restartable bounded runtime now reconciles exact just-in-time child
  bundles and rereads child ledgers after every drive attempt. Goal and child initialization
  are atomically published under contention, and a maximum-size 64-milestone campaign now
  survives 256 forced post-commit controller crashes while repeated 24-controller races
  preserve one execution claim per child. Just-in-time authority is checked before child
  admission and every drive; an expired proposal cannot create or resume execution, while
  a fresh exact pre-admission bundle can continue the recorded milestone without duplicate
  dispatch. Builder additionally recovers the exact consumed-before-launch claim through a
  single-winner `running` checkpoint race, while refusing recovery after lease expiry.
  The first strict goal-to-Builder driver now revalidates the entire runtime context and
  routes only to existing execution, continuation, verifier-recovery, or cleanup-recovery
  paths while returning content-free evidence. A two-iteration goal integration preserves
  verified-candidate lineage and treats the final declared iteration as runnable while
  forbidding any iteration beyond it. Owner-private append-only run custody now lets a
  fresh controller recover the exact compiled plan, expiring lease, and input snapshot;
  it rejects consumed-lease refresh, lineage substitution, input expansion, and budget
  expansion. Refresh and first admission now contend for one atomic append, preventing an
  expiry-boundary race; admission permanently closes refresh, and an unmarked child ledger
  fails closed. Durable scheduling pause, exact-hash resume, and no-active-child
  cancellation are implemented; pause does not claim to kill an already launched
  iteration, and active cancellation is refused until supervised cleanup exists.
  Builder continuation discovery is now durable: the resolver validates the persisted
  reduced lease against the prior claim and verified checkpoint, appends it to custody,
  and waits rather than executing if its issuance time has not arrived.
  Builder preparation now rehydrates continuation, verification, and cleanup-only inputs
  from private custody and exact claims. A strict private-config `work-cycle` entrypoint now
  performs one content-free reconciliation and exits. A locked, hardened, hash-bound
  service/timer bundle can now be rendered without installing it. Service exit now runs a
  locked, replay-safe cleanup-only pass and durably fails an interrupted child after proving
  resource removal. Unproven removal is now a typed retry-only `cleanup-failed` state;
  ambiguous inspection is terminal `recovery-inconclusive`, propagates without redispatch,
  and successful retry cannot double-charge usage. Exact-hash post-cleanup cancellation now terminally incorporates the
  child evidence. The service lifecycle now separately inspects, exact-confirms and installs
  inactive units, exact-confirms activation, and disables/stops/exact-removes units while
  retaining private state. It verifies only root-controlled destination bytes, binds bundle
  ownership to the unprivileged service identity, and rolls failed publication back to a
  complete inactive unit set. Install and activation also reject drift in the live private
  goal or controller configuration. Exact goal preparation now derives child hashes,
  dependency structure, and aggregate ceilings into one inert atomic private bundle. Exact
  controller preparation validates policy/profile compatibility, pinned OMP bytes,
  isolated runtime wiring, and writable-root separation before atomically assembling a
  still-inert controller bundle, while the existing Work Broker compiler is exposed through
  the top-level CLI. Exact-hash-confirmed staging now prepares all child workspaces, persists
  dormant single-use custody, and initializes the goal only after every child is recoverable.
  Guided bounded-job authoring now converts an owner brief for Scout, Builder, and public
  Researcher milestones into exact policy-clamped child requests, input manifests, a goal
  declaration, and a private hash-bound review without creating execution or scheduling
  authority. Exact local-folder admission now streams owner-selected trees into deterministic
  content-addressed ustar objects, keeps control files inert, produces the draft-ready
  private catalog, and rejects links, source mutation, path collisions, resource abuse,
  and publication races. Dataset selection now derives exact raw-file hashes and guided
  `analyze-data` milestones generate policy-clamped local Data Lab contracts with isolated
  exact-replay verification. A loopback guided wizard now creates exact-confirmed inert
  acyclic branch-and-converge goal drafts from opaque already-admitted inputs and enabled private profiles while
  keeping paths, hashes, compilation, staging, scheduling, and execution outside browser
  authority. Exact-confirmed draft-to-controller assembly atomically produces inert goal
  and controller bundles with final-path bindings. The guided launch-preparation path now
  combines that assembly with every exact child compilation in one atomic package from the
  reviewed draft hash; it contains expiring leases but still creates no ledger, stage,
  service, schedule, or execution. The wizard's bounded DAG editor accepts only earlier
  milestone dependencies and defaults to a simple serial chain. Dedicated-account
  provisioning UX and supported-host lifecycle qualification after the latest functional
  change remain. Optional multi-day supervision has a strict 24-milestone,
  49-hourly-invocation controller, append-only systemd/boot evidence, delayed-lease refresh,
  controlled-reboot requirement, hardened service lifecycle, and accelerated regression
  coverage. It is appliance evidence, not a release blocker. Real-process crash operation
  now has the required credential-free event-horizon campaign: a repeated-diamond graph is
  recovered by fresh controller processes killed after all four durable child transitions
  and must converge without replay or duplicate progress. Each supported-host lane runs the
  eight-milestone/32-crash form; promotion requires the full 64-milestone, 84-edge,
  256-crash, 385-process form from the exact release source.
  The runner now rehydrates cleanup-only Scout, Researcher, and Data Lab boundaries from
  their durable single-use claim and exact running or cleanup-failed checkpoint, removes
  only claim-labelled profile resources, and cannot launch through that reduced
  preparation. Multi-profile candidate/checkpoint publication and service routing are
  implemented locally; supported-host lifecycle qualification remains release-blocking.
- Preserve a one-meaningful-approval Builder experience; require new approval only for a
  real scope, data, provider, budget, or external-effect boundary crossing.
- The Builder capability-retention implementation is now development-green at 11/11 in
  both direct pinned OMP and the brokered Pixel lane (100% retention). The permanent gate
  binds the public synthetic corpus, external proof selectors, delegated-agent surface,
  runtime dependencies, source commit/tree, and image digest. Exact-clean-candidate
  repetition and the remaining release matrices are still required before promotion.
- Qualify filesystem, configuration, credential, network, supply-chain, resource-abuse,
  replay, checkpoint, verifier, and artifact boundaries before enabling write capability.

Exit criteria: exact-source automated and supported-host matrices pass twice; no escape,
exfiltration, credential, or authority-expansion finding remains; Builder capability
retention is at least 90% of the uncontained benchmark and standard jobs have no mid-job
prompt; first-time operator acceptance and independent security review pass; the release
is signed through the normal reviewed lifecycle. See `DEEP-WORK.md`.

## 4.2 — brokered research and local Data Lab

Status: functional Researcher and Data Lab candidates and controller integration are
implemented; exact-candidate lifecycle qualification, supported-host repetition, and
independent review remain.

- Reuse SearXNG and the Web Courier behind a typed Research Broker with source provenance,
  citation verification, prompt-injection treatment, and query/domain/byte budgets.
- Admit Vane only as an optional loopback adapter and only when it beats the reference path
  on quality, citation correctness, reliability, privacy, and operating cost.
- Add a local-only DuckDB/Polars/Python/SQLite Data Lab with read-only raw inputs and
  independently verified derived artifacts.

## 4.3 — reliability, knowledge, and extension layer

Status: reliability, extension, knowledge, content-free operator view, and production
controller/service wiring are implemented but disabled; full release qualification remains.
See `DREAM-FORGE-SOURCE-AUDIT.md`.

- Qualify exact local model/backend/accelerator pairs empirically against an exact bound
  corpus and evaluator, and route only profiles that passed every required category within
  their measured structured-output, tool-use, context, latency, and usage envelope. The
  hardened non-authoritative receipt, thirteen-case loopback runner, exact private-policy
  and fresh-worker admission binding, and per-request proxy clamps are implemented. The
  runner proves sustained exact output and observed long context instead of inferring them
  from request limits. Trusted-terminal review, hash-confirmed non-overwriting private-policy
  binding, and content-free readiness/status are implemented without model-start or
  enablement authority. Supported-host evidence, first-time usability, and real local-model
  qualification on the exact release candidate remain before enablement.
- Add normalized event watchdogs, evidence-bound progress, typed terminal reasons, and
  broker-issued hash-bound context capsules with durable fork/resume lineage. The isolated
  contracts, owner-facing create/list/inspect/show/fork/remove lifecycle, crash-safe
  retention custody, hostile-input and concurrency tests, and content-free OMP RPC
  outcome-loop guard are implemented. The controller-wide continuous activation now
  immediately follows each newly
  durable checkpoint, stops on unchanged/no-progress and authority/safety boundaries, and
  uses checkpoint path events with a timer only as a stalled/restart watchdog. The maximum
  repeated-diamond crash/recovery campaign now supplies event-defined giant-project
  qualification. Exact-candidate supported-host repetition and first-user acceptance remain.
  The optional
  48-hour hourly-systemd soak is calendar/boot appliance evidence only; it is neither a
  release gate nor the production scheduler.
- Add isolated signed capability-pack/MCP adapters with exact schemas, sterile execution,
  explicit data classes, bounded transports, health, cleanup, and removal contracts. The
  disabled foundation now has digest-bound declarations, expiring grants, durable one-use
  claim/execution tombstones, an exact modern stdio MCP client, a networkless Docker launch
  contract, bounded disposable workspace create/validate/remove commands, and canonical
  Ed25519 inspection/sign/verification with atomic owner-private disabled installation and
  trust-root-aware status. Exact-review removal, crash-visible recovery custody,
  independently persisted pre-delete custody proof, content-free no-residue tombstones, and
  same-version replay denial are implemented. Disabled image admission now verifies a
  canonical repository manifest digest, separate signed local image ID, exact Linux
  architecture, sterile metadata, and executable without image pull or container start,
  with bounded tar streaming, forced cleanup proof, exact-review admission revocation, and
  a durable single-writer guard across admission/revocation/removal. Explicit confirmed
  disabled health probing is now implemented: it performs only exact MCP discovery and tool
  listing with no grant, call, or client data; proves disposable-container cleanup; emits a
  chained content-free receipt; quarantines a version after three consecutive failures; and
  recovers the exact interrupted operation. The permanent supported-host live scratch-image
  lifecycle gate now covers health as well. The internal disabled runtime now consumes one
  exact expiring grant and checkpoint-bound watchdog decision, burns execution before adapter
  entry, returns structured output only to its live caller, stores a content-free terminal
  receipt, proves claim-bound container/workspace cleanup, and recovers without replay. The
  real supported-host fixture proves an actual tool call. Pure controller mediation is now
  implemented and hostile-tested: one private policy and job authorization bind the exact
  signed pack to the consumed OMP lease and current running checkpoint; private requests are
  schema/classification/resource checked against the complete watchdog-event head; and a stop
  decision creates no grant. Time is used only for expiry/freshness, never as goal cadence.
  Durable queue custody and watchdog-event settlement are also implemented: single-flight
  transfer, append-only content-free phase custody, private staged output, exact response/event
  publication, concurrency control, terminal-stop closure, and conservative no-replay recovery
  pass forced-crash tests. Aggregate runtime and retained-storage admission bound the full
  session envelope. One exact job-scoped OMP catalog and trusted-extension foundation are
  also implemented: the catalog is derived from the authorization and signed pack, its
  filename binds its canonical content, deterministic namespacing prevents builtin/pack
  collisions, the signed raw schemas are preserved, calls serialize against the settled
  event head, and ambiguous post-publication responses permanently close that worker session.
  Nothing is registered ambiently and the extension has no network, credentials, grant, or
  process-launch authority. Production catalog mounting, host-side queue supervision,
  job-lifecycle cleanup/evidence, and routing through Scout, Builder, Data Lab, and
  Researcher are implemented. The Ubuntu/Debian systemd harness now makes the real
  networkless scratch-image health and tool-call lifecycle mandatory and rejects a skipped
  live test. Two exact-clean-candidate host runs, guided pack-selection acceptance, and
  enabled-runtime qualification remain before enablement. A request-selected v2 dispatch that
  routed a v1 signed pack into the v2 lanes from a caller-injected runtimeVersion field and an
  injected v2 config was an authority escalation and has been removed (release-blocking P0). The
  v1 request schema no longer admits any runtimeVersion field; runtime dispatch is selected only
  from the validated controller grant schema and exact pack.schemaVersion, never from request
  content or ambient config, and v1 remains the byte-for-byte default. The sound low-level v2
  runtime fixes are retained as admission-only and non-operational: public retrieval still runs
  through the hash-bound Web Courier broker queue and receipts, every lane keeps durable
  single-winner custody with fail-closed no-blind-replay recovery, ambiguous external effects stay
  pending with zero execution and only transition through an injected externally verified approval,
  and workspace filesystem / allowlisted GET-only loopback keep their secure-files containment and
  bounded streaming reader. No v2 lane is selectable or executable from untrusted request content.
  The operational v2 controller compiler and grant-bound operational filesystem runtime slice
  are now implemented and proven through production controller code plus the runtime dispatcher
  (see the v2 operational evidence): the controller compiles the one exact operational v2 grant
  and its matching private runtime request only after validating the signed v2 pack (exact
  pack SHA/tree/signing), the v2 controller policy, and the exact plan/consumed-lease/running
  checkpoint custody, classification, budgets, and issue/expiry lifetime. The runtime slice
  creates one create-only bounded UTF-8 file inside the exact disposable job workspace and is
  hardened against traversal, symlink, hardlink, parent replacement, overwrite, and byte-limit
  drift; it rejects an expired or not-yet-valid grant from a trusted clock before custody, binds
  grant.contentSha256 === binding.contentSha256 === sha(content), keeps one atomic single-winner
  terminal (active custody is never recreated beside a settled/uncertain terminal), and makes
  receipt publication and custody settlement crash-recoverable without blind replay. The v2 pack
  schema was corrected so the workspace slice is expressible (workspace tool binding may be null
  and a top-level budgets declaration is admitted). A distinct controller-owned v2 queue
  (capability-tool-queue-v2) now provides the durable vertical slice: a closed v2 request/envelope
  schema that carries only untrusted operation data, an immutable queue root that binds the exact
  signed v2 pack SHA/tree/signer, v2 policy, plan, consumed lease, running checkpoint, job,
  limits/budgets/watchdog, trusted workspace/state root, and controller compiler path, and a
  dequeue path where the production controller compiles the one single-use expiring grant and
  runtime request. It persists create-only owner-private single-link request/decision/runtime
  result/response/history/terminal custody and distinguishes no-effect, completed-with-proof, and
  uncertain recovery without blind replay; duplicate requests/operation ids and cross-version
  collisions have one winner. Per the same-UID constraint, this durable slice restricts to a
  single create-only filename directly in the trusted workspace root (nested relative paths are
  rejected and documented as not race-safe). In the second correction pass the queue claim became
  a genuine atomic wx/O_EXCL|O_NOFOLLOW create with fsync and exact read-back identity (single
  winner), the single-level production workspace write became descriptor-bound (an
  O_DIRECTORY|O_NOFOLLOW directory descriptor pinned to the pre-open workspace-root identity,
  creating the one filename relative to it through a validated /proc/self/fd path), a standalone
  receipt is never promoted to completed and never fabricates settled custody, and the runtime
  custody/receipt contract strings and shapes are shared immutable constants with the runtime
  producer rather than duplicated. The injected processing executor moved behind the closed
  production surface into an internal core module. In the third correction pass the atomic claim
  surfaced a typed collision outcome emitted only for EEXIST/ELOOP (every write/fsync/permission/
  read-back/identity failure propagates and is never mistaken for a concurrent winner) and now
  keeps the original descriptor open through fsync/read-back, comparing dev/ino/type/owner/mode/
  single-link against the no-follow read-back so a same-UID inode swap fails closed; public
  recoverOperationalV2 no longer reports completed without an authentic expected grant/decision
  (it returns an unverified/uncertain state, with the queue's grant-bound reconciliation the only
  completion authority); executeOperationalV2 rejects every nested path before custody/effect,
  supporting exactly one create-only filename; and the traversal-capable createFileInWorkspace
  test seam moved behind a test-support module and now enforces a single filename component, so
  production source exports no such seam. This remains a source candidate: the release
  manifest (runtimeEnabled:false) is unchanged and no live qualification or production readiness
  is claimed. Still remaining before any live enablement: the
  release manifest (runtimeEnabled:false) and the admission-only v2 contracts are unchanged, and
  the existing v1 runtime and security controls are not weakened.
- Add a local knowledge vault with explicit consent, source provenance, client and data
  classification, deterministic relevance thresholds, citation, retention, and deletion.
  The disabled foundation now encrypts each source under an externally held master-key
  hierarchy, indexes only client-keyed term fingerprints, binds single-use retrieval to an
  exact vault head and job checkpoint, preserves classification, and implements exact
  deletion, retention purge, backup restore validation, and crash recovery. Strict external
  credential loading, atomic key rotation/recovery, and cross-key-generation backup
  tombstone reconciliation are now implemented in the disabled lifecycle foundation.
  A trusted-terminal entry point now provides exact fresh review/apply confirmation for
  ingestion, checkpoint-bound query output, deletion, offline rotation, and offline
  reconciliation. Reviewed resumable setup, exact local-only worker injection, systemd
  credential projection, and tombstone-safe encrypted restore wiring are now implemented.
  Production service-account credential installation, supported-host lifecycle evidence,
  and first-user acceptance remain before enablement. A plain-language trusted-terminal
  guide now covers setup, add, find, remove, rotation, and reconciliation without giving the
  browser private bytes or keys. The local owner page now includes a responsive,
  content-free Private Knowledge orientation panel with plain-language storage, key-custody,
  consent, deletion, backup, and terminal-workflow boundaries. It receives no knowledge
  bytes, titles, paths, keys, or mutation authority; desktop and phone-width browser checks
  show no horizontal overflow and sampled text exceeds WCAG AA contrast.
- Add plain-language session/activity/tool/service/budget/artifact/verifier UI without a
  global authority-bypass control. The local page now renders a strict content-free,
  stale-aware, read-only session and service projection. Successful production goal cycles
  now publish its heartbeat from authoritative custody, and stop-cleanup republishes its
  resulting failed or recovery-attention state. A separately enabled, exact-confirmed
  lifecycle controls can now separately pause future goal scheduling, resume only an exact
  reviewed paused checkpoint without launching work, or safely cancel an inactive,
  atomically revocable unlaunched, or cleanup-proven goal from a private configuration
  snapshot. All default off and accept no browser-supplied path, checkpoint, or review hash.
  A separate disabled-by-default, process-token-gated semantic review pane now revalidates
  and renders the exact Scout, Researcher, or Data Lab report, criteria, relative evidence,
  artifact inventory, and deterministic verifier limits without adding browser acceptance
  or completion authority. Exact acceptance stays terminal-only. Plain-language fork and
  boundary-expansion flows, expandable per-tool evidence, arbitrary artifact opening,
  accessibility journeys, and supported-host qualification remain.

Exit criteria: every phase passes deterministic, adversarial, crash/recovery, cross-client,
clean-room lifecycle, supported-host, accessibility, and first-time operator gates. No
model heuristic, retrieved memory, UI state, adapter, or worker assertion grants authority.

## 4.4 — explicit remote work provider and operator surface

Status: compiler/preview, owner-private mock lifecycle, credential-free fake-CLI
qualification, signed-MFA verification, credential custody, and the outer-isolated Codex
runner plus exact-host egress-proxy foundations are implemented but disabled. The compiler
validates a required local-attempt receipt, fails closed on never-egress and secret
material, generates a synthetic placeholder capsule, separates ChatGPT-plan from metered
API billing, and binds the structured-output and exact transport contracts. Mock and
isolated execution require mutually exclusive authorization sources; both durably burn one
grant before adapter entry. The fake qualifier proves sterile CLI construction without a
credential or provider. Synthetic tests prove signed password-plus-MFA binding, replay
burn, private ChatGPT/API custody, framed credential transport, pinned image/network/proxy
inspection, strict transcript/output validation, uncertain-turn non-retry, cleanup, and an
exact destination policy. Both images have been built and smoke-tested locally with no
provider access. A production identity issuer and enrollment flow, installed broker
service/credential/network/proxy lifecycle, published image qualification, local
verification, recovery/retention, operator flows, and live qualification remain gates.

- Add a separately governed Codex work-provider contract for explicitly public or
  owner-authorized code egress; do not widen the existing Frontier task classes.
- Add plain-language job preview, authorization, progress, budget, pause, cancel, resume,
  artifact, verification, and boundary-expansion flows to the local control surface.
- Keep merge, deployment, publication, purchase, messages, credentials, and production
  access outside the browser and worker authority.

## 3.3 — privacy-compiled Frontier foundation

Status: incorporated into the consolidated 4.0 candidate; not independently published.

- Local-first Pixel with typed plan-review and failure-triage spillover.
- Separate Frontier identity, credential, policy, approval, budget, and audit boundary.
- Deterministic sanitization, placeholder rehydration, structured Codex output, and
  tool-disabled ephemeral execution.
- SBOM, provenance, protected release gates, dependency auditing, CodeQL, and secret
  scanning.

The consent-bound, synthetic-only live-qualification workflow is implemented and tested
without provider access. Remaining promotion gates are a deployment-owned passing live
receipt and issuer-side closure of historical secret cleanup. Neither may be replaced by
a mock, a network-disabled rehearsal, or an undocumented exception.

## 3.4 — developer and customization kit

Status: incorporated into the consolidated 4.0 candidate; not independently published.

Goal: turn Pixel from a well-secured personal deployment into a reusable base-layer kit
that can be extended without editing broker internals.

- Versioned declarative signed policy packs for typed Frontier tasks, observe-only local
  capabilities, and fixed-helper Operations actions.
- A generator for an offline projection-limb skeleton, schemas, least-privilege service
  boundary, fixed gateway adapter, documentation, and negative tests.
- Compatibility and migration contracts with explicit capability, filesystem, network,
  credential, publisher, and retention declarations.
- Fixture-driven conformance and hostile-pack pressure tests; unreviewed packs remain
  disabled and absent from the gateway.
- Plain-language lifecycle diagnostics plus clean-clone install, enable, disable, upgrade,
  recovery, and removal evidence without authority or residue.

Exit criteria: two exact-commit full release gates pass; security scans have no unexplained
findings; the signed sample lifecycle passes on every supported host; and release identity,
SBOM, provenance, and operator documentation match the promoted source.

## 3.5 — adaptive local-first routing

Status: incorporated into the consolidated 4.0 candidate, including the safe live-qualification workflow; an
authorized deployment-owned live pass and promotion remain gated.

Goal: make Frontier spillover economical, understandable, and privacy-minimizing without
confusing ChatGPT subscription usage with separately billed API use or weakening the
broker's final egress authority.

- Support isolated ChatGPT-authenticated Codex CLI execution for eligible subscription
  access while preserving explicit separately billed API-key mode.
- Require versioned local-attempt receipts and produce deterministic local-only,
  local-retry, operator-context, preview, propose, bounded-auto, or reject decisions.
- Provide exact sanitized previews, transparent token/cost estimates, private
  policy/provider/model-bound cache and deduplication, and one-call behavior for exact
  concurrent or approval-time duplicates.
- Keep final critique and composition local; archive only exact-result-bound,
  content-free finding disposition and quality evidence.
- Report local-versus-live-versus-cache provenance, provider calls, token/cost usage,
  savings, quality outcomes, and failure/quality circuits without prompts or job IDs.
- Exercise receipt misclassification, injection, leakage, replay, cache poisoning, races,
  budget exhaustion, provider failure, quality regression, auth-cache tamper, and cleanup.

Exit criteria: both authentication modes pass the full release gate; ChatGPT mode reuses
only a private saved CLI login; API-key mode remains ephemeral; the disabled limb leaves
no auth path; two exact-commit gates are green; and a deployment-owned synthetic live
qualification passes with explicit credential/spend authorization.

## 4.0 — stable local-agent appliance

Status: release-closure candidate. The technical host matrix passed twice on the prior
functional source; functional release-mechanic changes require exact-source requalification.

Goal: provide a reliable, non-technical operating surface over the hardened base layer.

- Local onboarding and status UI for profiles, limbs, routing, approvals, budgets,
  backups, health, updates, and incident pause/recovery.
- The UI talks only to a narrow local control service; it receives no provider, source,
  model, SSH, or backup-decryption credentials.
- Signed release/update flow with preview, compatibility check, transactional activation,
  rollback, and recovery rehearsal.
- Accessibility, first-run, upgrade, degraded-mode, and disaster-recovery journeys tested
  on every supported host.
- A stable schema/API compatibility promise and a documented support lifecycle.

Delivered in the first increment: a dependency-free loopback-only page and narrow local
control service; credential-free onboarding with preservation of advanced private fields;
strict v1 onboarding, action, and content-free status schemas; exact expiring single-use
`configure`, `plan`, and `verify` actions; disabled-by-default public update checks,
encrypted backup creation, and one-way emergency pause bound to a private policy; private
bounded logs/state; accessible and responsive static UI checks; and deterministic
cross-platform hostile-input pressure. Content-free Frontier provider mode and rolling
job/token/failure/cost budget totals, ceilings, and remaining capacity are also projected;
private plan approval remains outside the browser.

Delivered in the review increment: a private-policy-gated, disabled-by-default,
on-demand projection of exact pending sanitized Frontier capsules, classification,
provider route, token ceiling, placeholder count, cost mode, and plan/capsule hashes.
The read path is no-follow and count/size bounded, rebuilds only a strict allowlist, and
requires a separate random process-lifetime terminal-fragment token rather than trusting
a loopback session as human authentication. It adds no browser approval or provider-call authority. Terminal approval and all private
broker revalidation remain unchanged.

Delivered in the incident-diagnostics increment: every failed fixed control action has
an owner-only receipt bound to its exact result and private evidence. The page exposes
only bounded categories, severity, state, evidence availability, and fixed next steps;
it excludes action identities, hashes, logs, paths, prompts, accounts, and credentials.
Later successful actions of the same kind produce durable result-bound resolution, while
missing required receipt coverage or altered, linked, malformed, incomplete, or
retention-inconsistent retained evidence fails the entire projection closed instead of
reporting a false clear state. This is local orientation, not incident recovery or
authority resume.

Delivered in the local-readiness increment: `./pixel doctor` and the local page classify
the supported-host contract and broad CPU, memory, free-storage, accelerator-vendor,
container-readiness, and generated-model configuration/context tiers, then provide a
conservative model/context starting point. Missing and unreadable generated configuration
are distinct states, and unreadable configuration fails readiness closed. The
collector runs no process or network probe, accepts no caller path, contacts no provider,
and projects no hostname, serial, device name, model/provider identifier, URL, local path, exact hardware value, or process
output. Recommendations are explicitly advisory and cannot assert exact model fit.

Delivered in the access-and-budget increment: credential-free onboarding selects eligible
ChatGPT plan access or separately billed API access and one of three exact managed local
safety budgets. Credentials and login remain terminal-only. Custom private policies are
represented explicitly and cannot be silently replaced by managed authentication or
budget choices. Status distinguishes off, prepared, active, and fail-closed unavailable
states, identifies generated-policy versus live-broker evidence, and never projects
credentials, raw policy, prompts, accounts, paths, or exact provider identities.

Delivered in the signed-update foundation: deterministic unsigned release envelopes bind
the archive, SBOM, provenance, exact packaged source commit/tree, qualified functional
source, release and compatibility identities, supported hosts, and upgrade floor. The
signer proves a clean exact HEAD, ancestry, and an exact three-file evidence-only delta between the
qualified and packaged commits, avoiding impossible self-referential commit evidence
without admitting later functional changes. Maintainer signing requires an exact Supported
compatibility/source record and protected Ed25519 key. Intake requires an independently
provisioned signer, rejects mixed or unsafe bundles, parses without extraction, executes
no candidate code, and emits only a content-free verification receipt. Neither intake nor
the browser has activation authority. Confirmed forward-only preparation now takes the
deployment lock and copies only the reverified bundle into bounded owner-only staging
under a full-envelope-hash identity. It is idempotent, detects staged-byte drift, safely
recovers exact interrupted copies, and still performs no extraction or candidate execution.
Confirmed compatibility rehearsal now revalidates that stage, safely materializes a
private normalized source tree, and runs only fixed syntax parsers with the exact pinned
host/Node/Python contract. It is network-free, idempotent, drift-detecting, and explicitly
does not execute candidate programs or touch the active deployment.

Delivered in the signed-update lifecycle: exact single-use activation claims precede a
fixed transactional configure/plan/apply sequence; exact update-bound rollback restores
the preceding release through the trusted current controller; content-free recovery can
finalize interrupted receipts without rerunning candidate code; and exact post-rollback
cleanup quarantines and removes only completed staging work while preserving installed
and active releases. Each operation is deployment-locked, private-state-bound, previewed,
and covered by interruption, tamper, drift, replay, retention, and clean-room regressions.

Delivered in the read-only update-status increment: the loopback page inspects only the
owner-bound filesystem shape of private candidate, rehearsal, activation, rollback, and
cleanup workspaces. It projects version labels, bounded counts, interruption state, and
fixed next-step codes without paths, hashes, signers, source identity, or receipt content.
It explicitly makes no signature or migration-validity claim and adds no activation,
rollback, recovery, cleanup, or migration authority; those remain exact terminal flows.

Delivered in the recovery-guide increment: exact retained control results and incident
receipts produce a bounded backup/rehearsal/restore and containment/resume checklist. The
guide distinguishes creation from validation, decryption, rehearsal, and restore; it also
refuses to infer current broker pause state from an earlier successful pause. It projects
no path, recipient, hash, reason, action identity, artifact, or evidence, and adds no
decryption, restore, recovery, or resume authority.

Delivered in the custom-budget increment: an existing private schema-v2 policy can draft
bounded, 15-minute exact proposals in the page. The browser has no apply endpoint. A
trusted-terminal confirmation rechecks the hidden policy path and exact source bytes,
writes a durable claim and exact backup, and changes only `budgets`; configuration,
activation, authentication, approval, and provider use remain separate.

Delivered in the human-approval increment: source, Operations, and ordinary Frontier
approval wrappers require a real controlling terminal, reset to the trusted system path
and `/usr/bin/sudo`, invalidate prior administrator timestamps, reject root and
passwordless-sudo policy, require fresh password-backed administrator authentication,
display the complete protected object, and require an unpredictable hash-bound phrase.
Protected JSON is rendered with control and non-ASCII characters escaped. The new
timestamp is invalidated on normal exit, while the broker independently rechecks the
exact immutable object, policy, expiry, cancellation, replay, cache, and budget state.
The fixed-synthetic live-qualification path retains its separate single-use consent flow.

Delivered in the qualification-contract increment: a machine-readable matrix requires
automated and real-systemd lanes on both supported hosts, keeps all automated lanes
credential-free with zero provider calls, mirrors Doctor's advisory memory/model classes,
and binds capability profiles to their source files. The separate
`deep-work-event-horizon` gate requires exact-source evidence across the maximum dependency,
durable-transition, crash, and fresh-process boundaries; elapsed days confer no credit.
`./pixel promotion-status` produces
a clean-source, content-free readiness index and remains blocked until two exact-commit
passes and every host, recovery/security, model-contract, live-qualification,
historical-secret, and licensing gate has exact reviewed evidence.

Delivered in the supported-host harness increment: `./pixel qualify-hosts` binds a clean
source commit/tree and release manifest to two manifest-fingerprint-pinned disposable VMs.
Both guests run the real local-only appliance lifecycle, live sandbox isolation, degraded
dependency detection and recovery, encrypted restore, exact rollback, and owner-UI reachability without
credential inputs or provider calls. The manual private-runner workflow cannot be triggered
by a pull request, uses the shared host lock, and always removes its bounded VM names.

Still required for 4.0: re-run two deployment-owned exact-source
systemd/degraded/disaster-recovery/UI-reachability matrices after the final functional
change; complete non-technical owner usability; bind the one-time signed bootstrap
lifecycle evidence; complete the authorized live-provider gate; and record the approved
historical-secret and proprietary-distribution evidence. Production signing-key custody
and required human review remain owner-controlled blockers.

Exit criteria: a new owner can deploy and operate the reference profile without editing
generated files, every consequential action remains previewable and attributable, and
two consecutive full host/recovery/security matrices are green on the exact release
commit.
