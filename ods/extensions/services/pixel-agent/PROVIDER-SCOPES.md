# Owner handoff preferences (candidate, not installed)

The Handoff scope panel, owner dashboard/host API and private POSIX store select
the configured compatible handoff recipient for task, conversation or newly
begun-task defaults. They do not activate routing, send a model request, approve
a checkpoint, change tool host/permissions, or change global ODS provider roles.

## Task and return semantics

- Begin task creates a distinct owner-supplied UUID, bound to the exact dashboard
  chat ID. A task can span multiple messages/runs. It ends only on End task.
  Every used UUID is retained against replay; a lost response requires readback.
- Task override wins over conversation override, then the default captured when
  this task began, then the existing configured leader. A native run never
  invents/completes a task and never copies a new default.
- Conversation choices survive task boundaries and browser reload. Resetting
  the new-task default affects only tasks begun after the reset. Existing tasks
  retain their captured default. An unregistered conversation does not receive
  a default merely because it runs inference.
- Return from task/conversation clears that layer only. The lower layer becomes
  eligible on the next run. End task clears its task choice and captured default
  while preserving the conversation choice. Reset new-task default is NOT a
  return of an existing task; the UI states that distinction explicitly.
- Changes never reroute or cancel a run already holding a frozen lease. The
  browser disables changes while its chat is sending; the host's revisioned
  boundary safely permits changes from another owner client for later runs.
- Provider revisions are pinned. Settings drift, disabled/incompatible targets
  and missing cloud consent block selection/use rather than silently accepting
  a changed recipient. Changing provider Settings requires explicit scope review.
- Cloud eligibility and unknown-cost consent do not replace each handoff run's
  full checkpoint approval. Every run has its independent deadline/cancellation.
  Persistent preferences are not an unlimited background/cloud authorization.

## Native identity and opt-in adapter

The existing ingress `computeSessionUser({user: chat_id})` hashes the EXACT UTF-8
chat ID to `ods-<sha256>`. The pinned OpenClaw HTTP handler then constructs
`agent:pixel:openai-user:ods-<sha256>` as **sessionKey**. This is NOT sessionId,
runId or a task UUID. Ingress case-sensitive hashing occurs before OpenClaw
lowercases its already hexadecimal key. Scope matching must not hash/lowercase
the raw browser ID differently.

An owner-qualified activation must set `ownerScopes: true` on BOTH
`createProviderRoutingBridge` and `createLeaseWorkerAdapter`. The bridge passes
the native sessionKey separately through the private worker frame. The worker
reads owner state once and constructs the existing checkpoint-gated handoff
lease. No client header/body can choose worker commands, paths or scope flags.
Direct handoffProviderId and scopeSessionKey are mutually exclusive requests.

Missing/noncanonical identity fails closed in this opt-in mode. Cron, other
agents, direct API users bypassing ODS ingress and native alternative channels
are not qualified by this adapter. Do not enable it globally until shared
admission and all applicable entry points are reviewed. Keep production default
off; this source does not change `index.js`, installers or installed config.
`PROVIDER-BOOTSTRAP.md` now defines the standalone composition factory and minimal
parent registration contract; it remains dormant until parent-owned integration.

Provider changes remain Pixel-only. Normal saved leader/backups and other ODS
consumers are unchanged. Per-run approval previews carry `selectionScope` and
`owner-scope-return-or-end`, so they do not falsely promise automatic next-run
return when a persistent owner preference remains in effect.

## Custody and API

`provider-scopes.json` uses the provider store's existing stable flock, strict
bounded JSON, private owner permissions, atomic replacement, fsync and revision
CAS. Recipient validation and preference commit share the same provider lock.
Maximum256 conversations/1024 retained task IDs; capacity fails closed. Recovery,
collection and native Windows storage are still required before broad release.
The same-UID malicious-process boundary is unchanged.

Authenticated owner POSTs at `/api/pixel/provider-scopes/{status,begin,end,select,return}`
forward to corresponding `/v1/pixel/provider-scopes/*` host routes. No public
resolver or checkpoint-publication endpoint exists. Responses explicitly report
`preference-only` and `required-each-handoff-run`, never active/installed state.
Browser consent clears on reload or scope change; ambiguous writes require
readback and are not automatically replayed. Scope state is server-owned.

## Qualification and joint fleet contract

Deterministic tests cover persistence, explicit lifecycle, default snapshots,
case-sensitive ingress identity, provider/scope revision drift, owner auth,
concurrent writers, cloud consent, unsafe storage and stale UI writes. The
opt-in real pinned gateway fixture runs with `ODS_HANDOFF_OWNER_API=1` and
`ODS_OWNER_SCOPE=task|conversation|default`; it proves selected handoff across
multiple runs, independent approval/denial, retained tool history and explicit
return. Its model responses are synthetic, not physical model acceptance.

First coordinated live target: parent worker's isolated ODS qualification guest
on Tower1. Parent retains exclusive ownership of native gateway, access bridge,
edge/native gates, installation and deployment. Feature lane owns scope/API/UI
source and private inference workers. Do not activate before the parent supplies
an exact installed runtime manifest, reviewed backup/rollback paths and an idle
single-writer handoff. Preserve current physical model pins and fleet services.

Joint acceptance: actual ODS browser Begin task -> choose authorized local model
-> ordinary Pixel message -> independently review checkpoint -> useful new tool
work using real local inference -> next message retains scope/history -> explicit
return -> configured leader continues. Inspect actual tool artifacts and model
requests, not prose alone; verify stop, reload, denial and unchanged provider
configuration. Then repeat conversation/default semantics and approved lifecycle
cases. Paid cloud and production privilege changes remain separately gated.

Source/isolated qualification, installed state and real operations must be
reported separately. Native platform/full access, install/upgrade/rollback and
the complete approved three-feature plan remain acceptance requirements.
