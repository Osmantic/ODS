# Product qualification and promotion

`QUALIFICATION-MATRIX.json` is Pixel's machine-readable release floor. It prevents a
container-only success from being described as full appliance support and prevents a
memory bucket from being described as proof that an unnamed local model will fit or
perform well.

## Host lanes

Every promoted release needs both automated lanes and both real service-manager lanes:

| Lane | What it proves | Credential/provider boundary |
|---|---|---|
| Ubuntu 24.04 automated | Static, unit, security, clean-room lifecycle, plugin, and package behavior | No credentials; no provider calls |
| Debian 12 automated | The full pinned release gate, including reproducible packaging | No credentials; no provider calls |
| Ubuntu 24.04 systemd | Installation, service/sandbox isolation, real-process Deep Work crash recovery, exact inactive-to-active event supervisor lifecycle, recovery, rollback, and removal on a deployment-owned host | Synthetic local fixtures only; no provider calls |
| Debian 12 systemd | The same real-service, real-systemd supervisor, and real-process recovery lifecycle on the second supported host | Synthetic local fixtures only; no provider calls |

An automated container is useful evidence, but it is not a substitute for a real systemd
lane. Host evidence stays outside Git, is bound to the exact source commit/tree and release
manifest, and is reviewed before its SHA-256 enters a promotion-readiness record.

On the dedicated private Incus runner, an operator can run both real-systemd lanes with:

```bash
./pixel qualify-hosts --evidence /secure/new-supported-host-run
```

The command requires a clean exact Git source, takes the shared disposable-host lock,
launches only manifest-fingerprint-pinned Ubuntu 24.04 and Debian 12 VMs, and refuses to
replace an existing VM or evidence directory. Each guest starts with an empty environment,
uses a loopback-only deterministic model/search fixture, and rejects credential-like
inherited variables. It runs the real configure, pinned bootstrap, plan, apply, and verify
flow; an eight-milestone branch-and-converge Deep Work campaign across 49 fresh controller processes and 32
forced post-commit terminations; an authenticated sandboxed workspace tool turn; loopback
owner UI; model and service degraded
detection/recovery; encrypted backup validation, rehearsal, and actual restore; exact
release rollback; and bounded disposable removal. The VMs are deleted on success or
failure. Detailed private logs remain outside Git, while the matrix summary binds only
the source commit/tree, release-manifest hash, lane, fixed checks, and evidence hashes.
Each lane also copies the exact candidate into a new root-held `/opt` tree, prepares a
deliberately paused zero-authority goal under a new owner-private `/var/lib` root, installs
its exact units inactive, explicitly activates the path watcher and liveness-only timer,
and observes a successful initial service run. It then stops only the watchdog while
leaving the enabled event path active, so exact-confirmed cancellation must produce a
durable checkpoint event and a distinct successful service run before the units
are removed and the retained private terminal state is revalidated. This is supervisor-
mechanics evidence only; it does not claim that a model or OMP completed useful work.
This bounded campaign proves supported-host process-loss recovery. The separate maximum
event-horizon gate expands the same exact-source recovery model to the full graph ceiling.

## Deep Work event-horizon gate

Long-horizon release evidence is event-defined, not day-defined. The required
`deep-work-event-horizon` gate uses one credential-free synthetic qualification goal with
the full 64-milestone ceiling arranged as repeated diamonds: one durable baseline,
independent branches, and verified convergence. It exercises 84 dependency links, 21
branch points, 21 convergence milestones, and 42 dependency levels. The production goal
and child ledgers must converge across 385 fresh operating-system processes and 256 forced
exits--one immediately after every durable `running`, `verifying`, `verified`, and
`completed` child transition. Elapsed time is recorded only to prove that fresh-process
restart boundaries actually occurred; no number of hours is treated as progress.

Run it from the exact clean source used for the release candidate:

```bash
node scripts/deep-work-endurance-probe.mjs \
  --output /secure/new-event-horizon-evidence.json \
  --source-commit "$(git rev-parse HEAD)" \
  --source-tree "$(git rev-parse 'HEAD^{tree}')" \
  --milestones 64 --restart-delay-ms 10
```

The schema-bound result requires 64 independently evidenced completions, 64 model-request
accounting units, 130 parent checkpoints, 320 child checkpoints, zero replay or duplicate
credit, and zero credentials, provider calls, network requests, external effects, or
production deployments. The private evidence must be independently matched to the clean
source commit/tree before its SHA-256 enters promotion claims.

Pixel also retains the 48-hour systemd/controlled-reboot soak as optional appliance
endurance evidence. It is useful for a particular deployment that wants calendar and boot
coverage, but it is not a release blocker and is not evidence that useful work occurred.

The disabled Codex work-provider images have an additional credential-free construction
gate:

```bash
scripts/qualify-work-codex-images.sh
```

It uses a digest-pinned BuildKit worker, builds both the runner and exact-host proxy twice
from scratch with a fixed source-date epoch and rewritten OCI timestamps, and requires the
two OCI archives for each image to be byte-identical. The command downloads only pinned
build dependencies; it receives no OpenAI credential, invokes no provider, and retains no
image archive or builder after the comparison.

`.github/workflows/product-qualification.yml` exposes the same run as a manual-only job on
the private `pixel-systemd-vm` runner. It is never part of an untrusted pull-request event.

## Model-capacity guidance

The five capacity rows exactly mirror `./pixel doctor`: under 8 GiB, 8–15 GiB, 16–31
GiB, 32–63 GiB, and 64-plus GiB. Their labels are conservative starting points, not model
recommendations or benchmarks. Every row keeps `fitIsGuaranteed=false` and requires a
fixed synthetic task-contract check, local latency/memory measurement, and owner quality
review before a deployment claims that its chosen model is suitable. Model names,
endpoints, hardware identifiers, and measurements remain private.

The capability-profile rows are checked against the authored files under
`profiles/capabilities/`. This catches documentation or release-matrix drift and keeps
Frontier disabled unless the owner explicitly enables it.

## Fail-closed readiness artifact

On a clean source tree, this prints a content-free readiness index:

```bash
./pixel promotion-status
```

The default exits with status 3 and reports `blocked`. Only the locally verifiable
model-capability contract is marked pass. No missing host, recovery, outcome-parity,
signed-upgrade, owner-usability, live-provider, multi-provider, historical-secret, or licensing evidence
is inferred.

After reviewers have inspected owner-controlled evidence, create a private mode-`0600`
claims file containing `schemaVersion`, `consecutivePasses`, and exactly these eleven
external gates: `automated-release-matrix`, `supported-host-systemd-matrix`,
`deep-work-event-horizon`, `recovery-security-matrix`, `outcome-parity`,
`signed-release-lifecycle`, `owner-usability`,
`frontier-live-qualification`, `multi-provider-local-first`, `historical-secret-closure`,
and `distribution-license`.
Each gate is either:

```json
{"status":"pass","evidenceSha256":"64-lowercase-hex-characters"}
```

or:

```json
{"status":"blocked","evidenceSha256":null}
```

Then generate a new owner-only artifact without overwriting prior evidence:

```bash
./pixel promotion-status --claims /secure/promotion-claims.json \
  --output /secure/promotion-readiness.json
```

The command binds the output to the clean Git commit/tree plus the exact raw release
manifest and qualification-matrix hashes. It turns green only with at least two
consecutive exact-commit passes and all eleven gates passing. The output contains no
evidence path, host or model identity, credential, or provider content. It is an index;
reviewers still verify the separately retained evidence and its authenticity. Automated
qualification never spends subscription or API usage. The one-call synthetic Frontier
gate remains the separate explicitly authorized workflow in
`FRONTIER-LIVE-QUALIFICATION.md`.

`outcome-parity` is not a narrative review. For each permanent journey, retain private
mode-`0600` Pixel and Codex run records plus their exact evidence outside the source tree.
Both runs must use real backends and tools and bind the same corpus, journey, task
specification, source snapshot, environment, tool policy, comparison lane, and effect boundary. Generate
the inert admission before either backend runs, then generate the content-free paired result:

```bash
./pixel outcome-task-admit --task /secure/task.json \
  --output /secure/task-admission.json
./pixel outcome-compare --pixel-run /secure/pixel-run.json \
  --codex-run /secure/codex-run.json --output /secure/comparison.json
```

The task admitter reopens every referenced request, source, environment, tool-policy,
verifier, required sanitization file, and controlled-lane model/inference contract; validates capability, data-route, effect, and
budget consistency; and emits a pathless content-free digest record. The task and admission
grant no execution or provider authority.

Run records are produced by the same fail-closed stack, never by narration. The run
assembler (`scripts/portal_outcome_runner.py`) streaming-hashes the exact model artifact —
single GGUF files or complete safetensors shard directories through per-file manifests whose
aggregate digest must match the admitted contract — binds runtime image, executable, and
launch-argument digests, refuses context or sequence widening, and converts live runtime
state (llama.cpp `/props` and `/slots`, vLLM `/v1/models` and zero-token `/metrics`) into
fresh-runtime evidence that rejects warm caches, template drift, and shared servers.
Execution metrics come from measured event transcripts under the admitted budgets: token
usage is summed from completed turns, completion requires a real completed turn with exit
zero, and any budget ceiling breach fails closed. Assertions are scored only by the
deterministic verifier engine (`scripts/portal_outcome_verifier.py`), which executes a
sha-pinned check vocabulary over exact evidence bytes with per-check evidence bindings and
journey-exact coverage; backend output is never the scorer. The comparator verifies every referenced file by
size and SHA-256, requires exact journey
evidence and assertion coverage plus independent scoring, rejects synthetic or self-graded
runs, and blocks on any failed assertion, score difference, incomplete execution, P0/P1,
authority violation, or unexplained delta. The private run content, paths, reviewer identity,
provider content, and credentials never enter its output. A passing comparison is evidence
for one exact task only; the promotion claim is the independently reviewed aggregate over
the full corpus and required perturbations.

A single favorable pair cannot satisfy the gate. Build a private campaign plan whose paths
name one distinct admitted task and distinct Pixel and Codex run records for the baseline
and every fault declared by every journey, then run:

```bash
./pixel outcome-campaign --plan /secure/campaign-plan.json \
  --output /secure/outcome-campaign.json
```

For the current 13-journey corpus this is 102 distinct paired comparisons: every one of the
51 baseline/fault scenarios runs in both `product-default` and `same-model-harness` lanes.
The product lane measures the actual configured products. The controlled lane requires both
runs to observe the same exact model artifact, tokenizer, chat template, runtime image and
arguments, inference settings, task-scoped function-tool policy, empty initial prompt cache,
and fresh cross-run-isolated runtime. Pixel's product model route is the local vLLM
DeepSeek-V4-Flash-0731 deployment; llama.cpp/qwen fixtures qualify plumbing mechanics only
and never substitute for the product model in either lane. It also requires distinct Pixel and Codex harness
contract hashes, so it changes the harness rather than accidentally rerunning one system.
Within each lane the campaign additionally requires one exact Pixel harness contract hash
and one exact Codex harness contract hash across every pair, and it publishes those lane
harness hashes content-free, so per-scenario harness swaps cannot cherry-pick results.
The campaign covers all journey-specific stale-source, injection, crash, restart,
ambiguous-write, replay, scope, secret, mock, self-grade, and artifact-substitution cases. It reopens and
revalidates both private runs for every pair; rejects unknown, duplicate, reused, or
mismatched records; and remains blocked unless the set is complete and every comparison is
parity. Only the hash of a passing complete campaign may support the `outcome-parity`
promotion claim. This defines required coverage without claiming those real runs exist yet.

The `signed-release-lifecycle` evidence follows `releaseUpdate.qualificationMode`. Pixel
4.0.0 is the original `bootstrap` release because the last Supported release predates the
signed-update controller. Pixel 4.1.0 is the one explicitly documented bridge successor:
it keeps the bootstrap policy byte required by the frozen, already-qualified 4.0 updater,
sets its upgrade floor to 4.0.0, and must prove both clean-host bootstrap and the complete
4.0-to-4.1 signed update lifecycle. This exception preserves the Pixel 4.0 candidate
instead of rewriting or silently replacing its trusted controller. The 4.1 controller
rejects bootstrap policy for 4.1.1 and later; every subsequent release uses `forward` and
must perform the same lifecycle from a Supported prior release to the exact candidate.

Bootstrap evidence combines a separately trusted Candidate qualification signature for
the exact archive with clean installation, verification, backup/recovery, rollback, and
removal of that exact package on both supported hosts. It also exercises signed staging,
no-execution rehearsal, activation, verification, interruption recovery, rollback, replay
rejection, and cleanup against a separately signed non-production fixture. The
qualification signature grants no staging, activation, publication, or production trust
authority. Pixel 4.0.0 does not advertise in-place eligibility from a release that lacks
the controller, and Pixel 4.1.0 is not promotable until an owner validates the separately
controlled bridge evidence.

#### Qualification-root-only lifecycle

The signed-release lifecycle also exposes four qualification-prefixed operations
(`release-qualification-prepare`, `release-qualification-rehearse`,
`release-qualification-activation-preview`, and
`release-qualification-activation-claim`) that run entirely inside an explicitly isolated,
disposable qualification root. Here the term *staging* means **qualification-root-only
private copying**, never production staging: none of these operations stages, activates,
publishes, or changes any production install, active version, or compatibility record.
The qualification signature grants no production staging, activation, publication, or
production trust authority, and every qualification receipt explicitly records
`publicationAuthority=false`, `productionActivationAuthority=false`,
`compatibilityMutationAuthority=false`, and `productionStateChanged=false`.

Each qualification root carries an owner-private single-link `QUALIFICATION-ROOT.json`
attestation (bound into every receipt by SHA-256) that records the absolute root, the
explicit safe baseline version, the exact candidate upgrade relation, and the production
install root it must not equal, contain, or be contained by. The root is freshly
initialized for the exact idempotent request and fails closed on symlinks, hardlinks,
group/world access, traversal, unexpected entries, missing attestations, production-root
overlap, and any reuse outside that exact request. Candidate programs are never executed
by the core.

A disposable host harness is the only authorized executor of candidate code and of any
later qualification activation-result, rollback, recovery, or cleanup step that would
consume production state; the core exposes only the low-level exact-hash
preview/claim operations and never fakes a successful candidate execution.

**This is a custody and rehearsal prerequisite slice only; it is not terminal
signed-release-lifecycle evidence.** The `release-qualification-activation-preview` and
`release-qualification-activation-claim` operations derive and atomically claim an exact
qualification-root-only activation hash and never execute candidate code. Every preview
and claim receipt explicitly records `candidateCodeWillExecute=false` and
`candidateCodeExecuted=false`, and the claim does not activate, publish, roll back, or
otherwise mutate any production install, active version, compatibility record, or
rollback marker. They must not be mistaken for a completed activation, promotion, or
release: a claim is only the reusable, exact-hash custody handoff to the disposable host
harness.

Before any promotion to the terminal signed-release-lifecycle gate, the following real,
disposable-host execution steps remain required and are **not** provided or faked by this
slice: a real candidate execution result, a real activation-result and rollback path with
an active-version file, interruption recovery, replay/duplicate-claim rejection on the
host, and cleanup of the never-executed qualification claim and disposable root.

Cleanup of a never-executed qualification claim is intentionally **deferred, not
implemented** in this slice. Adding a qualification cleanup command here would risk being
read as a terminal lifecycle cleanup and would consume the disposable host's single
owner of qualification-root state; because the qualification root is disposable and the
claim is never executed by the core, the disposable host harness owns removal of the
entire root after it records any custody result. Deferring cleanup keeps this slice honest
about what it does not complete.

#### PR-1: qualification host-run skeleton

PR-1 adds the `release-qualification-host-run` command, a qualification-root-only
skeleton that acquires one private host-run slice for an already-claimed activation
without executing candidate code or producing terminal promotion evidence. It writes
three artifacts under the root `runs` namespace (`runs/<candidate-id>/HARNESS-RUN.json`
and `EXECUTION-TOMBSTONE.json`) and a durable activation acquisition marker
(`activations/<candidate-id>/QUALIFICATION-ACQUISITION.json`) that records the
harness-run and execution-tombstone SHA-256s. The acquisition marker is written and
fsynced first, then the run directory is published with a no-replace atomic rename;
a crash before or after that durable marker may leave incomplete private state, but a
later invocation fails closed instead of reclaiming or replaying the acquisition. A
pre-existing marker or final run path rejects replay and requires the future recovery
phase to reconcile any incomplete state.

Every artifact in this slice is non-authoritative and non-terminal: each records the
exact non-authority boundary `publicationAuthority=false`,
`productionActivationAuthority=false`, `compatibilityMutationAuthority=false`, and
`productionStateChanged=false`, plus `candidateCodeExecuted=false`,
`terminalPromotionEvidence=false`, and `networkUsed=false`. The host-run harness,
execution tombstone, and acquisition marker grant no execution, activation, publication,
update, or production trust authority and must not be read as terminal qualification or
supported status.

Execution, activation-result, rollback, recovery, cleanup, and promotion are explicitly
**deferred, not implemented** in this slice and remain the responsibility of the real
disposable host harness. This skeleton only records that a host-run slice was acquired;
it never runs candidate programs and never reports a completed lifecycle.

#### PR-1b: qualification execution custody and content-free result

PR-1b adds real disposable-host candidate execution custody plus content-free
execution-result evidence, while keeping all state inside the attested qualification
root and granting no production, publication, compatibility, or promotion authority.
Execution is **not** modeled as transient-only: it has its own durable, private, fsynced,
no-replace `QUALIFICATION-EXECUTION-CLAIM.json` marker
(`activations/<candidate-id>/QUALIFICATION-EXECUTION-CLAIM.json`) written by
`release-qualification-execution-claim` **before any candidate process can start**.
Acquisition alone is not proof that execution started; the execution claim is. Once the
execution claim exists, automatic re-execution is forbidden after any crash or
interruption; only exact terminal result recording or a later explicit recovery phase
may proceed. Crash before the durable claim means execution did not start and the claim
may be retried; crash after the claim but before a result is execution-interrupted and
may never auto-reexecute; crash after result publication but before the secondary marker
is recovery-only.

Before claiming execution, the core revalidates the published `HARNESS-RUN.json` and
`EXECUTION-TOMBSTONE.json` bytes against the acquisition-marker hashes, all identity
fields, the activation claim, the qualification-root attestation, the exact rehashed
source tree, the exact supported host, and the authority=false boundary. Unexpected
activation or run entries are strictly rejected; exact file-set validation is never
weakened to broad subsets. The execution claim is bound to the candidate, activation
hash/claim, acquisition-marker hash, harness/tombstone hashes, rehashed source tree,
exact supported host, and qualification-root attestation.

`release-qualification-execution-result` records a separate immutable
`runs/<candidate-id>/EXECUTION-RESULT.json` artifact and, for deletion/recovery
durability, a terminal result marker
(`activations/<candidate-id>/QUALIFICATION-EXECUTION-RESULT.json`). Success requires the
observed exact candidate version and exit 0; failure carries a closed phase/reason;
timeout and signal can never become success. The PR-1 `EXECUTION-TOMBSTONE.json` is
immutable proof that PR-1 itself executed nothing; it is never mutated and never read as
terminal execution evidence. Deleting run/result paths never permits re-execution
because the acquisition marker and execution claim remain anchored in the activation
directory.

The core validates, claims, and records content-free observations but never itself
silently synthesizes successful execution. A separate narrowly scoped disposable-host
harness (`scripts/qualification-host-execution.py`,
`release-qualification-host-execute`) runs candidate code only from the verified
activation source under no-network, empty-credential, bounded process-group custody and
emits a bounded content-free observation for the core to record. Because the existing
candidate install/activation contract cannot yet be safely executed without widening this
slice, the harness implements the maximum honest executable boundary and explicitly
fails closed/defer the unsupported portion rather than fabricating success: a bounded
probe that exits zero is reported as `deferred` (phase=install,
reason=install-contract-deferred), never as install success. All result artifacts keep
`publicationAuthority=false`, `productionActivationAuthority=false`,
`compatibilityMutationAuthority=false`, `productionStateChanged=false`, and accurately
report `candidateCodeExecuted`/`executionObserved`. No credentials or provider calls are
made, and the harness never touches the production install root, live services,
compatibility, publication, or provider/network credentials.

#### PR-1c: fixed execution spec and exclusive exactly-once attempt boundary

The execution specification is no longer derived inside the harness. The
`release-qualification-execution-claim` command accepts the probe relative path and
probe timeout, derives the exact spec from the verified activation source (probe
SHA-256 plus argv/cwd/env/image/sandbox/resource/container-name contract), and durably
O_EXCL-writes+fsyncs a fixed owner-private `runs/<candidate-id>/QUALIFICATION-EXECUTION-SPEC.json`
**before** writing the execution claim, which references `executionSpecSha256`. A crash
after the spec but before the claim leaves the run file-set (spec present, no claim) so
that any retry fails closed. The container name is a high-entropy `pixel-qual-exec-<20hex>`
generated during the O_EXCL spec/claim step and bound into both the spec and the claim;
the validator requires the name grammar and exact claim/spec equality rather than
regenerating a predictable name.

The harness accepts no `--probe`/`--probe-timeout`; it reads only the claim-bound fixed
spec. Before any Docker create/start it durably O_EXCL-writes+fsyncs a fixed
`runs/<candidate-id>/EXECUTION-START.json` attempt marker hash-bound to the validated
claim/spec/acquisition and the exact container name. This is the exclusive exactly-once
attempt boundary: concurrent or replayed harness calls fail before candidate execution.
The run file-set contracts are explicitly phased: acquired (`HARNESS-RUN.json`,
`EXECUTION-TOMBSTONE.json`), claimed (`+SPEC`), started (`+EXECUTION-START`), observed
(`+OBSERVATION`), result (`+RESULT`).

The harness binds the already-revalidated activation source read-only into the container
(no random host workdir) with container tmpfs scratch. Before writing EXECUTION-START it
proves the claim-bound container name is exactly absent, and it binds the started attempt
into exact Docker custody labels (attemptId, candidateId, executionSpecSha256,
executionStartSha256). The Docker lifecycle owns cleanup in a `finally` block once
create/start is attempted: `docker run --detach` stdout is parsed as exactly one immutable
64-hex container ID (any malformed, multiple, or extra output is ambiguous and fails
closed). The started container is immediately inspected by that exact ID and required to
match the exact claim-bound name, image/digest, and all start/spec/candidate custody
labels before any wait; all mutation (`docker wait`, `docker kill`, and `docker rm`) then
binds only to the immutable ID, never to the mutable name. When the ID is unavailable
(docker-run timeout/error), cleanup discovers an owned record by the claim-bound name,
extracts and validates its 64-hex Id, and still mutates only by that ID; a foreign or
mismatched container fails closed and is left untouched. It then proves the claim-bound
name is exactly absent before any terminal observation is written
(`containerRemoved=true`, `absenceProven=true`); a foreign container that reappears under
the name fails closed and is never removed; if cleanup/absence proof fails, the durable
start marker remains, no terminal observation is written, and all retries fail
closed. The result recorder reads and independently
validates the spec and start marker, recomputes `executionSpecSha256`, validates the
observation against them, and closes `executionSpecSha256`/`executionStartSha256` into
The candidate runs only through an exact claim-bound `entrypoint=/usr/bin/env` with spec
argv `[-i, KEY=VALUE..., /bin/bash, probe]` carrying exactly the claim-bound
PATH/HOME/LANG/PIXEL_QUALIFICATION_CUSTODY values, with no Docker `-e` flags and no
duplicated `/usr/bin/env`, so host and image ENV are never inherited as candidate
environment, and the container uses `--log-driver none` so an adversarial probe cannot fill
host disk with unbounded stdout. The fixed spec validation is
exact (not partial): it rebuilds the expected spec from the revalidated state plus the
claim-bound probe/hash/timeout/container/uid/gid and requires full equality, so tampering
the image/digest, environment, sandbox/resource flags, log driver, scratch, boundary, argv,
or source hash is rejected. Pinned-image presence/identity verification is non-mutating and
runs before the EXECUTION-START marker is written, so a missing image or unavailable Docker
daemon consumes no execution attempt and writes no start marker. Container absence is proven
exactly (`docker container inspect` nonzero plus the empty-JSON `[]` response and the exact
`No such container: <name>` message); any other nonzero status, timeout, malformed output, or
daemon/permission error fails closed rather than fabricating absence.

the immutable result. A bare arbitrary spec hash/sandbox dict in an observation is not
authoritative evidence.
#### PR-1d: post-START infrastructure-failure interruption (indeterminate, never result)

After `EXECUTION-START.json` is durably written, candidate code may or may not have
started: a timeout, wait failure, cleanup failure, process crash, or power loss makes
candidate execution state indeterminate. This slice closes that honest dead end without
retrying indeterminate execution, synthesizing an observation/result, deleting durable
evidence, weakening identity/custody, or claiming promotion authority.

Two new private, immutable, owner-private artifacts are written only by the
`release-qualification-execution-interruption-record` command, under the existing
exclusive qualification stage lock and after exact START-only revalidation:

- `runs/<candidate-id>/EXECUTION-INTERRUPTION.json` — the terminal interruption
  artifact (schema `release-update-qualification-execution-interruption-v1.schema.json`).
- `activations/<candidate-id>/QUALIFICATION-EXECUTION-INTERRUPTION.json` — the terminal
  interruption activation marker
  (schema `release-update-qualification-execution-interruption-marker-v1.schema.json`).

The artifact is deliberately **not** an `EXECUTION-RESULT.json`: it has a distinct
filename, `status="interrupted"`, `operation="pixel-release-qualification-execution-interruption"`,
and `candidateExecutionState="indeterminate"` (never `false`/`success`/observed). It
carries no `success`, no observation/result fields, no `candidateCodeExecuted`, no
stdout/stderr, no paths, no credentials, and no user content. It binds the candidate,
`activationHash`, exact host/source identities, the claim/spec/start hashes, the exact
container name, the attempt ID, a fixed `reason="post-start-infrastructure-failure"`, and
keeps `terminalPromotionEvidence=false` and every authority flag false. The marker binds
the immutable artifact hash for deletion/recovery durability.

`release-qualification-execution-interruption-preview` is inert: it acquires the lock,
exactly revalidates the START-only run set and claimed activation set (refusing missing
START, any OBSERVATION, any RESULT, any existing terminal marker, unknown extra files,
tampering, cross-host/source drift, and symlink/hardlink/mode substitution), derives the
deterministic confirmation/interruption hash, and mutates nothing. The record command
requires `--confirm` plus that exact hash and then, before writing, proves the exact
claim-bound container is absent or removes only it by immutable container ID after exact
ownership/label/image/name verification (a foreign, mismatched, unprovable, or substituted
container fails closed with no artifact written and no unsafe removal). Both private
artifacts are written with `O_EXCL` and directory fsync. Crash after the artifact before
the marker permits only deterministic marker repair after fully revalidating the artifact;
if both exist replay is forbidden, and a marker without its artifact fails closed. After
interruption, execution is permanently non-retryable and the terminal state grants no
install, activation, publication, compatibility, external-effect, or promotion authority.


Both modes must show that the envelope's qualified functional commit is a real ancestor
of the exact packaged commit, that their complete delta is the required three ordinary
evidence files, and that the packaged source tree matches Git byte-for-byte with only its
separately bound generated SBOM added. The `owner-usability` evidence must come from a first-time owner who was not an
implementer: the owner follows the clean-host quick start, configures and operates the
reference profile without editing generated files, identifies the browser/terminal
authority split, completes the documented health/recovery/update journeys, and records
any assistance or ambiguity. Keep identities, paths, credentials, and private content in
the separately controlled source evidence; only its reviewed SHA-256 enters promotion.

Publication remains prohibited while any gate is blocked. In particular, selecting a
distribution license and obtaining provider-side historical-secret closure are owner or
repository-administrator decisions; Pixel does not fabricate them.
