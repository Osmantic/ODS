# deploy/mesh — Rootless trusted-owner Pixel mesh (mesh-0.1.0)

An additive, rootless deployment that runs one trusted-owner Pixel per tower
(Tower1/Tower2/Tower3) under `pixel-mesh-gateway.service` without touching the
hardened system deployment, the custody source tree, or any live runtime.

## Architecture

```
pixel-mesh-peer (CLI on $PATH via ~/.local/bin)
   ├── status          local evidence (host, GPU, model router, user systemd)
   ├── fleet-status    per-peer fresh evidence (local + SSH peers)
   ├── peer-status P   single peer evidence
   ├── message P       send bounded task text on stdin to peer P
   └── ask             run the local mesh Pixel agent on bounded stdin

pixel-mesh-gateway.service (systemd --user, loopback)
   ├── OpenClaw gateway with the mesh profile (state in ~/.openclaw-mesh)
   └── SHELL=current/exec-shell/bash (fail-closed pipeline status)
```

- **Release layout:** `~/.local/share/pixel-mesh/releases/<version>-<content-hash>` covers
  both `pixel-mesh-peer` and `exec-shell/bash`, with `current` and `previous`
  symlinks retained for rollback.
- **State:** `~/.openclaw-mesh` (workspace, `openclaw.json`, `.env`, approvals).
- **Peer allowlist:** fixed at exactly Tower1/Tower2/Tower3. Messages travel on
  stdin to fixed SSH/OpenClaw argv; they are never interpolated into a shell.

## Install

```bash
python3 deploy/mesh/install.py --node Tower1   # Tower2 | Tower3
```

The installer fails closed before state mutation if the pinned OpenClaw binary,
the bounded exec-shell source, or a root-owned non-writable `/bin/bash` is
unavailable. It validates the generated `openclaw.json` with
`openclaw config validate` before enabling the service.

Environment overrides (all optional; safe defaults preserved):

| Variable | Default | Purpose |
| --- | --- | --- |
| `PIXEL_MESH_OPENCLAW_BIN` | `~/.npm-global/bin/openclaw` | OpenClaw binary |
| `PIXEL_MESH_MODEL_BASE_URL` | `http://127.0.0.1:18080/v1` | Tower2 local-first router |
| `PIXEL_MESH_HOME` | real `$HOME` | alternate home (tests / staging) |
| `PIXEL_MESH_SESSION_KEY` | fresh random key per task | explicit safe session continuity |

`--dry-run` verifies the OpenClaw executable and validates a generated config
in a temporary directory, then prints the resolved plan as JSON without
mutating the target home.

## Update

For initial setup or an explicitly owner-managed standalone mesh deployment,
re-run `install.py` for the same node with the new release. The installer
creates a content-addressed release directory, validates the existing profile, points
`current` at it atomically, and retains the prior release as `previous`. It
preserves the gateway token, `openclaw.json`, approvals, and workspace files,
including Discord/channel configuration and owner customizations. Move those
state files aside explicitly if a clean profile regeneration is intended.

On the first hardened update, a legacy peer-only rollback target is copied into
a new content-addressed compatibility release with the exec shell; the legacy
release is not modified. OpenClaw's observed ordinary/PTY argv and interactive
snapshot argv are allowlisted. Any new upstream invocation shape fails with
exit 64 until deliberately qualified.

When the mesh helper is supporting an installed signed Pixel release, use the
version-bound reconciliation transaction instead of the standalone update or a
manual copy. Run both commands from the exact clean source commit named by the
active release's supported `release-identity.json`:

```bash
python3 deploy/mesh/install.py reconcile --node Tower2 --preview
python3 deploy/mesh/install.py reconcile --node Tower2 --confirm \
  --reconcile-hash <FULL_RECONCILE_SHA256>
```

Preview is read-only. It binds the source commit/tree/version, active release
identity hash, current/previous content-addressed mesh releases, exact candidate
bytes, fixed paths, host, port, and active gateway state. Confirmation re-reads
that entire intent under an owner-private lock and requires the full hash before
materializing or switching bytes. A legacy owner-owned, non-world-writable
release parent (for example `0775`) is recorded in the preview, hardened to
`0700`, and re-verified before candidate materialization; foreign-owned,
symlinked, or world-writable layouts are refused. It writes immutable mode-0400 claim and result
records under `~/.local/state/pixel-mesh/`, restarts and probes the gateway, and
restores both prior links if post-switch verification fails. Neither command
starts a model turn or grants external-effect authority.

Final receipt names are published atomically from fully written, mode-fixed,
fsynced anonymous inodes, so an uncatchable exit leaves either no record or one
complete immutable record. Candidate releases are likewise built and fsynced in
an owner-private sibling staging directory, then published with an atomic
no-replace rename; an interrupted staging directory is never treated as a final
release. The custody lock is opened descriptor-first with no-follow semantics
and rejects linked, non-owner, or non-private lock files.

Source custody does not rely on `git status` alone. Reconciliation rejects
assume-unchanged, skip-worktree, and every other noncanonical tracked-index
state; compares `VERSION`, the installer, peer, and exec-shell working bytes to
their blobs from one anchored `HEAD`; and rechecks HEAD, tree, index, and tracked
status before using the already-validated peer and shell bytes. Hidden working
tree drift therefore fails before candidate evaluation, materialization, or any
claim/receipt publication.

An uncatchable process or host interruption can leave the immutable forward
claim without a terminal result. Do not rerun `reconcile` or copy files by
hand. Derive a separately confirmed recovery from the original claim:

```bash
python3 deploy/mesh/install.py reconcile-recover --node Tower2 \
  --reconcile-hash <FULL_RECONCILE_SHA256> --preview
python3 deploy/mesh/install.py reconcile-recover --node Tower2 \
  --reconcile-hash <FULL_RECONCILE_SHA256> --confirm \
  --recovery-hash <FULL_RECOVERY_SHA256>
```

Recovery preserves the original claim, revalidates its exact source and active
Pixel identity, and accepts only one of the uniquely bound pre-switch,
previous-link-switched, or complete-forward-link states. Directory ownership,
mode, release bytes, helper link, service, or mixed-link drift fails closed for
manual review. Every recovery attempt receives a new predecessor-bound one-use
hash. A successful recovery writes the original forward result plus its bound
recovery receipt, without creating a second forward claim. A catchable recovery
failure restores the exact prior links and emits terminal failure receipts, so
a subsequent `reconcile --preview` derives a new retry hash instead of reusing
history.

After a successful reconciliation, derive and confirm its separately bound
rollback while the active Pixel and mesh state remain unchanged:

```bash
python3 deploy/mesh/install.py reconcile-rollback --node Tower2 \
  --reconcile-hash <FULL_RECONCILE_SHA256> --preview
python3 deploy/mesh/install.py reconcile-rollback --node Tower2 \
  --reconcile-hash <FULL_RECONCILE_SHA256> --confirm \
  --rollback-hash <FULL_ROLLBACK_SHA256>
```

This rollback accepts only the immutable successful forward receipt and its
exact resulting `current`/`previous` state. The standalone `--rollback` command
below remains for standalone mesh installations; it is not the signed-Pixel
reconciliation rollback contract.

If that confirmed rollback is interrupted after its immutable claim but before
its terminal result, recover the exact rollback claim rather than issuing a
second rollback:

```bash
python3 deploy/mesh/install.py reconcile-rollback-recover --node Tower2 \
  --rollback-hash <FULL_ROLLBACK_SHA256> --preview
python3 deploy/mesh/install.py reconcile-rollback-recover --node Tower2 \
  --rollback-hash <FULL_ROLLBACK_SHA256> --confirm \
  --recovery-hash <FULL_RECOVERY_SHA256>
```

Rollback recovery accepts only its uniquely classified pre-switch,
current-link-switched, or complete-rollback-link state. It preserves the
original rollback claim, binds each interrupted recovery to its predecessor,
and writes the terminal rollback receipt without creating a second rollback
claim.

## Rollback

The prior release is retained at `~/.local/share/pixel-mesh/previous`. Roll back
with the installer. Rollback swaps `current` and `previous`, validates that both
targets remain under the release directory, requires both targets to agree on
exec-shell hardening, and restarts the gateway:

```bash
python3 deploy/mesh/install.py --rollback
python3 deploy/mesh/install.py --rollback --dry-run
```

The wrapper enables Bash `pipefail`, so a failed producer is no longer hidden by
a successful final pipeline stage. Expected early-consumer pipelines such as
`yes | head` can therefore return SIGPIPE status 141 and must be handled
explicitly. A command that deliberately starts another shell (for example,
`/bin/sh -c 'false | true'`) controls that nested shell's semantics and bypasses
the outer `pipefail`; callers must not claim otherwise.

## Status checks

```bash
systemctl --user status pixel-mesh-gateway.service
pixel-mesh-peer status
pixel-mesh-peer fleet-status
printf '%s\n' 'bounded task text' | pixel-mesh-peer message Tower1
printf '%s\n' 'local task text' | pixel-mesh-peer ask
printf '%s\n' 'read-only review text' | pixel-mesh-peer message-readonly Tower1
printf '%s\n' 'local read-only review text' | pixel-mesh-peer ask-readonly
printf '%s\n' '{"schemaVersion":1,"task":"Return the observed result.","schemaName":"result","schema":{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"],"additionalProperties":false}}' | pixel-mesh-peer ask-contract
```

`ask-contract` and `message-contract` keep the ordinary owner-authorized
exploration tools, bind the validated canonical schema and its hash into that
exploration objective, and require the durable terminal itself to be exact JSON.
Finalization is deterministic and makes no additional model call. The input is
an exact four-field JSON envelope:
`schemaVersion`, `task`, `schemaName`, and `schema`. The schema is caller-supplied
but limited to a bounded strict subset: objects require every declared property
and reject extras; arrays require a bounded `maxItems`; scalar enum and length or
numeric bounds are supported, with integers confined to the interoperable JSON
safe-integer range. Bounded `oneOf` branches and scalar `const` values can make
cross-field states physically exclusive (for example, a `pass` branch whose
findings array must be empty versus a `block` branch requiring findings). Pixel
requires exactly one branch to match independently of provider decoding. Schema
depth, node count, bytes, result bytes, and provider output are capped.

The zero-token deterministic finalization receipt binds the canonical schema
hash and exact predecessor transcript hash. Pixel parses and validates only the
exact durable terminal JSON and canonicalizes it before success; it does not
derive missing fields from prose, tool output, hidden reasoning, or another
model. Fences, prose, duplicate keys, truncation, wrong types, missing or extra
fields, provider tools, and receipt drift fail closed with no prose fallback.
Contract validity proves shape and
custody only: `completionAuthority`, `externalEffectAuthority`, and
`acceptanceAuthority` remain false and semantic acceptance requires independent
verification. Append `-readonly` before `-contract` to combine the same output
contract with the existing single-tool read-only exploration profile.

For a local strict contract that needs public Web observation but no local-file
or external-effect tools, select the closed owner-process profile explicitly:

```bash
printf '%s\n' '{"schemaVersion":1,"task":"Read one official public URL with pixel_web_browse and report the observed result.","schemaName":"web_result","schema":{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"],"additionalProperties":false}}' | PIXEL_MESH_CONTRACT_AUTHORITY_PROFILE=web-read-only pixel-mesh-peer ask-contract
```

`web-read-only` exposes exactly `pixel_web_browse`. The task, schema, source
config, and model cannot select or widen that tuple. Pixel reconciles the
durable exploration transcript against the exact allowlist, binds the terminal
finalization to the profile and ephemeral-config hash, and verifies removal of the
owner-private ephemeral config before success. The profile is accepted only on
ordinary local `ask-contract`; combining it with read-only or sandboxed routes
fails closed.

### Operations broker turns (`ask-operations-broker`)

Use the fixed local Operations route for owner-authorized host observation or
change requests that must not expose generic shell, file, browser, mailbox, or
session tools:

```bash
printf '%s\n' 'Inventory the targets, select the exact target from evidence, and observe host identity.' | pixel-mesh-peer ask-operations-broker
```

The route exposes exactly `pixel_ops_inventory`, `pixel_ops_run`, and
`pixel_ops_job_wait`. The prompt, source config, and model cannot widen that
tuple. Tool exposure is not effect authorization: the Operations broker remains
authoritative for every target, action, tier, approval, execution, and
reconciliation decision. Pixel checks the durable tool-call order, proves the
canonical owner-private config remained byte-identical, and removes the exact
ephemeral config before success. Use `ask-operations-broker-contract` with the
same four-field envelope accepted by `ask-contract` when the terminal shape must
also be enforced deterministically. Neither command can be combined with
read-only, sandboxed, or environment-selected authority profiles.

### Exact download staging turns (`ask-download-staging`)

Use the separate fixed download route when a task must stage owner-authorized
public HTTPS bytes without exposing generic Operations actions:

```bash
printf '%s\n' 'Inventory the download contract, then stage the named URL with its expected SHA-256.' | pixel-mesh-peer ask-download-staging
```

This route exposes exactly `pixel_ops_inventory`,
`pixel_ops_download_stage`, and `pixel_ops_job_wait`. The inventory marks each
action with its truthful `requestKind` and `callableVia`; broker-native download
and transfer entries are not presented as generic target/action pairs. URL and
domain policy, expected hash enforcement, approval, quarantine execution, and
terminal reconciliation remain broker-authoritative. A successful stage is not
a workspace handoff. Use `ask-download-staging-contract` for deterministic
terminal shape. Neither command grants generic shell, file, browser, mailbox,
artifact-transfer, workspace-publication, or acceptance authority.

### Sandboxed workspace turns (`ask-sandboxed`)

`ask-sandboxed` runs one local turn bound to the exact validated git workspace
with a six-tool sandboxed default. The owner can narrow that turn to a smaller
immutable allowlist through the process environment only:

```bash
printf '%s\n' 'bounded workspace task' | \
  PIXEL_MESH_SANDBOXED_ALLOWED_TOOLS='read,edit' pixel-mesh-peer ask-sandboxed
```

- Unset, the exact six-tool sandboxed default (`read,write,edit,apply_patch,exec,process`)
  is preserved.
- An explicitly set value can only narrow: it must be a comma-separated,
  case-sensitive subset of that fixed set; order is preserved and reported as
  the turn's `allowedTools` authority evidence.
- Task prompt and stdin content can never set or widen this value; only the
  owner's process environment controls it.
- Authority evidence preserves both the sorted distinct `observedTools` set and
  the exact transcript order in `observedToolsInOrder`; downstream auditors do
  not need to trust a model-authored tool-order claim.
- Malformed or widening values (empty tokens, whitespace, duplicates, unknown
  tools, overlong or non-UTF-8-encodable input) fail closed with `ValueError`
  before any agent launch.
- A normal checkout exposes no host path beyond the writable workspace. For a
  linked Git worktree only, Pixel validates and revalidates the exact `.git`
  pointer, its owner-owned non-group/other-writable common Git directory, and
  their custody identities,
  then mounts that one common directory read-only at the same absolute path.
  Authority evidence reports this narrow metadata-read grant as
  `hostGitMetadataReadAuthority: true` while `hostFilesystemAuthority` remains
  false. Because OpenClaw normally rejects every bind source outside the
  workspace, the derived one-turn config also records and enables its external
  source override only for this Pixel-validated read-only path; neither prompt
  nor model can select it. Every fresh sandboxed turn removes its exact
  session-labeled runtime, verifies no exact-label container remains, restores
  OpenClaw's fixed workspace control path to its exact pre-turn presence, and
  removes its owner-private ephemeral config before returning success. Network
  and elevated execution remain disabled.

#### Owner-selected loopback inference pin

Any sandboxed entry point can keep its workspace, tools, Gmail projection, and
container execution on the current host while sending only model inference to
another owner-controlled loopback endpoint. Both values are required together:

```bash
PIXEL_MESH_SANDBOXED_MODEL_PIN='tower1-pin/Qwen3.6-27B-UD-Q4_K_XL' \
PIXEL_MESH_SANDBOXED_MODEL_BASE_URL='http://127.0.0.1:18101/v1' \
  pixel-mesh-peer ask-sandboxed-mailbox-readonly

# This installation's second local example uses the Tower3 tunnel.
PIXEL_MESH_SANDBOXED_MODEL_PIN='tower3-pin/Qwen3.6-27B-UD-Q4_K_XL' \
PIXEL_MESH_SANDBOXED_MODEL_BASE_URL='http://127.0.0.1:18103/v1' \
  pixel-mesh-peer ask-sandboxed-readonly
```

These Tower names, ports, and model IDs are local examples, not product policy.
Other owners may select different provider/model identifiers and ports. Pixel
accepts only a canonical `http://` loopback URL with an explicit port and exact
`/v1` path. It clones the already selected provider's credentials plus bounded
conservative context/output limits into the owner-private ephemeral config, rewrites only the Pixel
agent's model reference, and removes that config before success. The canonical
config is never edited, prompt/tool content cannot choose the pin, provider-ID
collisions fail closed, and `meshAuthority` reports `inferencePinned` plus the
non-secret selected provider/model/loopback URL. Unset variables preserve the
existing route; partial or malformed values fail before workspace validation or
model launch. Non-sandboxed entry points never read these variables.

### Read-only sandbox shell turns (`ask-sandboxed-readonly`)

`ask-sandboxed-readonly` is the fixed inspection profile for tasks that need
both file reads and shell-derived analysis without workspace mutation:

```bash
printf '%s\n' 'inspect the repository and report findings' | \
  pixel-mesh-peer ask-sandboxed-readonly
```

- The only model tools are exactly `read` and sandbox-hosted `exec`; prompt,
  stdin, model output, and `PIXEL_MESH_SANDBOXED_ALLOWED_TOOLS` cannot alter or
  widen that tuple.
- The validated Git workspace is mounted read-only at OpenClaw's fixed
  `/agent` path; relative tool paths still resolve inside the disposable
  sandbox workspace. Any separately validated linked-worktree common metadata
  is mounted read-only at its identity-bound absolute path. OpenClaw
  apply-patch execution is disabled, while the container's ephemeral `/tmp`,
  `/var/tmp`, and `/run` tmpfs mounts remain writable for analysis tools.
- Network and elevated execution remain disabled. The same exact container,
  label, config, transcript, Git identity, and workspace-control-path
  reconciliation used by `ask-sandboxed` runs before any success response.
- Authority evidence reports `workspaceReadAuthority: true` and
  `workspaceWriteAuthority: false`; either a disallowed transcript tool call or
  unexpected workspace residue fails closed and grants no acceptance authority.

### Read-only mailbox triage (`ask-sandboxed-mailbox-readonly`)

`ask-sandboxed-mailbox-readonly` is the closed profile for private Gmail triage
that must persist a local report without granting mailbox mutation or host exec:

```bash
printf '%s\n' 'triage the authorized mailbox and write the private report' | \
  pixel-mesh-peer ask-sandboxed-mailbox-readonly
```

- The exact tool tuple is `read`, `write`, sandbox-hosted `exec`,
  `pixel_gmail_search`, and `pixel_gmail_read`; neither prompt nor environment
  can alter it.
- The validated Git workspace is the only writable host bind. The container is
  networkless, non-root, capability-free, resource-bounded, and cleaned up
  before success; generic host filesystem/exec and apply-patch remain disabled.
- Transcript reconciliation rejects every other tool, including mailbox
  mutation, calendar, Web, social, and Operations tools. Authority evidence
  reports mailbox read and workspace write authority separately while keeping
  mailbox mutation and external-effect authority false.

## Trust boundary

- The gateway binds loopback only, with token auth.
- Peer name resolution is an exact allowlist; no shell interpolation anywhere.
- Each CLI/peer task gets a fresh OpenClaw session by default, preventing prior
  task/tool history from bleeding into new work. Set `PIXEL_MESH_SESSION_KEY`
  only when deliberate continuity is required.
- The peer supervises that exact session transcript and terminates the complete
  nonce-marked process tree if one turn reaches 96 model calls, 96 tool calls,
  more than two compactions, or a second explicit tool-loop overflow. Long work
  must cross turn boundaries through durable yield/resume instead of unbounded
  compaction inside one invocation.
- Status evidence is size-checked and timestamped; an unreachable
  peer is reported as unknown, never as proof of absence.
- The mesh profile is explicitly owner-trusted: unsandboxed host exec, real
  filesystem, Docker, network, and SSH, per Michael's standing authorization.
- `ask-readonly` and `message-readonly` mechanically narrow one fresh turn to
  the `read` tool through an owner-private ephemeral config. Before returning
  success, the peer independently reconciles the durable transcript and rejects
  any tool call outside that exact authority. Use the ordinary commands only
  when the owner has granted the task full mesh authority.
- Contract commands change only terminal output mechanics. They do not reduce
  the exploration tool surface unless the `readonly` form is selected, and
  they never convert schema validity into task truth or external-effect proof.
- Every tower is therefore a full trust root. SSH authenticates peer transport,
  but compromise of any allowed peer can request full-authority work on another
  peer. This deployment is owner-only and must not be exposed to untrusted or
  multi-tenant hosts.
- A command addressed to the current allowlisted node stays in the local
  transport instead of requiring a separate self-SSH key. Other peer commands
  retain the fixed BatchMode SSH path.

## Discord handoff to the mesh

`deploy/mesh/migrate_discord.py` moves the Discord-facing Pixel channel from
the restricted system profile to the trusted mesh profile. It preserves
unrelated configuration and the established Pixel workspace/persona while
toggling only Discord: enabled on the mesh profile and disabled in the
restricted profile. It contains no account-specific identity or credential.

```bash
# Non-mutating inspection (default; creates no backup and changes nothing)
python3 deploy/mesh/migrate_discord.py

# Apply after reviewing the inspection output
python3 deploy/mesh/migrate_discord.py --apply

# Restore a validated backup under the configured private backup root
python3 deploy/mesh/migrate_discord.py --restore <BACKUP_DIR>
```

The utility preflights every input before mutation. `--apply` creates a private
`0700` backup directory containing `0600` copies of the restricted config,
mesh config, workspace instructions, and a manifest. Both candidate configs
are validated from staging before atomic live writes. Later failures restore
the exact backups; explicit restore validates the root, manifest, and configs
before writing and restarts the user mesh service. Config contents and secrets
are never printed.

The utility never invokes `sudo`. Restarting the restricted system
`openclaw-gateway.service` requires separate authorization and is reported as
an operator step. After `--apply`, verify `pixel-mesh-gateway.service`, run
`pixel-mesh-peer status`, confirm Discord traffic lands on the mesh gateway,
then perform the separately authorized restricted-service restart if needed.

Optional path overrides are `PIXEL_MESH_HOME`,
`PIXEL_MESH_OPENCLAW_BIN`, `PIXEL_MESH_RESTRICTED_STATE`,
`PIXEL_MESH_STATE`, and `PIXEL_MESH_BACKUP_ROOT`.

## Runtime generation

The mesh profile, `.env`, `exec-approvals.json`, the workspace operating
contract (`AGENTS.md`, `IDENTITY.md`, `TOOLS.md`), and the systemd unit are
generated at install time from `install.py` with the resolved node, port, and
home. They are never committed to the repository.

## Explicit secret exclusions

The following are generated at runtime and are deliberately never source:

- `~/.openclaw-mesh/.env` and `~/.config/pixel-mesh/gateway.env` (gateway token)
- `~/.openclaw-mesh/openclaw.json` (may contain channel/plugin credentials)
- `~/.local/share/pixel-mesh/releases/**` (installed release bytes)
- `~/.config/systemd/user/pixel-mesh-gateway.service` (host unit)

Repository `.gitignore` additionally excludes `.env*` and OpenClaw runtime
state so none of these can be tracked accidentally.
