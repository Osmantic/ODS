# Pixel local control

Pixel's local portal is a dependency-free, owner-only agent workspace for one Pixel
deployment. Its default view follows the normal chat workflow: recent tasks on the left,
conversation and execution state in the center, a persistent composer, and an optional
context inspector. The prior onboarding/status controls remain available as the secondary
control center rather than a separate workflow a chat user must discover. Start it from a
trusted Pixel source or release tree:

```bash
./pixel ui
```

Open the exact printed `http://127.0.0.1:43117/#review=…` address on the same machine. The listener is
IPv4 loopback only. `--bind` exists so the invariant can be tested, but every value other
than the exact address `127.0.0.1` is rejected. Use `--port` to select another unprivileged
local port.

When the exact private runtime is configured, the workspace can send a bounded message to
only the fixed local Pixel agent. It uses a fixed launcher shape and private message file;
the browser cannot choose a binary, agent, session key, path, environment value, or command.
The private conversation record is bounded, atomic, and hash chained. Process-lifetime
opaque handles replace retained conversation identities, caller request IDs make replay
idempotent, and startup converts an unfinished running turn to `interrupted`. Only bounded
message text and content-free turn/tool counts cross back to the token-gated page; raw
launcher output, session metadata, tool names, paths, hashes, and credentials do not.
Submitting through the workspace returns after durable acceptance and the page reconnects
to the same task through a bounded accepted/started/terminal event trail. A reload selects
the most recent retained conversation unless the owner deliberately chose **New task**.

When a Deep Work controller snapshot is present, the ordinary conversation stream shows its
content-free milestone progress, checkpoint continuity, verifier state, and artifact kinds.
If private semantic review is enabled, the owner may explicitly load a pathless artifact
inventory and short exact-review identity inline; the same authenticated detailed review can
open in the optional side inspector without transferring the user to the control center.
Relative evidence references and private findings remain local to that explicit review. The card grants no artifact-open,
acceptance, completion, or external-effect authority and does not claim that the selected
chat created the controller goal without an explicit handoff receipt. An offline retained
snapshot is visibly stale, while a degraded controller is shown as a service concern rather
than being hidden behind an otherwise healthy goal state.

A settled chat task can now enter the existing bounded authoring flow through **Plan as Deep
Work** without leaving the conversation. Pixel moves the one existing authoring form into an
inline workbench, pre-fills the owner-authored objective, and keeps the composer available,
but still requires explicit milestone, input, sensitivity, and independent-completion review.
It does not duplicate or bypass the backend authoring path. The ordinary fixed-action
permission mode governs draft creation and any required exact approval remains in the same
conversation at the point of consequence. On success, a private immutable receipt binds the exact settled
turn record to the exact inert draft and goal declaration; chat receives only process-local
opaque handles and a no-authority summary. The receipt survives restart, fails closed on
source, declaration, or receipt substitution, and labels a retained target separately from a
changed or unavailable authoring configuration. If the process stops after the exact inert
draft is durable but before its receipt is durable, restart reconstructs the receipt only after
the draft, source turn, and original authoring inputs all revalidate; the interrupted action
still remains visible for operator attention. Drafting still grants no lease, execution,
scheduling, service activation, external effect, scope expansion, or completion authority.

Fresh controller states also place only the lifecycle actions relevant to that state directly
on the conversation card: pause and safe cancellation while active, or resume and safe
cancellation while paused. Each control shows its effective owner permission or why it is
locked, and routes through the same parameterless fixed-action request used by the advanced
surface. Pause may follow its bounded automatic permission; resume and cancellation always
require a fresh exact approval. Stale observations disable the controls, while a fresh
degraded-service observation does not suppress an otherwise server-authorized containment
action. Every request is revalidated against current private custody; denial removes only the
exact hash-bound proposal and executes nothing. Recovery-inconclusive, failure, exhausted
budget, and no-progress states remain explicit and never imply retry or completion.

## Authenticated remote portal boundary (development)

`control/access-adapter.mjs` is the narrow origin adapter for the future authenticated
portal. It is not a generic reverse proxy and it is not a production-deployment claim.
Both the adapter and the control service still bind exact IPv4 loopback sockets. A local
Cloudflare Tunnel may reach only the adapter; the adapter forwards only an explicit
method-and-path allowlist to a separately bound control service started with
`--adapter-mode --review-token-file PRIVATE_FILE`.

The adapter validates the signed `Cf-Access-Jwt-Assertion` itself against the exact Access
issuer, cached Cloudflare RSA signing keys, expiry/not-before/issued-at bounds, a private
email allowlist, and one exact application audience. Ordinary workspace routes require a
workspace Access application audience. Configuration writes and exact action execution
require a distinct approval application audience intended for Cloudflare Access
independent MFA on every login. A consequence remains as a durable exact card in the
conversation workspace. Denial is a workspace-authenticated, hash-bound narrowing action;
execution opens the approval application and correlates its completion through a fresh
256-bit browser challenge before submitting the same action ID and hash. The approval page
never calls the private control service. Every approval application route is under the
single `/approve/*` prefix. The Access application's Cookie Path setting must be enabled so
its token remains scoped to `/approve` and distinct from the workspace application token.
The adapter strips browser cookies, the Access assertion,
identity claims, and every unrecognized header before crossing the loopback boundary. It
injects only its private control-session cookie and owner-only review token; neither is
returned to the browser. Unknown routes, query strings, cross-site mutations, transfer
encoding, oversized bodies, malformed/duplicate JSON security inputs, and arbitrary
methods fail closed.

`schemas/control-access-adapter-v1.schema.json` and
`control/access-adapter.example.json` define the private configuration. The workspace and
approval audiences must be different. The example contains placeholders only; never
commit a real Access audience, account identity, tunnel credential, review token, or
client hostname. The adapter foundation is automated-test qualified, while the release
manifest deliberately keeps `remotePortalDeploymentQualified` and
`browserApprovalAuthority` false until supported-host service confinement, live Access
policy evidence, and a clean deployment
rehearsal are complete.

`GET /api/v1/approvals` is owner-private and fail-closed. It revalidates every pending
record and projects only the fixed label, declared effect, expiry, action ID, and exact
hash. Parameters, revisions, paths, prompts, credentials, and private logs remain local.
`POST /api/v1/actions/cancel` removes only that exact ID/hash and executes nothing. Pending
cards survive navigation and a fresh authenticated page load until executed, denied, or
expired. At medium and mobile widths the inspector starts closed so it cannot cover these
inline controls.

`GET /api/v1/permissions` projects all fixed action classes with one enforced mode:
`always-ask`, `auto-within-policy`, or `never-allow`. The owner can edit only modes that
the private deployment policy already permits. The ordinary chat workspace edits these
defaults in its optional side inspector through the same protected save path; the full
control center remains an advanced status and recovery surface, not a required destination.
`auto-within-policy` is unavailable for
Deep Work resume and cancellation, cannot supply a command or new parameter, and runs only
through `POST /api/v1/actions/request`, which derives the mode and exact preview entirely
on the private server. A permission save is optimistic-revision bound, requires the
separate approval audience remotely, and denies every older pending proposal. Any change
to the underlying private deployment policy resets user modes to safe defaults until a
fresh exact selection is saved, preventing a latent automatic permission from reviving.

The page can also save credential-free onboarding choices, prepare configuration, build a
review plan, run the read-only deployment verification, and show bounded content-free
status, including Frontier provider mode, rolling budget use/limits/remaining, and local
action incident guidance. Pixel Doctor adds rounded local host-readiness and conservative
model/context starting guidance without executing a process or probing the network. An
owner-only policy can separately enable a public-registry update check,
encrypted backup creation, one-way emergency pause for Operations or Frontier, and the
fixed Deep Work lifecycle actions described below. It
can also enable on-demand inspection of pending, already-sanitized Frontier capsules. It
cannot activate a deployment, approve source/Operations/Frontier work, resume a broker,
restore a backup, decrypt recovery material, install an update, or run a caller-supplied
command. Those actions retain their existing operator boundaries.

## First-run journey

1. Run `./pixel bootstrap` and then `./pixel bootstrap --apply` on a supported host.
2. Run `./pixel ui` as the dedicated non-root deployment owner.
3. Open the printed loopback URL, review every section, and save the local settings.
4. If Frontier is enabled, choose ChatGPT plan or separately billed API access and a
   managed local safety budget. Then select **Prepare configuration**, review the stated
   effect and exact short-lived hash, and confirm it. This runs the fixed `configure`
   action without activating services or contacting a provider.
5. Complete credential or account authorization outside the browser when the selected
   limbs need it. The UI intentionally has no API-key, OAuth, SSH, provider-session, or
   backup-key fields.
6. Select **Build review plan**, inspect the resulting plan through the normal operator
   workflow, and activate it outside the browser with `./pixel apply --confirm`.
7. Select **Check health** or run `./pixel verify` after activation.

## Deployment proof

The portal labels an installed release **Active** only while a fresh, exact runtime
attestation is verified. `./pixel verify` first removes the prior receipt, checks the
installed manifest, regenerates and compares the clean-source identity, checks the live
source manifest and active configuration, performs the normal service/connector/gateway
checks, and only then atomically writes the owner-private `runtime-attestation.json`.
The receipt becomes stale after five minutes; skipped endpoint checks produce **Limited**,
and changed source, installed bytes, or configuration produce **Mismatch**. Missing,
malformed, unsafe, or source-unidentified evidence never falls back to an active claim.

The browser projection contains only versioned states, timestamps, short hashes, profile
names, and connector states. Provider and model IDs are hashed. It deliberately says
`gateway-verified-model-unproven`: deployment integrity and endpoint reachability are not
evidence that the selected model can complete Pixel's real-work qualification corpus.
`schemas/release-identity-v1.schema.json` defines the source/baseline record, while
`schemas/runtime-attestation-v1.schema.json` defines the private verification receipt.

## Frontier access and local budgets

The browser selects a route and local policy limits, not a credential. ChatGPT mode uses
eligible Codex access from the selected ChatGPT plan/workspace. API-key mode is separately
billed through the OpenAI API Platform; a ChatGPT subscription does not cover that API
bill. Authentication is completed outside the page and the browser has no credential,
session-import, login, or provider-call endpoint.

The built-in managed profiles are rolling 24-hour Pixel ceilings:

| Profile | Reviews | Input tokens | Output tokens | Failures |
|---|---:|---:|---:|---:|
| Starter | 5 | 50,000 | 10,000 | 2 |
| Balanced | 20 | 200,000 | 40,000 | 5 |
| Expanded | 50 | 500,000 | 100,000 | 10 |

These limits constrain Pixel's Frontier broker only. They do not change ChatGPT workspace
allowances or API Platform billing/spend controls, and they are not a guarantee that a
provider attempt cannot consume more than Pixel's estimate. A private custom policy is
shown as **Custom private policy — terminal managed**. When that private policy is
already selected, the page can draft new limits without displaying its path or any
credential. The proposal expires after 15 minutes and its SHA-256 binds the proposed
limits, current raw policy, authentication/billing mode, expiry, and hidden policy path.
It cannot be applied in the browser. Copy the displayed exact command into the trusted
terminal; for a UI launched with non-default `--state` or `--onboarding` paths, repeat
those options before `apply`:

```bash
./pixel frontier-budget apply --proposal-id frontier-budget-... --proposal-hash SHA256 --confirm
```

Application rechecks the proposal, current onboarding, exact source policy bytes,
schema-v2 broker policy, billing mode, expiry, and hash; creates an owner-only exact
backup; and changes only `budgets`. It does not run configure, plan, apply, restart,
authentication, approval, or a provider call. Run `./pixel configure --force`,
`./pixel plan`, and the normal exact deployment apply separately after reviewing the
change. A durable owner-only application claim is written before the policy edit. If the
terminal process is interrupted after that claim, repeat the same exact command: Pixel
accepts the expired proposal only to finish that already-claimed edit, verifies the
backup and either the prior or exact updated policy bytes, and never starts a second
independent edit. ChatGPT policies require subscription cost mode and no API cost field. API-key
editing requires a private metered price policy and explicit local cost ceiling; an
unpriced API policy stays terminal-edited. Pixel also rejects attempts to pair a private
policy with a managed page preset or an authentication selection that conflicts with it.

Status is explicit: **Off** means no provider route is active; **Prepared** means a
coherent generated provider-and-budget projection exists but terminal provider setup and
full plan validation are still required; **Active** means
validated live aggregate counters are available; and **Unavailable** means required
evidence was missing, malformed, or incoherent. A malformed active usage file never falls
back to a more reassuring prepared state. Generated-policy and live-broker evidence are
identified separately.

Existing private onboarding fields that are not part of the browser contract are
preserved byte-for-value at the JSON-field level when the owner changes a public setting.
For a first deployment that needs a non-default model credential, begin with the normal
private JSON onboarding workflow in `DEPLOYMENT.md`, then use the page for later safe
settings changes. Never paste a credential into a display-name, URL, host, or other text
field.

## Browser-visible contract

The versioned schemas are:

- `schemas/control-chat-v1.schema.json`: the token-gated bounded conversation projection
  with no path, credential, raw-launcher-output, or generic-command surface.
- `schemas/control-chat-turn-request-v1.schema.json`: one caller-identified message for
  either a new conversation or one exact process-lifetime opaque conversation handle.
- `schemas/control-access-adapter-v1.schema.json`: the owner-private, loopback-only
  Cloudflare Access origin-adapter configuration with distinct workspace and approval
  audiences; it defines transport authentication but does not itself grant an approval.
- `schemas/control-onboarding-v1.schema.json`: the exact credential-free settings object.
- `schemas/control-action-v1.schema.json`: an exact action preview.
- `schemas/control-status-v1.schema.json`: the content-free status projection.
- `schemas/release-identity-v1.schema.json`: exact clean-source, manifest, and matching
  baseline-qualification lineage without a runtime claim.
- `schemas/runtime-attestation-v1.schema.json`: the owner-private exact verification
  receipt projected through the status schema without model-capability overclaiming.
- `schemas/control-update-status-v1.schema.json`: bounded release-workspace and migration
  orientation without paths, hashes, signer identity, or private receipt content.
- `schemas/control-recovery-guide-v1.schema.json`: content-free backup and incident
  recovery steps with no restore or resume authority.
- `schemas/control-doctor-v1.schema.json`: rounded local host and model-fit guidance.
- `schemas/control-diagnostics-v1.schema.json`: bounded content-free incident summaries
  and fixed operator guidance.
- `schemas/control-frontier-review-v1.schema.json`: the opt-in exact sanitized Frontier
  review projection.
- `schemas/control-work-semantic-review-v1.schema.json`: the opt-in private Scout,
  Researcher, or Data Lab candidate and verifier review projection. It can contain
  owner-authorized work content but no host-absolute path, credential, or browser action.
- `schemas/control-frontier-budget-request-v1.schema.json` and
  `schemas/control-frontier-budget-proposal-v1.schema.json`: the bounded custom-budget
  draft and exact terminal-apply handoff.
- `schemas/work-operator-status-v1.schema.json`: the owner-private controller-to-control
  snapshot for content-free Deep Work orientation. The public endpoint narrows it again.

The local API exposes only:

| Method and path | Result |
|---|---|
| `GET /` | Self-contained local page and a random per-process session cookie |
| `GET /app.js`, `GET /styles.css` | Fixed local assets |
| `GET /api/v1/onboarding` | Public settings, opaque revision, and boundary flags |
| `POST /api/v1/onboarding` | Optimistic-revision save of the exact public schema |
| `GET /api/v1/permissions` | Owner-private fixed-action modes and deployment-policy locks |
| `POST /api/v1/permissions` | Protected optimistic-revision save that invalidates pending proposals |
| `GET /api/v1/status` | Content-free product, limb, queue, routing, provider-mode, and budget status |
| `GET /api/v1/update-status` | Read-only release-workspace state, bounded counts, migration guidance, and no update authority |
| `GET /api/v1/recovery-guide` | Content-free backup validation/rehearsal and incident recovery/resume guidance |
| `GET /api/v1/doctor` | Rounded host readiness and advisory local-model guidance |
| `GET /api/v1/diagnostics` | Content-free local-action incident state and fixed guidance |
| `GET /api/v1/deep-work` | Read-only, content-free durable-goal, session, progress, aggregate usage, service, artifact-category, verifier, boundary, and activity orientation |
| `GET /api/v1/reviews/deep-work` | Private-policy- and process-token-gated, read-only exact semantic candidate, relative evidence, artifact inventory, and verifier-limit review |
| `GET /api/v1/reviews/frontier` | Private-policy-gated, read-only pending sanitized Frontier capsules |
| `POST /api/v1/frontier-budget/preview` | Store one expiring exact private custom-budget proposal; no apply or provider authority |
| `POST /api/v1/actions/preview` | Expiring SHA-256-bound preview of one allowed action |
| `POST /api/v1/actions/request` | Backend-decided ask/automatic/deny path for one fixed action request |
| `POST /api/v1/actions/execute` | Single-use execution of that exact preview |
| `GET /api/v1/actions/ACTION_ID` | Content-free result for one local action |

There is no generic path, file-read, prompt, URL-fetch, environment, process, shell, or
command endpoint. The always-available action kinds are `configure`, `plan`, and
`verify`. A private policy may enable `update-check`, `backup-create`,
`operations-pause`, `frontier-pause`, `deep-work-pause`, `deep-work-resume`,
`deep-work-cancel`, and `deep-work-draft`; the Operations and Frontier actions require an
exact bounded reason, while Deep Work lifecycle actions accept no browser parameter.
The update-status projection inspects only a strict, owner-controlled private workspace
shape. Unexpected names, links, ownership, permissions, or inconsistent lifecycle sets
produce an unavailable result. It never reads or verifies receipt contents and cannot
prepare, activate, roll back, recover, clean up, or migrate a release.

The recovery guide accepts only exact retained action results and incident receipts. It
reports backup-creation outcome, required terminal validation/rehearsal, open incident
counts and fixed next-step codes. A successful pause record never becomes a claim about
current broker state. The page cannot read an artifact, identity, recipient, pause reason,
private log, or evidence hash and cannot decrypt, rehearse, restore, recover, or resume an
Operations or Frontier broker. A separately enabled Deep Work resume remains limited to
the exact privately reviewed paused checkpoint described below.
Action output is written to a private local log and is never returned to the page. The result
contains only the fixed action kind, success state, timestamps, exit code, log digest,
and a fixed message.

The Deep Work view accepts one bounded, private, atomically published controller snapshot.
It shows the enduring goal as a content-free aggregate: state, verified milestone count,
started-job count, durable checkpoint sequence, local runtime, model and token usage,
failure count, and one fixed next-step code. Those facts are reconstructed from the
immutable goal ledger rather than worker narration. The checkpoint sequence is presented
as evidence of restart-safe custody, not as proof that useful progress occurred; milestone
completion still requires independently verified child evidence.
It also states that progress is driven by durable events and that the watchdog is liveness
recovery only. Per-session special-tool status is reduced to a fixed state and count; the
browser never receives capability or pack identities, schemas, arguments, results, paths,
hashes, or any ability to invoke a tool.

The view rejects unknown fields, links, invalid permissions, impossible goal, progress, or
artifact totals, inconsistent completion/verification, invalid time order, changed privacy
flags, or any enabled control in the status snapshot. The control service removes opaque session/event
handles; the page receives no objective, prompt, tool argument, filename, path, hash,
credential, provider content, or work authority. A snapshot older than two minutes is
displayed as offline. The status endpoint remains read-only. Separately policy-gated Deep
Work lifecycle action endpoints exist, but there is no approval, boundary-expansion,
evidence, artifact, caller-supplied path, or generic command endpoint.
The service defaults to `/var/lib/pixel-work-controller/operator-status/status.json`; an
operator may select a different owner-private snapshot with `--work-status` for an
isolated deployment or qualification run. This selects a read-only input and grants no
new browser authority. A supervised goal whose private controller `stateRoot` is
`/var/lib/pixel-work-controller` publishes to that default path after every successful
cycle. Other state roots must use the matching `--work-status` path. No successful cycle
means no heartbeat refresh, so the view changes to offline after two minutes.
The long-term goal card includes a collapsed remaining-capacity ledger. It shows exact
limits, settled-plus-active-observed use, and remaining jobs, runtime, local model calls,
input/output tokens, network bytes, artifact bytes, and failures. A single `current`
session marker binds active checkpoint usage into those totals. Both the controller
projection and the local-control server recompute the arithmetic; inconsistent, missing,
duplicated, or behind-ledger usage is unavailable rather than displayed optimistically.

The semantic candidate review is a distinct private view, not a widening of that
content-free heartbeat. Set `views.deepWorkSemanticReviews` to true and launch the control
service with `--work-controller-config PRIVATE_CONFIG`; the exact process-lifetime URL is
also required. Each request invokes only the fixed `goal-review-cli.mjs`, reopens and
validates the current waiting Scout, Researcher, or Data Lab candidate, verifies its exact
checkpoint/report/verifier/artifact bindings, and returns at most 1 MiB of normalized UTF-8
review content. Scout local quotes and paths are relative to admitted input IDs, Researcher
quotes use opaque retained source IDs, and Data Lab entries use derived `artifacts/` paths.
Host-absolute paths and credentials are rejected. The browser renders every string with
`textContent`. A malformed, linked, changed, stale, oversized, or unexpected projection
fails closed. This view can reveal private work content to the local browser, extensions,
screen sharing, and anyone holding the launch URL while the process lives, so enable and
load it only when that exposure is acceptable. It has no mutation endpoint or accept
button; `./pixel work-accept accept` remains a fresh exact trusted-terminal decision.

Deep Work has three separately policy-gated lifecycle actions: pause future scheduling,
resume one exact paused checkpoint, and safely cancel one settled goal. They are absent
unless their individual `actions.deepWorkPause`, `actions.deepWorkResume`, or
`actions.deepWorkCancel` flag is true, `./pixel ui` received an owner-private
`--work-controller-config`, that file is safely readable, and the fresh content-free goal
state admits that transition. Preview accepts no reason, path, identifier, checkpoint, or
other caller parameter. Every confirmation hash binds the private policy and controller
configuration revisions. Resume and cancellation additionally run the existing read-only
trusted review and privately bind its exact review hash and transition mode; execute reruns
that review and refuses any changed checkpoint or child custody. Execute copies the exact
configuration bytes to an owner-private single-use snapshot before invoking only the fixed
pause, resume, or cancel CLI. The snapshot is removed after completion and on crash
recovery. The browser never receives either path, revision, review hash, file content, or
command output. Changed, missing, linked, over-permissive, offline, stale, or ineligible
state fails closed. Pause cannot stop a bounded step that already won its start race.
Resume launches no work and grants no new lease or retry. Cancellation accepts only an
inactive goal, an admitted but unlaunched child whose lease can be atomically revoked, or a
started child with terminal supervised cleanup evidence; it cannot stop or hide a live
worker. Failure creates a content-free incident backed by the private log.

Deep Work also has a separate, disabled-by-default guided authoring boundary. The owner
first admits local folders with `work-input-pack`, then creates an owner-private
`control-work-authoring-config-v1` file from `control/work-authoring.example.json`. That
configuration fixes the work policy, input catalog, object store, draft store, and bounded
draft retention outside browser control. Start `./pixel ui` with
`--work-authoring-config PRIVATE_CONFIG` and set `actions.deepWorkDraft` to true in the
owner-only control policy. The exact process-lifetime URL printed at startup is required
before the private authoring endpoint or action preview will respond.

The wizard shows only enabled Scout, Builder, public Researcher, and Data Lab choices plus
generic, process-lifetime opaque input handles, classification, file size, and dataset
format/count. It never returns catalog IDs, dataset names, paths, object names, hashes,
  policy contents, or credentials. An owner can describe up to sixteen milestones, choose
  zero or more already-declared prerequisites for each later milestone, choose admitted
  inputs and a quick/standard/deep ceiling, and supply independent "done when" checks. The
  UI defaults each new milestone to the preceding one, but supports bounded branches and
  convergence. The server accepts only unique, canonically ordered references to earlier
  milestones, making cycles, forward references, unknown nodes, and self-dependencies
  impossible at this boundary. Public research requires a public goal and receives no local input. Data
analysis requires an already admitted dataset. The server translates handles back to exact
private catalog IDs, rejects classification downgrade and stale state, and binds the brief
plus current private configuration, policy, and catalog into the normal expiring
exact-confirmation action.

Confirmation invokes only `goal-draft-cli.mjs` with server-fixed private paths and
owner-private single-use brief, policy, and catalog snapshots. The result is an inert atomic
draft named by the control action ID inside the fixed draft store. No file admission, compilation, lease,
ledger, controller, service, staging, scheduling, execution, network call, egress approval,
policy change, scope expansion, or completion claim occurs. The single-use snapshots are removed,
output remains private, retention fails closed, and private command output stays in the
local log.

With a separately enabled `deepWorkPrepare` action and owner-private
`--work-launch-config`, the token-gated review card can prepare exactly the retained draft
whose opaque handle, authoring revision, and displayed review SHA-256 were confirmed. The
fixed launch configuration supplies the private controller environment, draft store, launch
store, and retention ceiling; none can be supplied or changed by the browser. Execution
rechecks the draft, authoring inputs, private launch configuration, environment bytes,
retention, and duplicate-draft custody after confirmation, then invokes only
`goal-launch-prepare-cli.mjs prepare`. Competing or stale confirmations fail closed. A
successful result is an atomic private package with compiled milestones and expiring
single-use leases, but it remains inactive: no ready goal ledger, schedule, worker, service,
provider call, external effect, or completion claim exists. Inspection, staging, service
rendering, and activation remain separate trusted-host steps.

With the further distinct `deepWorkStage` flag, the same card shows a pathless exact launch
manifest digest, child/profile summary, earliest lease expiry, and dormant-stage receipt
presence. A second confirmation reopens the complete package and invokes only
`goal-launch-prepare-cli.mjs stage` with its fixed private directory and displayed digest.
The underlying stage operation is idempotent under retries and competing confirmations: an
existing exact stage is revalidated, while substituted or corrupt custody fails closed.
Staging writes expiring child custody and one dormant ready checkpoint, but starts no worker,
timer, controller service, model request, provider call, external effect, or completion
action.

With a separately enabled `deepWorkServiceRender` flag and owner-private
`--work-service-config`, the staged package card can render one exact inactive supervisor
bundle. The server revalidates the draft, launch package, stage receipt, fixed controller
configuration, and fixed service configuration after confirmation, then invokes only
`goal-service-cli.mjs render`. The retained four-file set contains the exact service-bundle
manifest plus path, timer, and service units bound to the staged goal and fixed host
identities. The browser sees only an opaque handle, exact manifest digest, inactive state,
event-driven execution model, and liveness-only watchdog role. It cannot choose paths or
accounts, install units, reload systemd, enable or start a service, schedule work, consume
a lease, call a model/provider, create an external effect, or declare completion. Unit
installation and activation remain distinct trusted-host boundaries.

The same pause remains available directly as `./pixel work-pause --config PRIVATE_FILE`.
It rereads the owner-private controller configuration and publishes the paused heartbeat.
Trusted resume uses the same underlying boundary: `./pixel work-resume review` creates a
content-free, checkpoint-bound confirmation hash and `./pixel work-resume apply` rechecks
that exact paused state before enabling future controller cycles. Neither command adds a
new lease or launches a child directly.
Trusted cancellation follows the same review/apply boundary. Its review distinguishes an
inactive goal, an exact admitted-but-unlaunched child eligible for atomic lease revocation,
and a consumed child that still needs stop/cleanup evidence. Apply binds the exact current
parent, child, run custody, and cancellation review. Revocation and launch share one atomic
slot: a revocation winner records a zero-use terminal child, while a consumption winner
remains active until supervised cleanup. The command publishes the resulting content-free
heartbeat but cannot stop a worker. The opt-in browser action can invoke only this exact
review-confirmed command.

Private-knowledge mutation deliberately does not enter the browser action API. The owner page
shows only content-free health and instructions; it receives no source text, title, local path,
vault key, query, or deletion authority. `./pixel work-knowledge-guide` is the plain-language
operating surface for setup, add, find, remove, offline rotation, and historical reconciliation.
It presents one bounded terminal-safe review, requires one short digest-derived confirmation
phrase, and invokes the same full-hash core review/apply boundary. This closes the guided
journey without turning a browser compromise into a private-vault or key compromise.

The v1 schemas are strict: unknown fields fail closed. Until Pixel 4.0 is tagged they are
candidate interfaces. After that promotion, removing a field, widening browser authority,
or changing an existing field's meaning requires a new schema/API version; a new optional
projection should use a new version rather than silently widening v1.

## Request and execution boundary

The server enforces all of the following:

- one process per private state directory, using an owner-only operating-system lock;
- a random per-process `HttpOnly; SameSite=Strict` cookie;
- a separate random per-process review-view bearer delivered only in the
  terminal-printed URL fragment, removed from browser history state, and required as an
  exact header for the content-bearing review endpoint;
- exact local Host validation to resist DNS rebinding;
- same-origin mutation requests, JSON content type, one content length, no transfer
  encoding, duplicate-key rejection, finite JSON numbers, and a 64 KiB request ceiling;
- no CORS response and a restrictive Content Security Policy with no inline or remote
  scripts, styles, images, fonts, objects, frames, or form targets;
- 120 requests per minute per process, at most 64 pending previews, five-minute preview
  expiry, at most 100 retained results, at most 100 incident receipts with 20 returned,
  at most 20 private 15-minute Frontier budget proposals, and a 32 MiB private-log ceiling;
- owner-only, single-link, regular private JSON and log files with no-follow reads;
- an allowlisted environment and fixed argv with no shell or browser-supplied path;
- one fixed action executing at a time, including across distinct preview IDs;
- a 1 MiB command-output ceiling and per-action runtime ceiling;
- exact-once claiming before execution, restart recovery for interrupted actions, and an
  immutable owner-only snapshot of the onboarding input used by `configure`;
- opaque onboarding, private-policy, and generated-deployment revisions bound into every
  action hash and rechecked immediately before the action is claimed.

On supported Linux hosts, private control directories are mode `0700` and records are
mode `0600`. Browser responses deliberately omit local paths, action IDs from aggregate
status, command output, prompts, account details beyond the explicitly editable public
onboarding fields, and credentials. The opt-in review response is the sole exception for
provider job IDs and capsule text; it exposes only the exact sanitized review contract.

Pixel Doctor reads only fixed local operating-system and kernel facts plus Pixel's fixed
generated deployment file. It starts no process, accepts no caller-supplied path, performs
no network probe, and makes no provider call. CPU, memory, storage, and configured context
are reduced to broad tiers; accelerator output is only a vendor class; and the response
omits hostnames, serials, device names, model/provider identifiers, URLs, paths, exact hardware
values, and process output. Missing model configuration is distinguished from an unsafe or
unreadable configuration, which fails the readiness summary closed. The local-model class and context guidance are conservative
starting points, not proof that a particular model, quantization, context, or accelerator
memory allocation will fit. `./pixel doctor --json` emits the same strict projection;
ordinary `./pixel doctor` prints a plain-language version and never changes the host.

Each failed fixed control action creates an owner-only receipt bound to the exact action
result and private-log digest. The diagnostics endpoint rebuilds only an allowlisted
category, severity, state, time, independent incident identifier, evidence-availability
flag, and fixed next-action code. It never returns the action identity, evidence digest,
log content, local path, prompt, account, or credential. A later exact successful action
of the same kind marks earlier receipts resolved and binds that transition to the
successful result; the resolution remains stable when old results are pruned. Missing
required receipt coverage or changed, linked, oversized, malformed, or mismatched retained
evidence makes the whole projection `unavailable` with null counts and no incident rows.
An intentionally retention-pruned log is instead reported as unavailable private evidence.
A live or incompletely recovered action claim also remains unavailable rather than
appearing clear.

Active Frontier budget status is copied only from the broker's already content-free aggregate:
provider/auth/cost mode plus rolling job, input-token, output-token, failure, and estimated
cost totals, ceilings, and remaining capacity. Before activation, the page can instead
project only strictly validated limits from Pixel's generated policy; used and remaining
values stay unknown. Essential counter, policy, authentication, and billing contradictions
make the whole Frontier state unavailable. Optional individual values still fail to
`null`. No raw broker or policy object is passed through.
The page shows approval counts but cannot approve a private Calendar proposal, Operations
plan, or Frontier capsule. Frontier review display is disabled by default. When enabled,
it accepts only bounded `awaiting-approval` result files with a fixed job ID, strict
capsule schema and instructions, matching metadata, and a recomputed canonical capsule
hash. It returns at most 20 newest reviews in a response no larger than 1 MiB after
considering no more than 1,000 result names. Arbitrary result fields, private plan paths,
policy, requests, replacement maps, credentials, accounts, provider output, and broker
authority state are not returned.

The UI renders every capsule string through DOM `textContent`, never HTML. It displays
the exact `frontier-show` and `frontier-approve` terminal commands but has no approval
button or execution path. The broker remains responsible for rechecking the private plan,
request, policy, capsule, expiry, cancellation, cache, budget, and exact plan hash before
one transmission. A browser extension or screen-sharing participant can read displayed
sanitized content, so enable this view only when that exposure is acceptable.
The source, Operations, and ordinary Frontier approval wrappers fail closed outside a
real terminal or under root/passwordless sudo. They pin the system command path and
administrator binary, invalidate cached authentication, require fresh password-backed
administrator authentication, show the complete protected object, then require an
unpredictable hash-bound phrase and invalidate the new timestamp on normal exit. Protected JSON is
rendered with non-ASCII and control characters escaped. This is the separate
human-authenticated approval boundary; the browser's command text and `--confirm` flag do
not satisfy it. The fixed-synthetic live-qualification flow retains its separate,
single-use consent contract.
The ordinary session cookie is intentionally treated as CSRF protection, not proof of a
local human. A session cookie, base URL, stale launch URL, wrong token, or a token from
another control process cannot read the review endpoint. The review token is never in an
HTTP response, cookie, command argument, environment variable, or server log. It exists
in service memory and briefly in the browser fragment before JavaScript removes that
fragment; stop the process if the printed URL or browser profile may be compromised.

## Private operator policy

Copy `control/policy.example.json` to the default owner-only policy path and edit it from
a trusted terminal:

```bash
install -m 600 control/policy.example.json \
  "${XDG_CONFIG_HOME:-$HOME/.config}/pixel-control/policy.json"
```

All operator actions and review views default to `false`. Enabling `backupCreate` also requires a canonical
absolute backup directory and a real public age recipient. Neither is returned to the
browser. The control service passes its exact private policy path into the existing backup
workflow. That workflow revalidates the destination, captures only its declared
private-state roots, signs and encrypts without a plaintext archive, and refuses overlap
with captured state. `updateCheck` contacts only Pixel's fixed public npm registry
metadata origin and cannot prepare or activate a candidate. Emergency pause invokes only
the existing fixed broker pause command; resume remains terminal-only so a compromised
page cannot restore egress or execution authority.

The `views.frontierReviews` and `views.deepWorkSemanticReviews` switches add only their
fixed read endpoints; neither adds an action kind. The latter is the only one of these
views that may render owner-authorized private work content, and it remains separately
off by default. The policy is mode `0600`, no-follow, exact-schema data and is included in Pixel's signed,
encrypted private-state backups. Its opaque revision is included in every action hash and
rechecked immediately before claiming an action. A policy edit,
symlink, hard link, broad permissions, unknown field, noncanonical path, invalid recipient,
disabled flag, or unconfigured target limb fails closed. Use `--policy` only when an
operator intentionally keeps the same contract at a different owner-controlled path.

The service is a local software security boundary, not a physical air gap. The Python
process is part of the owner's trusted computing base and can read the private onboarding
file in order to preserve fields the browser cannot see. A malicious owner process,
browser extension, compromised kernel, or code already running as the deployment owner
is outside this boundary. Use a dedicated non-root account, keep the source/release tree
owner-controlled, and do not expose or reverse-proxy the listener.

## Private state and troubleshooting

By default, control state is in
`${XDG_CONFIG_HOME:-$HOME/.config}/pixel-control` and canonical private onboarding is in
`${XDG_CONFIG_HOME:-$HOME/.config}/pixel-deployment/onboarding.json`. Override them only
with the explicit `--state` and `--onboarding` options from a trusted local terminal.

The state directory contains pending previews, content-free results, owner-only incident
receipts, private command logs, custom-budget proposals and pathless application receipts,
and the single-instance lock. A crash converts a claimed
action into a failed, content-free result and bound incident receipt on restart, then
removes any orphaned configuration-input snapshot. If either durable write cannot finish,
the claim is retained for another startup attempt and diagnostics stays unavailable.
Do not publish the state directory or attach its logs to an issue without reviewing and
sanitizing them. Command output may contain local operational details even though the
browser never receives it.

If the page says settings or policy changed, reload it before continuing; another local
operation updated an exact input revision. If an action expires, preview it again. If a
second instance is rejected, close the already running local-control process; an ordinary
crash releases the operating-system lock automatically.

Emergency pause is containment only. Diagnose and resume through `./pixel ops-resume` or
`./pixel frontier-resume` after following `INCIDENT-RESPONSE.md`; the browser has no
resume path. Backup restore and rehearsal still require the offline age identity outside
the page. Credential rotation and break-glass authority also remain outside.

## Qualification

Local-model qualification remains outside the browser. After
`./pixel work-model-qualify` creates a private receipt, `./pixel work-model-policy review`
checks its exact identity, profile coverage, expiry, and measured envelope against a
private Deep Work policy. `apply` requires the reviewed proposed-policy hash and writes a
new private policy rather than overwriting the existing one; it changes no enablement,
budget, or authority. `status` emits only readiness, profile coverage, expiry, and measured
token ceilings and exits non-ready when evidence is absent, stale, substituted, linked, or
incoherent. The page has no endpoint for qualification, policy binding, or Deep Work
enablement.

The release gate validates the strict schemas and release manifest, Python and JavaScript
syntax, HTTP/session/origin/framing behavior, exact action binding, concurrency, expiry,
recovery, queue and retention ceilings, immutable configure input, private permissions,
content-free projections, incident/result/log tamper detection, durable interruption and
resolution evidence, rounded Doctor boundaries and deterministic recommendation bands,
no-follow reads, accessible labels/keyboard/reduced-motion and responsive declarations,
and the absence of browser credential or generic-command fields.

`security-evals/control-pressure/fuzz.py` repeats 7,885 hostile onboarding, malformed JSON,
custom-budget, review-result/capsule tampering, queue saturation, retention, replay, credential-canary, fixed-action, and rate-limit
cases without contacting a model or provider. The supported-host release matrix remains
the final promotion gate.
