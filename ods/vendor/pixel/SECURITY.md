# Security policy

## Required deployment controls

- Use a dedicated Google Cloud project per client.
- Prefer an Internal OAuth audience for Google Workspace deployments.
- Enable only Gmail API and Google Calendar API.
- Request only `gmail.readonly` and `calendar.events`.
- Store OAuth files under the dedicated Source Broker system identity with mode `0600`.
- Do not expose Google token paths or raw source content to the gateway or Pixel plugin.
- Run `./pixel ui` only as the dedicated non-root owner from an owner-controlled Pixel
  tree. Keep its exact `127.0.0.1` bind, private state, session/origin/Host/framing checks,
  fixed action allowlist, output limits, and credential-free schemas intact. Never
  reverse-proxy it, bind it to a LAN address, add a generic command/file endpoint, return
  private logs to the browser, or move deployment activation, approvals, recovery keys,
  or credential entry into the page without a separately reviewed narrow broker.
- Keep optional local-control update-check, encrypted-backup, and emergency-pause actions
  disabled unless explicitly needed. Preserve the strict owner-only policy, exact
  preview/revision binding, and one-way containment boundary; the browser must never
  gain update activation, backup restore/decryption, approval, or broker-resume authority.
- Keep Deep Work browser drafting disabled unless the owner-private work policy, admitted
  input bundle, object store, draft store, and retention are deliberately configured.
  Preserve the process-lifetime launch token, opaque input handles, strict schemas, exact
  confirmation, and fixed `goal-draft-cli.mjs` dispatch. Never accept browser paths or raw
  file uploads, return catalog IDs/hashes/private logs, or add compile, stage, schedule,
  execute, egress approval, resume, scope expansion, or completion authority to this view.
- Accept a release update only with an independently provisioned allowed-signers file,
  the expected publisher identity, and a successful `./pixel update-inspect` receipt.
  Never take the trust root from the candidate bundle, sign a Candidate compatibility
  record, or treat safe inspection as activation authority. Keep release signing keys
  offline, owner-only, Ed25519, and separate from CI artifacts.
- Preserve the two-commit release boundary: qualification evidence names the exact
  functional source that passed, while the signed envelope names both that commit and the
  exact later packaged commit/tree. Signing must occur from that clean exact HEAD and the
  fixed compatibility-table/live-audit evidence paths must all differ after qualification
  and remain ordinary tracked files.
  Any code, configuration, schema, workflow, unknown, equal, or non-ancestor source fails
  closed; do not widen the evidence-only allowlist to make a release pass.
- Keep the signer's archive-to-Git proof intact. Every packaged source byte, file set,
  directory set, and normalized mode must match the exact signed Git tree; only the
  separately hashed generated SBOM may be added. Provenance labels alone are not proof
  that an arbitrary archive came from the named commit.
- Stage updates only through `./pixel update-prepare` under Pixel's deployment lock. Keep
  the staging root owner-only and outside browser authority; never execute or extract a
  staged candidate merely because its signature and content-free receipt validate.
- Rehearse only through `./pixel update-rehearse`. Preserve its no-follow safe extractor,
  owner-only normalized tree, exact pinned toolchain, clean parser environment, no-network
  rule, and explicit distinction between parsing candidate code and executing it. A green
  rehearsal receipt is still not activation authority.
- Keep Pixel's sandbox on `network=none`, with a read-only root and only the workspace
  writable.
- Run the gateway as the generated hardened system service under the unprivileged client
  identity. Keep its loopback/token flags, home masking, empty capability set, and
  explicit bind paths intact.
- Treat `gatewayExtensions` as executable-code approval. ID-only entries are limited to
  Pixel's release-pinned catalog; pin every other plugin to its absolute reviewed
  directory plus its `./pixel extension-hash` tree digest. Custom extension code cannot
  live beneath gateway-writable state, workspace, or cache paths. Never preserve an
  unknown plugin merely because an older config enabled it. Custom tools require an
  explicit namespaced `tools` list that exactly matches the pinned plugin manifest;
  they cannot overlap Pixel's managed Source, Operations, or Frontier namespaces.
- Accept customization packs only through `pixel limb-kit`: canonical schema validation,
  a dedicated Ed25519 allowed-signers trust root, exact tree locking, and a separate
  confirmed install/enable lifecycle are mandatory. Validation or signature-file presence
  alone is not trust. Packs remain disabled until explicitly added to private onboarding.
- Keep v1 generated limb workers offline and credential-free. Preserve their signed
  `PrivateNetwork`, empty capabilities, strict filesystem, dedicated non-login identity,
  gateway-user-only projection ACL, projection-only write path, bounded runtime, and
  baseline negative-test template. Disable must revoke the ACL and remove the worker
  identity. A request for network, credentials, raw-content retention, or arbitrary gateway
  code is a new broker design—not a manifest exception.
- Treat signed policy packs as bounded broker inputs, never as executable authority. Local
  packs are observe-only metadata tied to signed limb tools. Operations packs stay inert
  until the operator maps their placeholder to existing private target IDs; fixed helpers,
  namespace isolation, non-production standing grants, and per-file hashes are mandatory.
  Frontier packs may only restrict an existing typed task and can never enable or widen the
  base policy. Disable removes derived receipts and bindings; configure rechecks hashes,
  provenance, and restrictive composition before generating broker policy.
- Keep session list, history, and send visibility pinned to `tree`. This permits useful
  coordination with spawned subagents while the gateway rejects unrelated session keys.
  Use a dedicated deployment per client; do not use session keys as a tenant boundary.
- Keep Discord direct-message scope at `per-account-channel-peer`; allowlisted people
  must not share one transcript merely because they contact the same personal agent.
- Keep `agentSkills` explicit. An empty list is the secure default; an installed skill is
  model-facing executable instruction text and must be reviewed even though its actions
  remain constrained by Pixel's effective tool allowlist.
- Render the top-level OpenClaw configuration and default-agent block from Pixel's
  explicit contract. Do not preserve ambient hooks, browser/node settings, bindings,
  default workspaces, provider headers, or unknown future keys from an older config.
- Keep channel tools denied unless the client explicitly requests and approves them.
- Permit direct Calendar execution only for the broker's fixed private-create and
  time-only ETag-bound reschedule shapes. Require a broker-owned proposal snapshot and
  exact SHA-256 for deletes, attendee/invitation changes, content edits, and other shapes.
- Do not notify attendees unless the owner explicitly approves notifications.
- Keep the Web Courier in its hardened system service running as the unprivileged owner;
  do not grant the sandbox direct
  networking as a substitute.
- Keep Web Courier egress on the explicit public-web port allowlist (80/443 by default),
  public globally routable IPs, and canonical IDNA hostnames; do not widen it to private,
  transition-address, or arbitrary service-port scanning.
- Treat all search, fetched, rendered, and screenshot content as untrusted input.
- Keep Operations policy, SSH keys, host pins, approvals, and private spool state
  unreadable by the gateway owner and Pixel.
- Never add the gateway owner to the Operations Broker's authority-bearing group. Grant
  that user ACL access only to request, cancel, inventory, result, and event projections.
- Use named fixed-argv actions and dedicated runners for routine work. Treat raw shell
  as break glass and approve only the exact immutable plan hash outside Pixel.
- Keep the broker key's `restrict,command=` entry intact on every runner. The key must
  never regain an interactive or arbitrary remote shell.
- Keep `pixel-ops-transport` separate from non-login `pixel-runner`. Only transport may
  hold the managed-helper sudo rule; workload code must fail every direct sudo probe.
- Quarantine any target whose SSH identity changes; never bypass strict host checking.
- Permit Operations downloads over HTTPS only; retain public-IP validation, DNS pinning,
  TLS hostname checks, redirect source-scope enforcement, optional expected-hash
  enforcement, and non-executable quarantine.
- Treat machine output, logs, test output, and artifact metadata as untrusted input.
- Install Web Courier Python dependencies only from exact, all-transitive,
  SHA-256-hashed wheels with pip hash checking forced during download, preflight, and
  immutable release installation.
- Keep the Frontier provider key, policy, request archive, plans, approvals, authority
  state, and provider runtime outside the gateway identity. Keep both the provider-key
  directory and file root-owned; the broker group may read the file but the worker must
  not be able to replace it. The gateway gets write-only request/cancel paths and
  read-only result/event plus content-free aggregate usage projections.
- In ChatGPT-authenticated Frontier mode, keep the refreshable Codex `auth.json` inside a
  broker-owned mode-`0700` directory as a single-link mode-`0600` file. The broker may
  refresh that cache, but the gateway must not read it and the broker must not replace
  its root-owned parent directory. Treat it as a password in backups and incident work.
- Treat local-control Frontier authentication and budget fields as policy selectors only.
  Never add browser credential entry or login. Keep ChatGPT plan use distinct from
  separately billed API use, and reject managed presets that conflict with a private
  custom policy. Pixel budget ceilings supplement rather than replace provider billing,
  workspace, and spend controls.
- Keep Frontier requests local-first and typed. Never add a generic prompt, raw file,
  repository, message, URL, or log forwarding tool. Restricted data and mandatory
  never-egress categories must fail closed; confidential jobs require exact-plan approval.
  Require content-free enumerated routing receipts and keep aggregate usage projections
  free of prompts, identifiers, job IDs, credentials, and account details.
- Keep adaptive routing broker-owned and deterministic. Local sufficiency, retry, and
  missing-context outcomes must make no plan/provider call; safety/security reasons must
  force exact approval if work advances to spillover. Never let a model-authored estimate, receipt, or cache claim grant
  authority. Rejections, decisions, attempts, cache hits, quality, and savings telemetry
  must remain content-free.
- Keep the local Frontier review projection disabled unless the owner needs it. It may
  expose only the exact broker-sanitized capsule plus allowlisted hashes, classification,
  route, token, placeholder, and cost fields. Revalidate the capsule hash and schema on
  every no-follow read, render only with text nodes, and retain no browser approval or
  provider-call endpoint. Treat browser extensions and screen sharing as readers of the
  displayed sanitized content; use the terminal and exact broker plan for approval.
  Require a separate random process-lifetime review token from the terminal-printed URL
  fragment, remove the fragment from browser history state, never log or return the
  token, and reject a session cookie, duplicate header, stale token, or wrong token alone.
- Keep source, Operations, and ordinary Frontier approval wrappers human-authenticated.
  They must run on a controlling terminal, reset to the trusted system command path,
  invoke only `/usr/bin/sudo`, invalidate any prior administrator timestamp, reject root
  and passwordless-sudo policy, require fresh administrator authentication, show the
  complete protected object, require an unpredictable hash-bound confirmation, and invalidate
  the timestamp again on normal exit. Render protected JSON as ASCII-escaped text so
  directionality and terminal-control characters cannot alter the review. `--confirm`
  alone is not approval. Keep the administrator
  secret outside Pixel and its tools. Broker-side hash, policy, expiry, cancellation,
  replay, cache, and budget validation remains mandatory after this boundary.
- Keep Frontier cache and integration archives broker-private, single-link, mode `0600`
  beneath mode-`0700` directories. Bind cache entries to the exact sanitized capsule,
  normalized policy, provider/auth mode, model, output cap, and output contract; fully
  validate output on every reuse. Never cache approval-required work, forced safety or
  security review, or work while the quality circuit is open. Recheck cache and budgets
  after approval but before consuming it.
- Treat local finalization as the authoritative composition step, not as new provider
  authority. Validate its exact result hash and complete finding partition; archive only
  the exact result hash, salted local-output commitment, indexes, counts, verdict, and
  quality. Local conclusions, verification text, and commitment salt must remain in the
  gateway turn. Retain integration evidence no longer than its bound
  result and fail closed on missing or invalid evidence.
- Cost is a policy estimate, never an invoice. Metered mode requires operator-supplied,
  dated rates and a rolling ceiling; subscription and unavailable modes must stay
  explicitly non-monetary. Preserve provider-side budgets as the outer cost circuit.
- Keep live qualification deployment-owned, terminal-only, and synthetic. Authorization
  files contain consent but no credential, are short-lived mode-`0600` single-link files,
  bind exactly one authentication/billing mode, and are consumed once by hash. Accept no
  caller prompt, file, URL, account, or payload. `prepare` must stop at forced exact
  approval; only a second confirmation hash plus `--transmit` may execute. API mode must
  use pricing evidence no older than 31 days and explicit consent covering the 12,000
  input/256 output worst-case estimate under a one-dollar hard ceiling. Persist only a
  content-free receipt, and recover the same private claim without a duplicate attempt.
  The request-spool handoff is mode `0640` because the deployment operator and isolated
  broker are intentionally different Unix identities; its schema accepts only Pixel's
  fixed public synthetic capsule. Qualification preflight and receipt stdout are exact,
  content-free schemas and exclude credentials, account details, prompts, responses, job,
  provider, and model identifiers.
- Keep supported-host appliance qualification on a private runner and inside disposable,
  manifest-fingerprint-pinned VMs. Pass no host credential environment into a guest; use
  only loopback synthetic model/search fixtures; retain detailed evidence outside Git;
  refuse existing VM/evidence targets; and remove only the runner's bounded VM names.
- Run Codex ephemerally with ignored user config/rules, an isolated credential home,
  read-only sandbox, strict structured output, and shell, web, browser, computer,
  image-generation, apps, plugins, hooks, goals, and multi-agent facilities disabled.
  Require the offline exact-config/tool-manifest qualification on every Codex upgrade.
  Treat the pinned Codex executable, proxy image, container runtime, DNS resolver, and host
  kernel as trusted. Pin the local runner/proxy image identities and source hashes; attach
  the runner only to a broker-owned internal network whose sole peer is the inspected proxy.
  The proxy must accept only canonical CONNECT for the policy-pinned provider host on 443,
  reject IP literals and every DNS answer set containing a non-public address, connect to
  the vetted IP, retain end-to-end TLS, enforce tunnel limits, and log no body bytes.
  Disable DNS in the worker and address the proxy by its inspected private IP. Require the
  proxy to verify its canonical policy hash at startup and bind that hash into inspectable
  container state.
- Accept Codex spillover authorization only from the exact private-policy issuer and
  Ed25519 key. Require fresh password plus authenticator-app or email-code MFA, exact plan
  and billing binding, a typed challenge, a maximum five-minute lifetime, and a durable
  content-free one-use tombstone written before authorization. Pixel must never receive
  the password, one-time code, session token, subject identity, or private signing key.
- Keep Codex credential locations deployment-private and outside policy. Require a real
  owner-only directory and bounded single-link credential file; allow ChatGPT cache
  rotation only after complete revalidation and reject any API-key file change. Do not put
  credential material or paths in arguments, ambient environment, handles, receipts,
  plans, UI, logs, or results. Synthetic custody tests are not production installation or
  permission evidence.
- Bind the exact isolated transport hash into the plan, MFA assertion, authorization, and
  claim. Keep `test-mock` and `external-signed-mfa` authorization sources mutually
  exclusive. Stream credential bytes and the sanitized task as separate length-framed
  stdin fields; use only an ephemeral private in-container Codex home; force-delete every
  named runner on success, failure, timeout, cancellation, or malformed output. Never retry
  an uncertain provider turn under the same or a replacement authorization. Do not record
  success or install a returned ChatGPT cache until forced removal is independently proven;
  after timeout, wait for bounded adapter cleanup before returning uncertain evidence.
- Require the detached Ed25519 signature as well as the age identity and checksum for
  every private-state validation, rehearsal, or restore. Keep a trusted allowed-signers
  copy separate from the backup set.
- When Deep Work knowledge is enabled, keep `pixel-knowledge-vault-key` outside the
  state, configuration, and vault backup roots. Enable the separate Deep Work backup scope,
  preserve historical vault keys offline for the retention period of their encrypted
  archives, and never bypass the restore hook by copying a historical vault over the live
  path. The hook must reconcile the active tombstones, remove resurrected ciphertext,
  rotate to the current key, and pass a deep audit before service restart.

## Static-analysis disposition contract

Code-scanning alerts are fixed in code whenever the reported flow can cross a trust
boundary. An alert may be marked false positive only after its exact path is reviewed
and the repository alert records the compensating boundary.

The intentional group-readable files are limited to the sanitized broker interfaces:
Source projections/results and Operations/Frontier inventory, results, and events use
mode `0640` inside setgid `0750` directories or equivalent reader-only ACLs. The gateway
reader is explicitly excluded from broker policy, credentials, plans, approvals,
authority state, private state, and artifacts; those remain mode `0600` in `0700`
directories. Changing the shared interface files to owner-only would break the broker's
separate-identity design, while broadening the reader identity would expose authority.
The Frontier live-qualification request and receipt use the same interface solely for
their fixed public synthetic request and content-free evidence; private consent and the
pre-call authorization claim never enter that interface.

Two reviewed network flows are also intrinsic to their feature: Google OAuth sends the
local client credential only to Pixel's fixed Google token endpoint and atomically stores
the returned refresh token in a mode-`0600` local file; upstream intake writes registry
bytes only after canonical-origin, size, and exact npm-integrity verification into a
private exclusive-create quarantine file. A generic destination, unverified response,
or less-private file is not covered by this disposition.

## Never commit runtime state

OpenClaw runtime directories contain identity keys, pairings, OAuth tokens, session
transcripts, channel credentials, and personal memory. This repository tracks
templates only. Never copy a live `~/.openclaw` directory into Git.

Before every push, run:

```bash
bash scripts/check-no-secrets.sh
```

The release archive is built from committed source only and contains no `.git`, `.env`,
`.generated`, `.runtime`, token, or workspace runtime state. Treat a source clone as
developer material and the checksummed archive as the preferred client handoff.

## Legacy clean-migration boundary

Migrating from the supported legacy Pixel 3.2.2 source to the current 4.2 release is
terminal-only: there is no in-place 3.2.2 update, and the 4.2 release stays
`qualificationMode=forward` with `minimumUpgradablePixel=4.0.0`. `migrate-legacy-clean`
never claims or enables an in-place update and does not automate bootstrap, apply, or
rollback; the authenticated backup is the single rollback boundary. The `activate`
subcommand executes the trusted 4.2 restore in an explicit migration-only mode that builds
its allowlist from exactly the authenticated 3.2 root contract; the authenticated 3.2 manifest
may omit new 4.2-era control/frontier roots, but every declared legacy destination must still be
within the known 4.2 destination allowlist, so a 3.2 root that 4.2 dropped is rejected and
standard 4.2 restore is never widened. It binds the restore receipt to the exact plan/rehearsal/backup/source, proves the
pre-prepared 4.2 configuration is frozen and unchanged before and after the legacy data swap
(nothing is regenerated), restarts services and verifies the live deployment and runtime attestation while the
restore's old-path rollback state remains armed (services may run through the verify, which
can require them), and only then commits and deletes the old state. Commit marks the state
committed durably before destructive cleanup and is idempotent, so a cleanup failure retains
the verified new live state and never triggers rollback. On any failure it leaves a truthful
non-pass receipt whose `rolledBack` is recorded only when an exact rollback was verified, and
never deletes the authenticated backup.

The migration transaction journal lives in a fixed, root-owned, mode-0700 custody directory
(`/var/lib/pixel-migration-journals`) and is written only directly beneath it with a safe
basename. A privileged helper (run as root via sudo) creates/validates the root-owned mode-0700
custody directory through a bound descriptor and atomically reserves the exact journal basename
(`O_CREAT|O_EXCL|O_NOFOLLOW`, mode `0600`) under an exclusive `flock` before any live mutation;
the non-root orchestrator never claims it can test availability inside the root-0700 custody.
The reservation is O_EXCL-only: EVERY pre-existing marker is refused, including a stale
reservation for the same contract (there is no implicit stale reclaim in normal activation). The
reserved marker is bound to the authenticated contract, the exact backup SHA256, and the fixed
3.2.2 -> 4.2.0 source/target; the helper returns a per-invocation cryptographic reservation token
that arm and abort must present, so a concurrent same-contract activation can never reclaim or
arm another invocation's reservation. Control verbs reject a reserved (not yet armed) journal, and
arming atomically replaces the exact reserved marker with the fully armed transaction after
validating it with the same exact path/sibling/contract/unit validator used by commit and
rollback (failing closed on token/contract/backup mismatch or any malformed/malicious entry). A
pre-swap failure aborts only the exact reserved marker; an armed or foreign transaction is never
erased. Ownership trust for the journal and its custody directory is derived only from the
privileged effective uid (root); there is no caller- or environment-supplied owner override, and
the helper runs as the root euid invoking only fixed absolute trusted binaries (`/usr/bin/systemctl`,
`/usr/bin/rm`, `/usr/bin/mv`) directly — no environment/PATH tool override a privileged euid accepts.
The privileged helper opens the custody directory through a bound descriptor and holds an exclusive
`flock` on it for the descriptor-bound journal read, every state operation, and the atomic state
write, so concurrent control verbs serialize and a same-user process cannot swap the directory.
Every armed/progress/commit/rollback/finalization state transition is a crash-safe atomic
replacement (an O_EXCL mode-0600 temp written and fsynced beneath the bound custody dirfd,
verified, renamed over the journal via dirfds, then the custody directory is fsynced) — never an
in-place ftruncate that a power loss between truncate and fsync could leave truncated and destroy
the only rollback state. Rollback quiesces the exact validated units (stopped and verified
inactive) before any filesystem mutation; if any stop or verification fails, every validated unit
is restarted best-effort and no mutation or rollback progress is recorded. Rollback persists
per-root progress after each root with recorded old-snapshot inode/device/type evidence so a retry
recognizes an already-completed sibling move and never deletes an already-restored pre-state.
Terminal commit never restarts services (they already run and passed the outer verify) and marks
finalization complete truthfully without a disruptive restart; filesystem cleanup is tracked
separately (`cleanup`), is idempotent, and a cleanup failure is reported truthfully and never
triggers rollback. A rolled-back transaction is never committed and a committed transaction is
never rolled back.

**Crash-safe armed-before-swap recovery.** The full transaction journal is durably armed BEFORE
the first live destination rename, capturing per-root old-snapshot evidence from each live
destination while it is still at destination (the inode/device/type follows the content to
`oldPath` if moved); arm fails closed unless the live preconditions rollback depends on already
hold. After arm, an ordinary failure before the successful migration-mode return is undone
automatically from the shell's EXIT path by the same privileged rollback. A hard kill (SIGKILL/
power loss) never runs any trap, so nothing is invoked automatically; the armed journal remains
the single exact recovery authority and recovery is **deterministic resumable recovery** via the
explicit `--migration-rollback` control verb, which an operator or resumer runs to reconstruct
unswapped, half-swapped, partially swapped, or fully swapped roots exactly and clean every
temp/old sibling. Before arm, the authenticated backup is the single recovery authority and the
reservation is aborted exactly, never leaving an armed transaction.

All plan, rehearsal, and completion receipts are owner-only evidence written atomically
outside the source repository. They are content-free: they may bind content hashes and
Git source identity, but never include paths, host identity, credentials, signer
identity, model/provider identity, or user content. Backup SHA-256 and `sourcePixel` are
bound from the authenticated backup's content-free audit; restore is validated with the
existing trusted primitive and rehearsed only into an explicit new non-live root.
Finalization requires the authenticated backup and an exact private 0600 restore receipt,
recomputes the backup SHA-256, and validates the receipt's exact fields and self-hash against
the plan. It confirms the active release pointer is exactly `releases/4.2.0`, removes any stale
pre-existing attestation at the exact install-dir path, runs `./pixel verify --expected-install-dir
ABSOLUTE` (which loads the normal trusted configuration and fails closed unless its configured
`PIXEL_INSTALL_DIR` resolves exactly to that safe non-root path, rather than overriding it),
separately requires the attestation at the same directory, strictly validates the fresh runtime
attestation (requiring `recordStatus=supported`, a `same-source`/`qualified-ancestor` relationship,
a non-null source commit, a strict `YYYY-MM-DD` qualified date, and six unique connectors covering
the expected set), and binds every phase to one clean, current Git source identity. The restore
receipt path is atomically reserved (as a non-passing marker) before any live-state transaction
and finalized only after commit, so a bad/occupied/unsafe/full path cannot fail the command after
an irreversible live commit; a rare finalization failure reports state accurately. The rollback boundary is the authenticated backup: no in-place rollback exists
for a 3.2.2 source, so verify and rehearse the backup before touching live state.

## Data handling

| Data | Repository | Private backup |
|---|---:|---:|
| Templates and plugin source | yes | optional |
| Sanitized configuration examples | yes | yes |
| OAuth client and refresh token | never | yes, encrypted and signed |
| Workspace memory and daily notes | never | yes, encrypted and signed |
| Session transcripts and uploaded files | never | policy-dependent |
| Device identity and pairing files | never | yes, encrypted and signed |

Email bodies are untrusted correspondence. The Source Broker must discard raw bodies
after generating a typed projection, and Pixel must never receive source credentials or
raw content. Projection summaries remain untrusted and cannot authorize another tool.
Web pages are equally untrusted. Courier policy rejects obvious SSRF targets and checks
resolved addresses for every browser request; its egress proxy connects to that checked
numeric address rather than resolving again. Operators must still patch Chromium and
Playwright promptly and must never use the courier as an authenticated browser.

The Operations Broker applies the same content/authority separation to CLI, network,
and fleet work. The OpenClaw plugin can write bounded requests and read bounded results
but contains no process, SSH, or network implementation. The broker independently
compiles a private policy, checks target identity, binds approvals to an immutable plan,
limits time/output/concurrency, redacts credential-shaped output, and marks all evidence
untrusted. Downloads use public-IP enforcement and quarantine; artifact transfers
rehash both ends and never execute the file. See `THREAT-MODEL.md` for residual risks.

Use `security-evals/email-prompt-injection/` to verify the email boundary against a live
deployment. The harness uses disposable local canaries and a fake secret; never replace
them with a real credential or a real exfiltration endpoint.

The Source Broker reduces blast radius but is not treated as a perfect sanitizer. Any
email-triggered `exec`, file, memory, network, actuator, or raw-source access is a failed
release gate even when the final response refuses the attack.

## History-remediation rule

The repository's writable history was rewritten on 2026-08-05 after an audit found legacy
OpenClaw runtime state and credential material. Removing files from the current tree alone
is never sufficient: rotate every affected credential at its issuer, scan all local refs
including stashes, resynchronize every clone, and run the isolated remote-ref audit. GitHub
Support ticket `#4636471` tracks removal of the six legacy pull-request refs and cached
views. Those exact pinned refs are tracked as a separate historical incident; their
presence is not evidence of a defect in current Pixel code and does not block a Pixel
release. A moved legacy ref, unknown non-Pixel ref, or finding in the active Pixel lineage
still fails closed. Do not restore a pre-rewrite branch, tag, stash, bundle, or clone to
the remote.

## Reporting

Report suspected exposure privately to the repository owners. Do not open a public
issue containing credentials, OAuth codes, private keys, session text, or client data.
