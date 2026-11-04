# Native Portal integrations qualification

Status: native lifecycle implementation and owned-file regression coverage;
**physical Apple Silicon qualification has not been completed**. Linux/WSL
metadata-driver results do not establish launchd, Darwin ACL, Seatbelt, reboot,
Metal/model or native process behavior.

This matrix covers the native Portal runtime, scheduling and Coffee integrations.
The candidate includes the native Source Broker path for Google/Calendar and
reviewed Connected apps, alongside the Linux/WSL route. Native custody, first
connection, repeated updates, retained history and retirement now have Linux
owned-file coverage. The Darwin catalog/relay and these owner journeys still
require the physical run below; an offline pass is not Mac acceptance.

The previously accepted Windows candidate (`fa2e`) used Source **4.3.46**
(`bf6db06c`), with 51 installed SDK files and 26 canonical repair receipts. Its real Portal
Calendar-count and scheduled-task journeys passed. The PR qualification note
identifies the exact composed candidate and records its final Windows update
result.

## First physical run

Start from the exact source commit named in the PR qualification note, with
the checkout outside the install directory. Keep the local container engine
running. For a first native installation, use a disposable Apple Silicon Mac
or macOS guest with no existing native Pixel deployment. A second user account
or a different `ODS_HOME` still shares the system launchd services.

On a Mac that already runs native Portal, retain the installation, model and
private receipts and use the existing-install procedure below. The native
initial-install preflight deliberately reports
`existing-native-pixel-requires-migration-or-recovery` for an existing deployment;
that is not an instruction to remove it. The general `ods-macos.sh update`
command uses the installed source.

Before updating an existing MacBook installation, record the selected model,
connected accounts, integration choices, existing tasks and retained results.
Do not retire or reinstall a working installation to perform this test.

For a checkout at `~/src/ODS` and installation at `~/ods`, record the source and
platform first:

```sh
git -C "$HOME/src/ODS" status --short
```

If there are local edits, stop and preserve them. Replace `QUALIFIED_PR_HEAD`
below with the reviewed commit supplied in the PR qualification note. Fetch
the PR and select that revision; a moving branch name is not an acceptance
identity:

```sh
git -C "$HOME/src/ODS" fetch origin pull/8293/head
git -C "$HOME/src/ODS" switch --detach QUALIFIED_PR_HEAD
git -C "$HOME/src/ODS" rev-parse HEAD
git -C "$HOME/src/ODS" status --short
sw_vers
uname -m
docker context show
docker version --format 'client={{.Client.Version}} server={{.Server.Version}}'
```

Record the actual `rev-parse HEAD` output with the results and confirm the
checkout is clean. Use that recorded revision for the journeys below.
For an existing installation, use **Update the existing MacBook installation**
below. On a disposable Mac or guest for a fresh-install test, run these
separately, stopping at the first error:

```sh
ODS_HOME="$HOME/ods" bash "$HOME/src/ODS/ods/install.sh" --pixel --preflight-only
```

```sh
/usr/bin/python3 "$HOME/src/ODS/ods/installers/macos/lib/pixel-native-install.py" \
    --install-dir "$HOME/ods" --preflight-only
```

On the disposable Mac or guest, proceed after both preflights pass.

```sh
ODS_HOME="$HOME/ods" bash "$HOME/src/ODS/ods/install.sh" --pixel
```

Choose the local test model and installation options when prompted. For a
custom install directory, use the same path for `ODS_HOME`, `--install-dir`
and the status command.

After installation:

```sh
ODS_HOME="$HOME/ods" bash "$HOME/ods/ods-macos.sh" status
```

In Portal, first confirm chat and Settings load. Use only invented data for
these acceptance journeys:

1. Save a test integration, reload Settings and confirm the same choices.
2. Under Ask first, request one exact test-note append. Confirm a pending
   request and unchanged note; Approve must also leave the note unchanged.
   A new explicit retry must append exactly once. Repeat with Decline and
   confirm the refused action never changes the note.
3. From ordinary chat, schedule a one-off calculation a few minutes ahead,
   explicitly with no tools or external notifications. Close the browser,
   let it run, reopen Portal and inspect its retained result. Create another
   one-off task, pause it before it is due and confirm it remains Paused with
   no run. Remove only those test tasks through the normal controls.
4. Complete the apps, local MCP, isolation and lifecycle cases in the matrix
   below. Restart the Mac, wait for Docker Desktop, rerun status, and confirm
   chat, saved integrations and retained task results survive.

Record the first failing step, exact error and source commit. Share sanitized
results, never credentials, account content or private receipt files. Passing
the initial UI journeys does not replace the remaining physical matrix.

### Update the existing MacBook installation

From the normal owner's terminal, keep Docker Desktop running and invoke the
reviewed checkout's updater directly:

```sh
/usr/bin/python3 "$HOME/src/ODS/ods/installers/macos/lib/pixel-native-update.py" \
    --install-dir "$HOME/ods" --ods-source "$HOME/src/ODS/ods" --prompt-for-sudo
```

This uses the candidate's packaging and verification helpers. The older
installed `ods-macos.sh update` does not select an unmerged checkout. Substitute
the actual installation directory consistently. Preserve any preparation
directory and private recovery journals. If it stops, record the first error
before another attempt; do not force a reinstall, delete receipts or reset the
model/connection state.

When `--ods-source` selects an external checkout, the updater also prepares the
owner-side host-agent code and both Portal web images before protected
activation. It retains the installed Compose routes, environment and local
extensions. The public file inventory excludes owner configuration, model
weights, history and credentials. Images are built from the staged bytes;
preparation leaves the running containers and their selected tags unchanged.
After Core activates, public files and clients change under one owner admission
hold shared with ingress finalization. Original public files are retained by
rename, and the prior web image IDs and host-agent definition remain recorded.
Readiness requires the expected images, mounts, health and authenticated host
route before admission reopens. This does not update unrelated ODS services.

An interrupted client handover does not mean Core activation was rolled back.
Keep its preparation and the held operation. After resolving the reported
failure, resume only that preparation from the same source checkout:

```sh
/usr/bin/python3 "$HOME/src/ODS/ods/installers/macos/lib/pixel-native-update.py" \
    --install-dir "$HOME/ods" --ods-source "$HOME/src/ODS/ods" \
    --resume-preparation "/exact/path/printed/by/the/update/preparation"
```

This command requires proof that protected Core activation already completed.
It resumes owner finalization; it cannot activate a prepare-only result or
recover an unrelated deployment. The installed-source `update-pixel` route
continues to update the native runtime from already installed public code.

After a successful update:

```sh
ODS_HOME="$HOME/ods" bash "$HOME/ods/ods-macos.sh" status
```

Record each owner journey separately:

1. Portal loads, the expected installed model answers an ordinary chat, and
   previous model choices, conversations and integration settings remain.
2. Settings → Connected apps offers the native Google route. Use standard
   sign-in if this deployment's Google app is provisioned. Otherwise, Advanced
   may use a Desktop client the owner already manages. For this test select
   only Calendar read. A maintainer's local test client is not the official
   distributed app; Google's production verification remains external work.
3. Pick an explicit date in the primary calendar and ask Portal for only that
   day's event count, without names/details or changes. Compare with that same
   primary calendar and owner-local day. Verify connection status and native
   Calendar-read evidence separately; a plausible answer alone is not a pass.
4. Create one clearly named disposable dated task a few minutes ahead, asking
   for `14 + 8` with no tools or notifications. Close the browser before it is
   due, then reopen Portal and verify one retained result. Remove only that
   task through the rendered confirmation. Previous tasks/history must remain.
5. Complete Notes approval, local MCP, ntfy and configured-app cases below.
   Disabled or declined tools stay disabled or declined. Approval itself makes
   no edit; the matching explicit retry consumes it once.
6. Restart the Mac, start Docker Desktop and recheck saved choices, chat,
   retained results and the native connection route. Record actual launchd,
   Seatbelt and ACL behavior separately from Linux metadata-driver evidence.

Share the exact source commit, OS/chip, model, access mode, per-journey result
and first failure. Do not share client JSON, callback addresses, tokens,
account contents or private receipts. Fresh install and retirement tests belong
on a disposable Mac or guest; another user or install directory still shares
the system services. Results apply to the recorded hardware, model, provider
and access mode.

### MacBook preparation observation, 4 November 2026

The clean starting revision `4525380c411e2dfc85d5664b9f0040154fa2b77c`
was inspected on a physical Apple M5 with 16 GB, macOS 26.5.1 (25F80), arm64.
The existing installation was separate from both the reviewed checkout and the
checkout used for fixes. Existing unrelated checkout edits were left intact.
Docker Desktop was updated from 4.74.0 to 4.98.0 through its normal updater;
the engine changed from 29.4.3 to 29.9.2 and all existing ODS services recovered.

Before ODS activation, the installed Qwen 3.5 9B Q4_K_M at 65,536 context
answered an invented no-tool request correctly in 3 minutes 25 seconds. The
response survived reload and the Docker update. Reading Settings in another
tab triggered the old frontend's stale-conversation warning; closing that tab
and reloading retained the result. This was a baseline observation, not a
candidate conversation-isolation pass.

Private comparisons found unchanged environment, model-selection and active
protected-configuration bytes, including the protected configuration inode.
Both model files retained their inodes and sizes; their initial hashes were
recorded privately. Twenty existing history files remained byte-identical.
The invented QA result and its session index changed normally, and its request
lock disappeared after completion. No existing scheduled tasks or Google grant
were available in this installation to establish preservation of those items.

The reviewed prepare-only command staged its source/runtime, then stopped at
`native-migration-proof-or-authorization-failed`: noninteractive sudo required
owner authentication. Its preparation was retained and Core was not activated.
The proposed owner-client preparer separately passed the real Mac's file/ACL,
Compose-baseline and host-agent binding checks and built both candidate web
images under separate tags, without replacing running services. Preparation
does not establish that a client handover or protected migration succeeds.

After owner authentication, the full updater from
`6dea6ff3517ae0e30e8a0cfeef53e751f6dfb69f` prepared both runtime and clients,
then stopped before activation at `runtime-upgrade-existing-file-missing`.
The existing protected deployment lacked the three newly introduced scheduler
programs. The updater incorrectly treated these new files as required old
files. Inspection also found that the integration lifecycle's ten new paths
were missing from the filesystem adapter's closed addition set, including its
new code directory. The old configuration bytes and inode remained unchanged,
and all eleven existing ODS containers remained healthy after the refusal.
The preparation and private recovery material were retained. Fixes require
complete approved addition families and journaled rollback; silently repairing
missing files in an already capable deployment is not an accepted migration.

Candidate chat, Calendar, browser-closed scheduling, Notes approvals, MCP,
notifications and reboot acceptance remain pending. The Mac has not been
restarted for this qualification. A Google test client was not available;
official application provisioning and verification remain separate maintainer
work. These observations must not be reported as macOS acceptance.

### Scheduled Notes approval on Apple Silicon

Record the source commit, physical model, selected OpenClaw provider and model
alias, and whether the run used the installed Portal or an isolated gateway.
For the managed model route, use the shipped Pixel model relay and the
installer-generated Pixel hook policy. The configuration writer sets
`plugins.entries.pixel-ods.hooks.allowConversationAccess` on the private
candidate. The native runtime reports whether it can restrict a follow-up's
tools and require its first tool call. Record those two acknowledgments
separately when testing a follow-up. A custom provider can report a different
capability set even when it reaches the same model. Keep API keys, private
configuration and note contents out of shared receipts.

1. With a disposable vault set to **Ask first**, schedule a recurring task that
   appends one unique line to an existing note. Close the chat and let the
   scheduler run it. Check for one pending request in owner Settings and
   verify the note is byte-identical. A question in task history alone does
   not give the owner a request to approve.
2. Approve the pending item and verify the file is still unchanged. Let the
   next occurrence run. Verify that it consumed the approval and appended the
   line exactly once. Cancel the task, retain both history entries, and check
   that the following due time passes without another edit.
3. Schedule a read-only request for the note heading. Check that the result
   contains the actual heading, the file stays unchanged and Settings gains
   no approval. For revocation, turn note writing **Off** between occurrences
   and verify that subsequent runs cannot edit the note or queue an approval.

Record whether an owner decision came from rendered Settings or the
authenticated owner API. Track a supported update of an existing launchd
installation as a separate physical qualification.

## Deployment contract

The integrations snapshot publishes nine programs and one owner-run launchd
definition. The composed Source lifecycle includes a separate protected,
code-only installer-support capsule and a fixed 111-file runtime Source
closure. Ordinary discovery selects the actual old owner, gateway, runtime
and configuration inside the bounded, locked operation; it does not initialize
the bridge from candidate fields or relax the protected-program check.

The composition retains the genuine Source 4.3.46 bundle/pins and 26 managed
SDK compatibility repairs. Older Source45 evidence keeps its original identity.
These custody results do not replace the physical Mac checks below. The
controller admits exactly the legacy three-role
or new four-role native service sets, selected from verified complete source
snapshots. A partial set is refused.

The agent endpoint is `/private/var/lib/ods-pixel-integrations/control.sock`.
This runtime is persistent so an owner daemon does not need to recreate a
root-owned `/var/run` path after reboot. Settings remain in the real owner's
`~/.config/ods/pixel-integrations`, outside the gateway's private HOME.

The owner endpoint authenticates one canonical request with fresh 32-byte
server/client nonces and HMAC under a root-provisioned credential. The key and UID
directory are root-owned and grant only the selected owner read/search with
an exact non-inheriting ACL. They do not grant `staff` access. Authentication
has a whole-frame deadline and connection cap. Lost replies never cause an
automatic resend. Replies use a separate authenticated domain bound to the
both nonces and exact request, so a substituted socket cannot forge a successful
settings acknowledgement without the key. This does not encrypt Unix traffic;
private endpoint/alias and relay behavior remain part of the physical gate.
Agent requests cannot select an owner role.

The gateway profiles deny state, credential and log reads/writes in both
modes. This is not a claim of absolute same-UID isolation: arbitrary
owner-selected programs and separately authorized Full Access retain their
existing owner-account implications. Current host-agent Python/LaunchAgent
paths are owner-writable, so a peer PID/path match would not establish trusted
host-agent custody. The protocol does not pretend otherwise.

Readiness checks owner settings and agent tool inventory only. It never
contacts a provider, starts an MCP program, sends a notification or approves
an action. SIGTERM closes admission, reaps owned HTTP workers and local MCP
groups, and preserves effect journals. Native process/descendant behavior
still needs the physical checks below; there is no cgroup-equivalent memory
limit claim.

## Upgrade and preservation

### Optional preparation without activation

The MacBook command above performs the update. To inspect preparation alone,
use the same reviewed checkout with `--prepare-only`. This does not complete
the update or its acceptance journeys. Packaging and verification helpers are
imported relative to that checkout; an older installed helper cannot select
the candidate merely by receiving a newer source directory.

```sh
cd "$HOME/src/ODS/ods"
/usr/bin/python3 installers/macos/lib/pixel-native-update.py \
    --install-dir "$HOME/ods" --ods-source "$PWD" --prepare-only
```

Substitute the actual installation directory for `$HOME/ods`. If protected
verification needs a password, add `--prompt-for-sudo` in a local terminal.
A successful preparation prints its directory and returns the status `prepared`.
Retain that directory and its recovery journals. The existing gateway and
services stay active while the preparer verifies the proposed migration.

Before scheduling a live rollout, check the host agent and both web images.
After signing in, open **Settings → Portal integrations** and verify that
the Dashboard API can read a pending request through the host agent. During
qualification, make a disposable note edit with **Ask first** enabled. Approval
should leave the note untouched until the next matching tool call. Record
separate results for the prepared migration and for this journey through the
running installation.

Legacy recovery schemas 1/2 remain exact. Schema 3 admits only the whole
ten-file integrations addition, or that addition together with the five
inspection additions. Rollback restores prior file absence and old policy
bytes. A protected never-started witness is consumed before bootstrap;
loaded jobs require normal PID/birth/descendant stop evidence.

Source updates use a separate closed schema 4 family. Before replacing code,
runtime or kit bindings, the actual old selection either proves strict absence
or quiesces all recorded Source roles and withdraws the exact old generation.
The protected context and journal bind that receipt to the candidate. Owner
model/environment/onboarding bytes are preserved from an admitted current
snapshot; an old bootstrap receipt is retained as history. Rebinding preserves
identity and token inode and leaves the new generation inactive until explicit
connection activation. Repeated updates retain qualified fixed-slot records
through a finite history ledger rather than overwriting earlier evidence.

Private settings, credentials, delivery journals and notes are not copied
into public deployment snapshots or deleted during rollback. Native retirement
verifies the selected service set and quarantines its runtime, authentication
tree and logs; the owner's settings and external vault remain intact.

Before the first private provisioning step, the controller durably records
the exact prior protected settings/service/installation authority, owner and
install identity, complete three-root prior absence, and approved validator
source. After rollback, the modern retirement reader can recognize that exact
preserved state without first requiring a successful new-service update.
All ten static additions must be absent and the integration job must be
absent and not intentionally disabled. Partial directories accept only known
preparation prefixes; foreign data, links, ACL grants and altered authority
are refused. Interrupted receipt publication never becomes authority.

This record also supports retry and interrupted private quarantine. It does
not widen the restored legacy gateway's filesystem policy, rotate a key or
delete private settings. Unknown residues still fail closed. Real-home
configuration components are created by an owner process, so a root
mkdir/chown interruption cannot leave a root-owned `.config` behind.

Only the verified legacy-preservation transaction may finish interrupted
preparation. It transfers ownership of verified empty pre-chown objects,
preserves every existing byte of a never-activated partial key, appends only
its missing random suffix, and completes the exact missing owner ACL steps.
A complete credential is never replaced or rotated. An already active new
deployment does not use this path to repair drift.

Normal Source retirement has a distinct transaction, protected Source cursor
and fixed Core move plan. It preserves exact state, token, environment, logs,
definitions and ledger-derived history by rename, then archives the final
witness/lock/cursor. Lost acknowledgements resume the same archive and inodes,
including after Core settings/runtime paths have moved. Source-bearing archives
are excluded from legacy completed-retirement pruning. Tokens are not copied
or read to repair custody.

This is not universal stopped-state or reboot recovery. A fresh Source-bearing
retirement with already-stopped Core services and no same-transaction witness,
and Source-bearing `--prepare-stopped-recovery`/`--resume-stopped-recovery`, are
refused. The new ordinary descriptor-only classification also does not admit
fresh stopped recovery. The pre-existing no-descriptor legacy schema 1/2 assist
path is unchanged. A reboot does not silently refresh a retirement witness.
Unknown state, new actors/token, identity drift or cross-filesystem rename
preserves the data and stops the operation.

History capture is bounded to 1,024 nodes, 32 MiB and depth 16; Source and Core
move plans each allow at most 1,024 items. Installer history has at most 64
explicit entries, and pending-initial boot history has at most 16 transactions.
Reaching a bound preserves/refuses; it does not authorize deletion or a prefix
allowlist.

## Required physical matrix

Use a disposable Apple Silicon installation and invented notes, provider
records, pages and notifications. Record the exact source commit, native
snapshot digest, OS/architecture, Python and selected access mode. Use the
ordinary native installer/update path from that reviewed source revision.

| Case | Required evidence |
|---|---|
| Fresh install | Seven verified launchd jobs, both authenticated/restricted channels ready, Settings save and reload |
| Legacy upgrade | Existing Google grant and settings preserved; complete new service snapshot; no provider calls during readiness |
| Existing new-service update | Key unchanged, approvals/journals preserved, no extra default permissions or notification opt-in |
| File/load/readiness failure | Admission remains closed on uncertainty; rollback restores old bytes/absence; forward retry succeeds |
| Interrupted private provisioning | A restored legacy deployment can retire directly using its exact preservation record, including a resumed private quarantine |
| Reboot | Owner, agent, watcher and scheduled notifications return without manual directory creation or service repair |
| Owner-channel isolation | Sandbox cannot read key/state/logs or dispatch owner actions; test direct paths, aliases, hardlinks, child exec and available IPC |
| Role/replay/refusal | Bare request, wrong key, reused proof, changed arguments/version/role, overlong frame and drip client refused without effects |
| Local MCP | Homebrew `npx`/`uvx`, tools, call, restart and idle stop work; SIGTERM and watcher failure leave no owned live descendants |
| Apps and notes | Invented Paperless/Immich/FreshRSS/changedetection data, ntfy, notes, exact approval, task notification and digest recovery |
| Retirement | Six-role legacy and seven-role new deployments; applicable legacy reboot assist tested separately; ordinary Source retirement and same-transaction cursor retry preserve private data/vault; unknown already-stopped Source refused |

The owner-key ACL ABI follows Apple's public
[ACL header](https://github.com/apple-oss-distributions/Libc/blob/main/include/sys/acl.h)
and [membership API](https://github.com/apple-oss-distributions/Libinfo/blob/main/membership.subproj/membership.h).
Mocked ABI tests establish control flow and refusal coverage, not enforcement
by a Mac kernel. Keep this gate pending until the physical evidence exists.
