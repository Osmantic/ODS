# Pixel

Pixel is a local-first autonomous agent runtime and deployment system for a single owner. Its
job is to carry an owner-approved objective through durable, checkpointed work to a verified
outcome: delegating across local models, remote model providers, and private machines; using
brokered files, commands, browsers, APIs, mail, calendars, and other tools; recovering across
crashes and restarts; and retaining auditable evidence for what happened. Capability-scoped
sandboxes, dedicated credential custody, fixed egress routes, exact approvals for
consequential or ambiguous effects, tamper-evident journals, and fail-closed reconciliation
constrain that work without turning the agent into a read-only assistant.

Pixel builds on a pinned OpenClaw baseline and adds its own goal, fleet, provider, tool,
control, release, rollback, and recovery planes. Those surfaces do not all become trusted by
existing in the source tree: each stays disabled until explicitly configured and authorized,
and candidate-only capabilities remain labeled separately from supported ones. This
repository is the complete sanitized deployment source, not one owner's live agent state; it
contains no client secrets, tokens, SSH keys, sessions, host pins, or personal memory.

Pixel's source is included in ODS under an ODS-only use and distribution grant;
see [LICENSE.md](LICENSE.md). It is not Apache-2.0 or a standalone open-source
product. Third-party components retain their own terms.

## Clean-host quick start

Supported deployment hosts are Ubuntu 24.04 LTS and Debian 12.

```bash
git clone https://github.com/Osmantic/ODS.git ods
cd ods/vendor/pixel
./pixel bootstrap                 # inspect requirements
./pixel bootstrap --apply         # install pinned requirements if needed
./pixel doctor                    # review rounded local hardware/model fit
```

Choose the credential-free local page:

```bash
./pixel ui                        # save settings and choose Prepare configuration
```

Or use the advanced private JSON workflow:

```bash
cp onboarding.example.json onboarding.json
${EDITOR:-vi} onboarding.json
./pixel configure --answers onboarding.json
```

Then continue the operator-owned setup (skip services that are not enabled):

```bash
./pixel services up --confirm     # reference profile only
./pixel authorize                 # when email or Calendar is enabled
./pixel source-broker --confirm   # when any source limb is enabled
./pixel ops-broker --confirm      # when Operations is enabled
./pixel frontier-broker --confirm # when Frontier review is enabled
./pixel plan                      # or choose Build review plan in the local page
./pixel apply --confirm           # atomically install the reviewed plan
```

`./pixel ui` prints a loopback-only URL for Pixel's primary owner workspace. The default
surface is a chat-and-task shell with recent conversations, visible run state, a persistent
composer, and an optional inspector; onboarding, evidence, budgets, recovery, and advanced
policy remain in its secondary control center. Chat is available only through the exact
process-lifetime review URL and invokes one fixed configured local Pixel agent. The browser
can submit bounded message text and receives bounded user/assistant text plus content-free
turn/tool state; it receives no launcher output, host path, session file, credential, generic
command, or arbitrary-agent selector. Each caller-generated request identity is retained in
tamper-evident private custody so a repeated request cannot rerun the turn, and an interrupted
running turn is recovered as interrupted rather than falsely answered.

The same workspace supports credential-free onboarding, review-plan preparation, and
status. A separate owner-only policy can enable a public update check,
signed encrypted backup creation, one-way emergency pause, and fixed Deep Work pause,
checkpoint-bound resume, and safely settled cancellation actions when a private controller
configuration is separately supplied. A distinct disabled-by-default Deep Work review view
can show an exact waiting candidate and the limits of its deterministic verifier under the
process-lifetime launch token; it is read-only and exact acceptance remains terminal-only.
Credential provisioning, authorization, deployment
activation, approvals, backup restore/decryption, update activation, and Operations or
Frontier authority resume remain outside the browser. See
[CONTROL-SURFACE.md](CONTROL-SURFACE.md).
For a new managed Frontier setup, the page defaults to eligible ChatGPT plan access and
the conservative Starter local budget. It can instead select separately billed OpenAI
API access, but it never accepts either credential. Sign-in or key provisioning remains
in a trusted terminal. Pixel's local limits are an additional stop; they do not replace
ChatGPT workspace limits, eligible plan allowances, API Platform billing, or provider-side
spend controls. For an already selected private custom policy, the page can create a
15-minute exact budget proposal. Applying its ID and SHA-256 still requires `--confirm`
in a trusted terminal, creates an exact private backup, changes only the source-policy
budget block, and makes no provider call; configure, plan, and deployment apply remain
separate. An unpriced API policy cannot use this safe editor. A private custom policy
cannot be silently overridden by a page preset.

`./pixel frontier-live-qualify` provides the separate release-qualification path. It
creates a short-lived consent record, prepares one fixed synthetic public capsule without
provider use, and prints exact inspection and confirmation commands. Only the final
`confirm ... --transmit` step may use a provider. ChatGPT plan/credits and separately
billed API Platform access are distinct modes; API mode additionally requires fresh
metered policy evidence and an explicit worst-case cost ceiling. See
[FRONTIER-LIVE-QUALIFICATION.md](FRONTIER-LIVE-QUALIFICATION.md).

The status view distinguishes Frontier being off, prepared with coherent generated
provider/budget settings but awaiting external setup and plan validation, active with
live broker totals, or unavailable because evidence could not be verified. It
includes only aggregate provider/auth/billing mode and rolling budget usage, limits, and
remaining capacity—never prompts, job IDs, accounts, or credentials.
Pixel Doctor reports only broad CPU, memory, free-storage, accelerator-vendor, host-support,
container-readiness, and generated-model configuration/context tiers. It offers a conservative local-model and context starting
point without starting a process, probing the network, or contacting a provider. It never
projects a hostname, serial number, device name, model identifier, provider URL, local path, or exact hardware value, and
its model-fit recommendation is advisory rather than a compatibility guarantee.

`./pixel work-model-qualify --config PRIVATE_JSON --output NEW_PRIVATE_JSON`
runs Pixel's fixed credential-free benchmark against an already-running local
OpenAI-compatible llama.cpp, Ollama, vLLM, or explicitly generic server at an exact
`http://127.0.0.1:PORT` origin. The owner-private configuration pins the
model artifact, backend image/version, accelerator class, prompt contract, tool contract,
context window, response ceiling, timeout, and receipt lifetime. Thirteen sequential cases
test exact short and sustained structured output, a 128 KiB long-context sentinel with
backend-observed token usage, recovery
discipline, exact usage, and profile-specific tool selection/arguments. The command never
uses an API key, follows a redirect, retries a failed case, or prints model/backend details;
it creates one owner-private receipt, prints only a content-free summary, and exits `2`
when no profile qualified. Bind it without hand-editing hashes:

Qualification is measurement, not runtime admission. A receipt grants no authority. The
contained Deep Work runtime currently admits llama.cpp and vLLM only after an exact matching
receipt is bound into private policy. OMP still speaks its pinned llama.cpp-compatible
facade exclusively to Pixel's per-job proxy; for vLLM, that proxy fixes bounded low reasoning,
suppresses reasoning disclosure, forces exact streaming usage, and is the backend's only job
peer. Ollama and generic-compatible receipts remain measurement-only until their contained
runtime adapters are separately implemented and qualified.

```sh
./pixel work-model-policy review --policy PRIVATE_POLICY.json --receipt PRIVATE_RECEIPT.json
./pixel work-model-policy apply --policy PRIVATE_POLICY.json --receipt PRIVATE_RECEIPT.json \
  --output NEW_PRIVATE_POLICY.json --confirm-proposed-policy-sha256 REVIEWED_SHA256
./pixel work-model-policy status --policy NEW_PRIVATE_POLICY.json
./pixel work-model-policy enable-review --policy NEW_PRIVATE_POLICY.json
./pixel work-model-policy enable-apply --policy NEW_PRIVATE_POLICY.json \
  --output NEW_ENABLED_PRIVATE_POLICY.json --confirm-proposed-policy-sha256 REVIEWED_SHA256
```

Review rechecks the exact model/backend/prompt/tool identity, measured envelope, expiry,
and every policy-enabled profile. Apply must receive the reviewed proposed-policy hash,
writes a new owner-private file, refuses overwrite and linked inputs, sets only the local
model's prepared state and qualification binding, and cannot enable Deep Work, a profile,
or a service. Status is content-free: it reveals readiness, eligible/required profiles,
expiry, and measured token ceilings but no model, backend, accelerator, path, hash,
credential, prompt, or response. It exits `2` for a non-ready state. Every fresh or continued worker rechecks that exact
current receipt before materializing work, while cleanup-only recovery needs no model.
The per-job proxy binds the admitted receipt, requires backend-observed usage, and clamps
context and output to its measured envelope.

Enablement is a separate exact review/apply boundary. It refuses an unprepared, expired,
tampered, profile-ineligible, empty, or already-enabled policy. Apply writes a new
owner-private file and changes only top-level `enabled` from `false` to `true`; it cannot
alter profiles, tools, budgets, security, runner identity, or qualification. The command
does not install the result, start a model or service, route work, deploy, or perform an
external effect. Installing that exact resulting policy is the separately governed step
that makes its already-reviewed profiles eligible for contained execution.

Before enabling a goal controller, inspect the actual backend that its private environment
names:

```sh
./pixel work-model-backend inspect --config PRIVATE_BACKEND_CONFIG.json
```

This read-only command remeasures the selected model bytes and any configured executable
runtime-cache seed, rebuilds the exact launch, then
inspects Docker directly and returns only content-free readiness. It requires the exact pinned
image, command, inherited-plus-safe environment, resource ceilings, restart/log behavior,
model mount, bounded temporary filesystems, one running read-only unprivileged container, the exact
private non-attachable internal network with no extra peer, no unexpected or non-loopback
published port, read-only mounts, and policy-bound labels for the provider, hashed model ID,
model artifact, backend version, accelerator class, prompt contract, and tool schema. CPU
backends must have no GPU or direct device grant. NVIDIA backends must use exactly one
canonical Docker GPU request; direct host devices, mixed/count-plus-ID grants, unknown
request fields, and accelerator classes not yet implemented by the contained Docker runtime
fail closed. The command does not start, stop, pull, connect, or modify anything and exits
`2` when the backend is unavailable or incompatible.

Pixel can also prepare that backend without hand-writing a Docker command or mutating the
daemon. Copy either `deploy/work-controller/model-backend-llama.example.json` or
`model-backend-vllm.example.json` into owner-private storage, bind it to the private
controller environment and selected local model bytes, then run:

```sh
./pixel work-model-backend review --config PRIVATE_BACKEND_CONFIG.json
./pixel work-model-backend render --config PRIVATE_BACKEND_CONFIG.json \
  --output NEW_PRIVATE_LAUNCH.json --confirm-launch-bundle-sha256 REVIEWED_SHA256
```

Review walks and hashes the actual selected bytes through no-follow file descriptors. A
GGUF identity is its raw SHA-256; a materialized vLLM directory identity is the canonical
manifest of every relative filename, byte count, and file SHA-256. Empty trees, unsafe
names, symlinks, hard links, non-files, ownership/mode violations, races during hashing,
and policy/hash drift fail closed. Review exposes only its confirmation hash. Render
remeasures everything and writes a new owner-private inert bundle containing exact Docker
argument vectors for a stopped container and private network; the backend subnet is explicit,
private, and disjoint from the per-job worker subnet. It does not execute those
vectors. The bundle pins read-only model mounts and root, a non-root UID/GID, dropped
capabilities, no-new-privileges, private IPC/shared memory, resource limits, disabled logs,
no image pulls, policy identity labels, and either no device or an exact counted NVIDIA
grant. vLLM tensor parallelism must equal that GPU count. vLLM requires a separately measured,
backend-image/fingerprint-bound runtime-cache seed. Only its declared vLLM, Triton,
SparkInfer, and TileLang subtrees are mounted read-only beneath an owner-mapped bounded
ephemeral compile-cache tmpfs; logs and cache misses remain private and ephemeral. A live
mutable production cache is never trusted directly. Every seed file is no-follow measured,
must be readable by the non-root container identity, and is covered by the confirmed launch
hash. Model bytes stay read-only, Hub access is
offline, ambient proxies are disabled, and usage telemetry is opted out. Loopback publication is optional
and defaults off. Actual network/container creation and startup remain a separate lifecycle
that must remeasure and revalidate this bundle before mutation. Review and execute it
explicitly:

```sh
./pixel work-model-backend start-review --config PRIVATE_BACKEND_CONFIG.json
./pixel work-model-backend start --config PRIVATE_BACKEND_CONFIG.json \
  --confirm-lifecycle-sha256 REVIEWED_START_SHA256
./pixel work-model-backend stop-review --config PRIVATE_BACKEND_CONFIG.json
./pixel work-model-backend stop --config PRIVATE_BACKEND_CONFIG.json \
  --confirm-lifecycle-sha256 REVIEWED_STOP_SHA256
./pixel work-model-backend halt-review --config PRIVATE_BACKEND_CONFIG.json
./pixel work-model-backend halt --config PRIVATE_BACKEND_CONFIG.json \
  --confirm-halt-sha256 REVIEWED_HALT_SHA256
```

Start never pulls an image. It reconciles an absent, network-only, exact dormant,
loading, or already-ready backend; rejects foreign names, bytes, labels, mounts,
environment, resources, devices, ports, networks, and peers; and succeeds only after an
exact pinned-image health event or bounded `/health` observation. The configured startup
limit is a monotonic safety deadline, not a schedule or substitute for that event. A
failed new start removes only resources whose exact creation it can prove; an indeterminate
Docker response is re-inspected and retained as a coherent resumable state when ownership
cannot be proven. Stop rechecks the exact runtime and refuses while a worker peer is
attached, then removes only that container and its empty private network. Both operations
are idempotent, content-free, remeasure before action, and attest private inputs and runtime
again before returning. If loopback publication is requested, readiness also requires the
effective Docker binding; recording only the requested `HostConfig` is insufficient.
Start, ordinary stop, and every worker proxy attachment share one owner-private,
crash-recoverable coordination lock. The lock covers only the exact inspection-and-attach
critical section, not a worker's long-running task. This prevents a new proxy from attaching
between stop's final peer check and Docker mutation without serializing independent work for
its full duration. `halt-review`/`halt` is the distinct recovery path when the selected model
storage or pinned image is missing or damaged: it reads only the private configuration,
environment, and policy, binds the current exact container observation into a one-time
confirmation, stops that statically identity-labeled container even when its hardened boundary
has degraded, and retains the container and network for forensics. It removes nothing, never
pulls an image, reports whether active worker peers may be interrupted, and cannot be used to
restart or otherwise expand authority.

For a newly prepared goal controller, set `modelBackendLaunchPath` in the private
controller environment before rendering the backend launch, render the reviewed launch to
that exact path, and then prepare the controller. Controller preparation verifies the
launch against the same environment, policy, Docker path, backend names, and artifact
identity. It propagates only the expected policy/environment/configuration/artifact binding
hashes and makes exact binding mandatory for that controller's workers. A runtime-label or
backend-network substitution then fails before a proxy can attach. Controllers prepared
without this optional migration input remain usable for compatibility but do not claim the
stronger exact-binding assurance and should be regenerated before client release.

The opt-in `tests/work-model-backend-lifecycle-live.mjs` qualification requires a clean
source tree, the already-present pinned llama.cpp image, and the public
`ggml-org/tiny-llamas` `stories15M-q4_0.gguf` artifact at revision
`99dd1a73db5a37100bd4ae633f4cfce6560e1567` (19,077,344 bytes, SHA-256
`6151b1929d7f5aa3385d9ddef3393e55587c0a55de661562322bc51dfda93a04`). It
performs one fixed non-user inference inside the private backend, removes the exact
disposable runtime, and emits a schema-validated receipt bound to the source commit/tree,
Docker versions, public artifact, image digest, readiness, generated-content observation,
and cleanup. It is lifecycle proof, not a model-quality benchmark.

The page also shows bounded local-action diagnostics: content-free incident categories
and fixed next steps backed by private tamper-detecting receipts. Action identities,
logs, evidence hashes, paths, prompts, accounts, and credentials stay out of that
projection; incomplete required receipt coverage or changed retained evidence makes
diagnostics unavailable rather than falsely clear. An operator may separately enable exact-
confirmed Deep Work scheduling pause, resume, and safe cancellation with
`actions.deepWorkPause`, `actions.deepWorkResume`, and `actions.deepWorkCancel` in the
owner-private control policy and by launching `./pixel ui` with the private
`--work-controller-config`. All three default off. The page receives neither that path nor
its contents. Pause stops only future supervised milestones; resume launches no work and
can preserve only the exact paused custody; cancellation is admitted only for an inactive
goal, an atomically revocable unlaunched child, or a child with terminal supervised cleanup
evidence. None can stop or hide a live worker or expand authority. An operator may also enable an
exact read-only candidate view with `views.deepWorkSemanticReviews`. Unlike the ordinary
content-free dashboard, that token-gated view may display owner-authorized private report
text and relative evidence references; it returns no host-absolute paths or credentials
and has no browser acceptance or completion action. An operator may also enable an
inert long-goal wizard with `actions.deepWorkDraft` and an owner-private
  `--work-authoring-config`. The exact startup URL then exposes only generic opaque summaries
  of already admitted inputs and enabled work types. The wizard supports a bounded acyclic
  branch-and-converge milestone graph, defaults new milestones to a simple serial chain, and
  permits dependencies only on earlier milestones. Exact confirmation creates a private
policy-derived draft; it cannot pick paths, admit data, compile, stage, schedule, run,
approve egress, or widen authority. The token-gated page then reconstructs a pathless exact
review card from the retained draft bytes, including objectives, dependency graph, criteria,
budgets, tool envelope, verifier limits, and canonical review digest. A malformed retained
set fails closed; paths, input identities, job IDs, and credentials remain hidden. The
private fixed-path example is `control/work-authoring.example.json`. A distinct
disabled-by-default `actions.deepWorkPrepare` action plus `control/work-launch.example.json`
lets an exact-confirmed review card call the existing atomic launch preparer. It creates an
inactive package with expiring single-use leases, but no ready goal state, schedule, worker,
service, provider call, external effect, or completion authority. A further disabled-by-default
`actions.deepWorkStage` action displays the pathless package manifest digest and can
idempotently create or revalidate dormant ready custody for that exact package. Staging
still starts no worker, timer, service, model request, provider call, external effect, or
completion action.
An independently disabled `actions.deepWorkServiceRender` action plus
`control/work-service.example.json` can then render the exact staged goal's systemd path,
timer, and service units into a new private bundle. The portal receives only the bundle
manifest digest and inactive state. Rendering does not install units, reload systemd,
enable or start a service, schedule work, consume a lease, invoke a model/provider, or
grant external-effect or completion authority.
An operator may also enable an
on-demand view of the exact already-sanitized capsules awaiting Frontier approval. It is
off by default, exposes no private plan or
provider credential, and remains inspection-only. Open the exact process-lifetime URL
printed by `./pixel ui`; approval still uses the exact terminal command.
Source, Operations, and ordinary Frontier plan approvals require more than `--confirm`:
Pixel refuses non-terminal, root, or passwordless-sudo approval, clears any cached
administrator authentication, requires fresh OS administrator authentication, shows the
complete protected object, then requires a one-time hash-bound phrase and clears the new
authentication timestamp on normal exit. Keep the deployment owner's sudo policy password-backed;
the separate fixed-synthetic live-qualification consent flow is unchanged.

Signed releases use a four-step, terminal-only update boundary: inspect, privately
stage, rehearse without executing candidate code, then preview an exact activation
hash. `./pixel update-activate --preview ...` is read-only. Repeating its complete
hash with `--confirm` creates a non-replayable private claim before running the fixed
configure, plan, and transactional apply sequence. Private onboarding data is read
locally, never placed in update receipts, and the browser cannot activate an update.
Activation receipts contain hashes, versions, phase, and rollback availability, but
no local paths or configuration content. `./pixel update-rollback --preview ...`
derives a second exact hash bound to that activation and its unchanged rollback
marker. Repeating the complete hash with `--confirm` creates a one-use claim before
the trusted current controller restores the preceding release; candidate code never
runs during rollback. If a process stops after the active release changed but before
its receipt was written, `./pixel update-recover --preview ...` reports only the
content-free interruption state. Its exact-hash finalize mode can write the missing
activation or rollback receipt; it cannot rerun candidate code, use the network, or
resume a partial deployment.

After a successful activation and exact rollback, the retained, previously activated
release controller may run `./pixel update-reactivate --preview ...`. The preview
revalidates the original signature, staged and rehearsed source, activation claim and
result, successful rollback claim and result, restored active version, and consumed
rollback marker. Repeating its distinct complete hash with `--confirm` creates a new
single-use private claim before the same transactional apply sequence safely re-adopts
the byte-exact retained release and creates a fresh rollback point. The original
activation and rollback receipts remain immutable and cannot be replayed.
`./pixel update-reactivation-rollback --preview ...` binds a second single-use
rollback to that exact reactivation result and fresh marker. If the terminal is
interrupted after either reactivation or its rollback changes live state,
`./pixel update-reactivation-recover --preview ...` can only diagnose or finalize
the missing content-free receipt; it cannot execute candidate code or resume work.
The ordinary update cleanup command fails closed while a reactivation workspace
exists; a later dedicated cleanup transaction must bind that second journey.

After an update has been successfully rolled back and the restored version is still
active, `./pixel update-cleanup --preview ...` derives one exact cleanup hash. Repeating
that complete hash with `--confirm` atomically quarantines only the verified bundle copy,
rehearsal copy, and completed activation workspace, writes a path-free audit tombstone,
and then deletes those quarantined copies. Installed releases and the active deployment
are outside the cleanup boundary. An interrupted cleanup is resumed from its private
claim and tombstone; it never reruns candidate code. Cleanup is terminal-only and is not
available from the browser.

If bounded staging is full and every ordinary cleanup candidate is excluded, the trusted
operator may use `./pixel update-archive --preview ...` only for one exact terminal failed
rollback with no reactivation workspace. The preview hashes every file and directory in
the candidate, rehearsal, and activation roots, rejects links, foreign ownership, special
modes, extended attributes, an active candidate, or its live rollback marker, and
binds the fixed owner-private same-filesystem archive destination. Repeating the complete
`archiveHash` with `--confirm` moves those three roots by atomic no-replace renames,
preserves the failed receipts byte-for-byte, fsyncs every custody boundary, and proves the
candidate count changed from eight to seven. An interruption resumes from the immutable
replay-tombstone claim and manifest; no evidence is deleted and the archived candidate ID cannot be staged
again.

A separate `./pixel update-reactivation-archive --preview ...` path is deliberately narrower. It is available
only at the exact eight-candidate boundary for a terminal failed reactivation that has no live-mutation marker,
retry journal, rollback artifacts, or deployment change. The operation requires a newer exact production-signed
controller, binds its source/package/signature and operator bytes into the preview hash, and moves all four
candidate/rehearsal/activation/reactivation roots into the fixed private same-filesystem archive only after an
interactive exact-hash confirmation. The sole legacy no-marker bridge is fixed to the exact 4.3.15 source identity
that exposed the capacity defect; every other ambiguous legacy result is rejected.

The loopback-only page also provides a read-only update and migration orientation view.
It checks the private filesystem shape of candidate, rehearsal, activation, rollback, and
cleanup workspaces and shows only version labels, bounded counts, interruption state, and
a fixed safe next step. It does not read or project private receipt content, does not claim
that a signature or migration is valid, and cannot activate, roll back, recover, clean, or
migrate a release.

Its recovery guide turns exact retained control receipts into a content-free backup and
incident checklist. It distinguishes backup creation success from the still-required
terminal signature/decryption validation and isolated rehearsal, and treats a successful
browser pause only as a reason to inspect current broker state. Backup artifacts,
decryption identities, private evidence, restore, recovery, and authority resume never
enter the browser.

Create the per-client Google OAuth app before the authorization step; see
[CLIENT-ONBOARDING.md](CLIENT-ONBOARDING.md). Verify at any time with `./pixel verify`.

## Deployment profiles

- `prepared`: the client already has private SearXNG and an OpenAI-compatible model.
- `reference`: Pixel supplies hardened localhost SearXNG and can optionally start an
  operator-provided GGUF model through Docker Compose.

Both profiles use a versioned installation under `PIXEL_INSTALL_DIR/releases`, an
atomic `current` symlink, an isolated Source Broker identity, a hardened gateway system service,
an isolated Operations Broker identity, an isolated Frontier Broker identity, an isolated Web Courier system service,
signed age-encrypted private-state backups, transactional gateway-credential rotation,
and automatic rollback if post-apply or post-restore verification fails.

Capability profiles (`minimal`, `chief-of-staff`, `research`, and
`engineering-operator`) choose a starting limb set. Every limb can then be independently
enabled or disabled in client onboarding. See [CUSTOMIZATION.md](CUSTOMIZATION.md).

The gateway itself runs as a hardened system service under the unprivileged client
account. `ProtectHome=tmpfs` hides the rest of that account's home; only the OpenClaw
state, workspace, embedding cache, and pinned runtime are mounted into its namespace.
Optional channel or custom plugins must be listed explicitly in `gatewayExtensions`.
Unlisted agents, plugins, tool grants, channels, bind mounts, and memory paths are
removed during planning instead of being inherited from an older OpenClaw config.
Pixel-pinned optional plugins use `{ "id": "discord" }`; arbitrary ambient plugin IDs
are rejected. A custom plugin also requires an absolute read-only path and the tree
digest printed by `./pixel extension-hash /absolute/path`;
planning and apply both fail if any file or symlink differs. Any custom tool names must
also be listed explicitly and match the pinned plugin manifest contract; loading a
plugin alone does not grant its tools to Pixel.
Installed agent skills are also unrestricted by OpenClaw unless configured. Pixel sets
an explicit `agentSkills` allowlist; the default `[]` injects no ambient skills. Add a
name only after reviewing the copy supplied by the pinned runtime or client workspace.
Session list/history/send remain available for useful subagent coordination, but their
visibility is pinned to `tree`: the current session and sessions it spawned. Direct
requests for an unrelated session key fail at the gateway.

## What is and is not replicated

The repository reproduces Pixel's code, policies, workspace structure, integrations,
and deployment lifecycle. A new owner gets a clean identity and memory. OAuth consent,
model access, client-specific answers, and private state are deliberately provisioned
per deployment and never copied from the source Pixel instance.

## Operator map

| Path | Purpose |
|---|---|
| `pixel` | Single command surface for bootstrap through rollback |
| `control/` / `schemas/control-*-v1.schema.json` | Loopback-only credential-free onboarding, status/diagnostics, opt-in sanitized review, and exact safe-action surface |
| `scripts/limb-kit.py` / `schemas/limb-pack-v1.schema.json` | Signed, disabled-by-default offline projection-limb authoring and lifecycle preview |
| `scripts/client-kit.mjs` / `schemas/client-overlay-v1.schema.json` | Private client overlays that keep negotiated terms, client state, MFA policy, and extension selections outside the signed golden core |
| `profiles/` | Infrastructure and modular capability profiles |
| `deploy/` | Reproducible sandbox, brokers, runners, Web Courier, and optional reference services |
| `plugin/` / `plugin-ops/` / `plugin-frontier/` | Projection-only source, Operations, and privacy-compiled review tools |
| `schemas/frontier-request-v2.schema.json` / `schemas/frontier-policy-v2.schema.json` | Adaptive local-attempt and broker routing/cost/cache contracts |
| `schemas/frontier-integration-v1.schema.json` | Content-free receipt for exact-result-bound local critique and finalization |
| `security-evals/` | Live prompt-injection and tool-trace regression harnesses |
| `workspace-template/` | Sanitized identity, policy, memory, and helper skeleton |
| `tests/` | Static and clean-room plan/apply/verify acceptance tests |
| `RELEASE-MANIFEST.json` | Only authored source for release, runtime, artifact, and image pins |
| `OPENCLAW-COMPATIBILITY.json` / `.md` | Machine-readable qualification states and generated readable table |
| `QUALIFICATION-MATRIX.json` / `QUALIFICATION.md` | Supported-host, model-capacity, and fail-closed promotion contract |
| `OPENCLAW-UPSTREAM-QUALIFICATION-PLAN.md` | Implementation plan for qualifying and promoting new OpenClaw releases |
| `UPSTREAM-INTAKE.md` | Read-only registry discovery and verified private candidate preparation |
| `scripts/run-upstream-runtime-matrix.sh` | Disposable Ubuntu/Debian container and systemd-VM qualification |
| `scripts/run-supported-host-systemd-matrix.sh` | Exact-source product appliance qualification in disposable Ubuntu 24.04 and Debian 12 VMs |
| `schemas/supported-host-systemd-evidence-v1.schema.json` | Strict private evidence contract for real-systemd product lanes |
| `scripts/deep-work-endurance-probe.mjs` / `schemas/deep-work-endurance-evidence-v1.schema.json` | Required credential-free event-horizon graph and real-process crash/restart qualification |
| `scripts/deep-work-multi-day-soak.mjs` / `schemas/deep-work-multi-day-soak-evidence-v1.schema.json` | Optional resumable 48-hour, hourly-systemd, controlled-reboot appliance soak; not a promotion gate |
| `scripts/run-upstream-assurance.sh` | Two-pass source, clean-room, pressure, split active/legacy ref, and secret assurance gate |
| `scripts/run-upstream-canary.sh` | Isolated systemd canary, observation, and exact rollback rehearsal |
| `scripts/upstream-attestation.py` | Detached signing, independent evidence verification, and explicit promotion |

Start with [DEPLOYMENT.md](DEPLOYMENT.md), hand the client
[CLIENT-ONBOARDING.md](CLIENT-ONBOARDING.md), and use
[OPERATIONS.md](OPERATIONS.md) after launch. Fleet operators should also read
[OPERATIONS-LIMB.md](OPERATIONS-LIMB.md),
[OPERATIONS-AUTONOMY.md](OPERATIONS-AUTONOMY.md), and
[THREAT-MODEL.md](THREAT-MODEL.md). Frontier operators should read
[FRONTIER-LIMB.md](FRONTIER-LIMB.md) and
[FRONTIER-LIVE-QUALIFICATION.md](FRONTIER-LIVE-QUALIFICATION.md). Release reviewers should follow
[SECURITY-ASSURANCE.md](SECURITY-ASSURANCE.md) and
[QUALIFICATION.md](QUALIFICATION.md), and responders should use
[INCIDENT-RESPONSE.md](INCIDENT-RESPONSE.md). Maintainers evolving the OpenClaw baseline
should follow [OPENCLAW-UPSTREAM-QUALIFICATION-PLAN.md](OPENCLAW-UPSTREAM-QUALIFICATION-PLAN.md).
The exact discovery and quarantine workflow is in [UPSTREAM-INTAKE.md](UPSTREAM-INTAKE.md).
Candidate failures route through [UPSTREAM-FAILURE-GUIDE.md](UPSTREAM-FAILURE-GUIDE.md),
and release reviewers use
[UPSTREAM-RELEASE-CHECKLIST.md](UPSTREAM-RELEASE-CHECKLIST.md).
Planned, test-gated product milestones are recorded in [ROADMAP.md](ROADMAP.md).
The post-qualification client experience, real-backend proof rules, and promotion gates
are tracked in [PRODUCTIZATION.md](PRODUCTIZATION.md).
The Pixel 4.1 post-4.0 Deep Work candidate adds restartable, independently verified local goals and
a fair, resource-admitted multi-goal fleet inspired by the useful long-horizon patterns
reviewed in Dream Forge. `work-input-pack` safely turns explicitly selected local folders
into inert content-addressed snapshots and the private input catalog; `work-goal-draft`
then turns a small private goal brief into exact,
policy-clamped Scout, Builder, public Researcher, and replay-verified Data Lab proposals
plus one reviewable bundle. `work-goal-assemble` binds one exact reviewed draft and
owner-reviewed environment into atomic inert goal/controller bundles. The shorter
`work-goal-launch prepare` path performs that assembly and compiles every exact child into
one atomic inactive package from the same reviewed draft hash. It contains expiring
single-use leases, but creates no goal ledger and starts no worker, service, or schedule.
`work-goal-launch inspect` reopens the complete published package without mutation, and
`work-goal-launch stage` accepts only that exact manifest hash before creating dormant
ready custody. Neither operation starts work.
Its advanced operator commands are `work-fleet`,
`work-fleet-cleanup`, `work-fleet-host-evidence`, and `work-fleet-service`; see
[DEEP-WORK.md](DEEP-WORK.md) for the exact private-contract workflow and current release
gates. Its event-horizon probe now drives a repeated-diamond dependency graph and kills and
recreates controller processes after every durable child transition. Each supported-host
lane runs the bounded form; promotion requires the exact-source maximum of 64 milestones,
84 dependency links, 256 forced exits, and 385 fresh processes. A separate 48-hour
systemd/controlled-reboot soak remains available as optional appliance evidence, but
elapsed time is not work progress or a promotion gate. This candidate is not yet a
supported conversational full-auto mode.

`work-context` adds durable, checkpoint-bound private session creation, content-free
listing/inspection, explicit trusted-terminal display, new-job forking, and crash-safe
exact removal. `work-context-guide` wraps create, fork, and remove in a plain-language
review plus exact confirmation. A fork inherits only hash-bound context and lineage: it
cannot reuse a lease or approval, lower classification, or grant completion. Start with
`deploy/work-controller/context-session-input.example.json`; private content is never
returned by review, apply, list, or inspect.

Long-horizon means one objective pursued through a durable chain of attempts, evidence,
verification, and next decisions until its acceptance criteria reach a terminal state. A
checkpoint event immediately resumes bounded reconciliation. The low-frequency timer is
only a liveness watchdog for a missed event or interrupted supervisor; it never defines a
work cadence, creates progress, or authorizes another attempt. The optional 48-hour soak
tests restart durability across real elapsed time and is not the production execution model.
The local status view labels that distinction directly and shows content-free OMP capability
state per attempt (`not configured`, `configured`, `authorized`, or `recovery required`)
without exposing tool names, prompts, data, paths, or trust material.
Service inspection and activation preflight every selected pack's exact installed identity,
signed provenance, image admission, recent passing health, and policy lifetime before any
event-driven goal supervision can be enabled.
The disabled baseline and opt-in capability environment examples are separate, so copying
the ordinary setup never accidentally enables extra tools.

The disabled capability-pack declaration and image-admission path is available through
`./pixel work-capability-pack`. `inspect` validates an untrusted declaration; `sign` and
`verify` bind its canonical bytes to a separately provisioned Ed25519 publisher identity;
`install --confirm` makes an atomic owner-private copy; and `status` re-verifies every
installed copy against the current trust root. Declaration installation never pulls or
inspects an image. `image-admit --confirm` then admits only an already-local canonical
repository manifest digest using an empty credential-free Docker configuration. The signed
pack separately binds Docker's local image ID and exact Linux architecture. It validates
the fixed safe image environment and signed metadata,
creates but never starts a networkless hardened inspection container, streams the declared
executable through a bounded strict tar parser, verifies its exact size and hash, and
force-removes the container with an explicit absence check. The resulting image admission
is still disabled and initially has `health: not-probed`; it registers no tool and grants
no data, network, execution, external-effect, or completion authority. `image-status`,
`image-revoke-review`, `image-revoke`, `image-recover`, and `image-cleanup-recover` expose
content-free status and exact crash recovery. Revocation removes only Pixel's admission and
retains the host-owned image. Pack removal is separately exact-review bound, records durable
custody before deletion, and prevents same-version replay. One exact-pack mutation record
serializes inspection, revocation, and removal across crashes and concurrent callers.
`health-probe --confirm` is a separate disabled, supervised operation over that exact
admission. It starts a sterile disposable container only long enough to perform MCP
`server/discover` and `tools/list`; it issues no grant or tool call, receives no client data,
and cannot register or enable the tool. Pixel always force-removes the container, proves its
absence, and appends a chained content-free result. Three consecutive failed probes
quarantine that signed version. `health-status` is read-only, while `health-recover` resumes
only the exact interrupted cleanup or receipt publication. The permanent opt-in Linux gate
builds a networkless scratch fixture and proves the real sign/install/admit/health/status/
tool-call/revoke/remove lifecycle plus test-owned image cleanup. The Ubuntu 24.04 and
Debian 12 systemd qualification lanes make that gate mandatory and reject a skipped result.

The internal supervised runtime can now consume one separately issued expiring grant against
recent passing health and one checkpoint-bound watchdog decision. It durably burns the grant,
runs exactly one named tool, returns schema-validated structured output only to the live
caller, and retains a content-free terminal receipt. Container and optional disposable-
workspace ownership are inspected before removal, exact absence is proven afterward, and a
crash can recover cleanup but can never replay the call. Health age is an evidence-validity
limit and runtime time is a safety ceiling; neither schedules progress. There is no
general-purpose tool-execution CLI. A pure controller compiler
admits an exact pack only after the ordinary OMP lease is consumed and its matching `running`
checkpoint exists. It binds a private allowlist, exact job authorization, signed input schema,
classification, resource envelope, request, and complete watchdog-event head before issuing
one grant. Repeated events stop future grants even when they are far apart; elapsed time never
creates a call. A private single-flight queue now moves each raw request into durable custody,
stages successful output only inside job-scoped state, appends exactly one content-free event,
and publishes one response. Crashes before launch may resume; crashes at or after launch can
clean and settle but never replay. The registration bridge derives
one content-addressed, fresh worker catalog from that exact authorization and signed pack.
A trusted OMP extension registers only deterministic `pixel_cap_*` names for that job,
passes through the exact signed input/output schemas, serializes calls against the trusted
event head, and fails the session closed after any ambiguity beyond request publication.
It has no network, credentials, process-launch, ambient-registration, or grant authority.
Scout, Builder, Data Lab, and Researcher now route that catalog through three exact private
mounts only when the consumed job and its `running` checkpoint match. A host-side bridge
moves each request into durable controller custody, republishes only the exact settlement,
survives post-custody crashes without replay, and emits content-free lifecycle evidence.
Only the primary OMP worker receives the extension; exporters, replay verifiers, inventory
stages, independent verifiers, unrelated jobs, and profiles without an authorization do not.
Cleanup removes the exact settled bridge and refuses orphaned capability custody when its
recovery authorization is absent. A production goal controller can bind one reviewed signed
pack and uniquely sorted tool set to an exact immutable child job. Preparation copies and
hash-binds the capability policy into the private controller bundle, requires an empty
credential-free Docker configuration and current trust root, and checks the installed pack
plus recent passing health. Only after the ordinary one-use lease is consumed and its exact
`running` checkpoint is durable does Pixel derive the authorization and catalog. Their
content-free pack/policy/catalog custody is written atomically before worker setup, so
interrupted cleanup can recover the exact boundary without re-trusting a removed pack or
replaying work. Profiles with no job binding remain exactly capability-free. Capability-pack
operator setup/status and live OMP/Docker qualification on the exact candidate remain
closed gates, so this route is not Supported yet.

The disabled local knowledge vault now has a reviewed, resumable first-time setup, strict
external-key custody, exact consent/query/deletion lifecycle, checkpoint-bound local worker
injection, and tombstone-safe encrypted backup restore. `./pixel work-knowledge setup-review`
never mutates state; the exact-confirmed `setup-apply` creates the key without displaying it
and atomically publishes a deep-audited empty vault. `./pixel work-knowledge-guide` supplies
the plain-language setup, add, find, remove, rotation, and recovery journey without sending
private bytes or key authority into the browser. See [KNOWLEDGE-VAULT.md](KNOWLEDGE-VAULT.md).

## Multi-provider execution substrate

The provider substrate (`deploy/work-provider/`) binds every execution to a durable run
ledger and a closed model-selection policy, and routes only through statically registered
transports. It is disabled by default: remote providers require an exact owner-private
policy, credential custody, cost/token ceilings, and an exact-host egress proxy, and no
cloud-to-cloud fallback exists.

### Explicit model-selection policy

Every provider profile declares a closed `modelSelection` mode:

- `fixed` (Moonshot Kimi, OpenAI, Anthropic): uses exactly `profile.defaultModel`; the
  `model` field may be omitted.
- `owner-pinned` (OpenRouter, Together, Fireworks, Groq): the optional owner-private
  `model` field is required, must not be a placeholder/auto/provider-selected value, and must
  match qualification, router decision, run binding, request, and returned response model.
- `qualification-pinned` (local): the model is bound by the sealed qualification.

All validation flows through the closed helpers in
`deploy/work-provider/provider-registry.mjs`; there is no path that can execute the literal
`provider-selected-model` placeholder.

### Durable response-model binding

Every run-ledger request carries a `responseModel` that starts `null`. A successful
qualification-trial or semantic settlement/reconciliation must receive a validated exact
response model equal to `ledger.model`; connectivity-smoke may remain `null` only for
backward-compatible reachability, and non-success settlements cannot claim one. The response
model is persisted atomically before any response is disclosed, and promotion cross-checks
each content-free evidence `responseModel` and input hash/usage against the corresponding
ledger request (including the two-request tool case) rather than aggregate counters.

### Closed generic transports

`deploy/work-provider/generic-remote-transport.mjs` provides production transports with exact
static provider-ID-to-wire mappings and no caller-controlled URL/path/header for OpenAI
(`/v1/responses`), Anthropic (`/v1/messages`, fixed `anthropic-version: 2023-06-01`),
OpenRouter, Together, Fireworks, and Groq (each `chat/completions`). All use a proven
proxy-only CONNECT/TLS framing with exact owner-private host allowlists, custody handles,
strict UTF-8/JSON, response byte/time bounds, exactly one explicit response framing mechanism
(exact `Content-Length` or extension/trailer-free chunked encoding), provider-reported
model equality, exact token usage, credential zeroing, and deterministic failed-known vs.
uncertain network error classification. No arbitrary production exchange callback is exposed.
All transports are registered in the frozen `transport-registry.mjs`.

### Hermetic wire tests and equivalence harness

`tests/work-provider-transports.test.mjs` asserts the exact CONNECT host, TLS server name,
HTTP method/path, auth header, fixed headers, request JSON shape, adapter selection,
response-model check, usage, and failure classification for every provider against test-only
fake exchanges, including adversarial wrong host/path/auth/protocol/model, missing
usage/model, duplicate/conflicting framing, oversized/invalid UTF-8/JSON, and owner-pinned
placeholder models. No test contacts the network or reads a real key.

`deploy/work-provider/equivalence-runner.mjs` is a content-free CLI suitable for a later real
local and K3 equivalence run. It runs the genuine fixed semantic corpus cases
(`structural-review`, `patch-proposal`, `failure-triage`) through the production router,
semantic binding, durable executor, closed transport, and deterministic grader, while keeping
the qualification controls (`tool-choice-continuation`, `long-context-sentinel`,
`output-capacity`) in the qualification runner. The harness never mints a semantic
qualification itself: it requires the exact owner-private qualification artifact path and its
bound SHA-256 (`--qualification-report` / `--qualification-sha256`) produced by the sealed
qualification runner after the full trial attestation and closed durable run ledger, validates
its schema/hash, status, and lane/provider/model/corpus provenance, and passes that exact
promoted qualification to the router and binding. It accepts only exact owner-private
policy/credential/proxy/ledger paths where required and emits hashes, counters, and grades
without prompt or response text. Uncertain outcomes remain non-retried and non-rerouted.

`deploy/work-provider/qualification-runner.mjs` promotes a content-free owner-private
qualification artifact (`--qualification-report ABSOLUTE_PATH`, exclusive mode `0600`) that binds the
exact promoted semantic qualification to the qualified trial's qualification/evidence/ledger
hashes and lane, provider, model, corpus, trials, and status. The artifact is mandatory for a
successful (qualified) campaign and is written only when qualified. Artifact and evidence
reports use distinct, owner-private, create-once paths; the reader uses bounded no-follow
custody and strict duplicate-key-rejecting JSON. The equivalence runner refuses tampered,
missing, unqualified, wrong-lane/provider/model/corpus, and self-minted
envelopes. Production remote transports reject any caller-supplied exchange and always use the
single closed proxy exchange; hermetic tests inject fake exchanges only through the explicitly
named frozen `remoteProviderTransportTestOnly` / `moonshotTransportTestOnly` seams.

## Maintainer release generation

Edit supported release pins only in `RELEASE-MANIFEST.json`. The upstream preparation
command records an exact Candidate combination in `OPENCLAW-COMPATIBILITY.json`; manual
release changes must record the matching combination there. Then run:

```bash
node scripts/generate-release-files.mjs --write
node scripts/generate-release-files.mjs --check
```

The generator owns bootstrap/configuration constants, example environment pins, image
defaults, plugin package versions, `VERSION`, and the readable
[compatibility table](OPENCLAW-COMPATIBILITY.md). The release-contract gate rejects a
missing compatibility entry, invalid SHA-256/npm integrity, unpinned reference image,
stale generated output, or a release pin copied into another authored file. The manifest
also pins the exact Node binary used to make upstream runtime results comparable across
hosts; it is separate from the broader production minimum supported by `node`.

On a supported Linux packaging host, `./pixel package` also emits an unsigned
`pixel-VERSION.update.json` envelope binding
the archive, standalone SBOM, provenance, exact packaged source commit/tree, qualified
functional-source commit, release manifest, compatibility matrix, supported hosts, and
upgrade floor. The signer proves that the packaged source descends from that qualified
source through evidence-only changes; any later functional change fails closed. A
Candidate may first receive a separate `./pixel release-qualification-sign` signature for
exact-bundle review. Its distinct namespace and inspection path explicitly grant no
publication, staging, activation, update, or production trust authority. After every
non-waivable release gate passes and the exact compatibility record is `supported`, a
maintainer may sign that validated envelope with `./pixel release-sign` under the
production namespace.
Signing also proves the archive's complete source content and normalized modes match the
named Git tree, with only the separately bound generated SBOM added.
Users provision the publisher's allowed-signers file through an independent trusted
channel and run `./pixel update-inspect`, then explicitly `./pixel update-prepare` for an
eligible forward release. Inspection reads bounded single-link files and parses the
archive without extraction; preparation copies only those verified bytes into private,
deployment-locked staging with an immutable content-free receipt. Neither step extracts
or executes candidate code. `./pixel update-rehearse` then safely extracts into that
private boundary and runs fixed JSON/shell/JavaScript/Python syntax parsers plus exact
host and pinned toolchain checks; it never runs candidate programs, uses the network, or
changes the active deployment. See [UPGRADE.md](UPGRADE.md). Browser update preparation,
rehearsal, and activation remain intentionally absent.
