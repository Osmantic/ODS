# Native Portal integrations qualification

Status: implementation and offline regression coverage; **physical Apple
Silicon qualification has not been completed**. A passing Linux/WSL test is
not a launchd, Darwin ACL, Seatbelt, reboot or native process-custody result.

This matrix covers the native Portal runtime, scheduling and Coffee integrations.
Google/Calendar and fixed reviewed apps in Connected apps still depend on the
Linux/WSL Source Broker; their host relay returns unavailable on macOS. They need
a native custody/lifecycle/transaction implementation before a Mac connection
journey can be qualified. Do not treat a pass here as support for those routes.

## First physical run

Start from the exact source commit named in the PR qualification note, with
the checkout outside the install directory. Keep the local container engine
running. For a first native installation, use a disposable Apple Silicon Mac
or macOS guest with no existing native Pixel deployment. A second user account
or a different `ODS_HOME` still shares the system launchd services.

On a Mac that already runs native Pixel, run the two preflights below and
retain the installation, model and private receipts. The general preflight
checks the host. The native preflight checks whether an initial installation
can proceed. If it reports `existing-native-pixel-requires-migration-or-recovery`,
stop before the initial install step and use the existing-install procedure
below. The general `ods-macos.sh update` command uses the installed source.

For a checkout at `~/src/ODS` and installation at `~/ods`, record the source and
platform first:

```sh
git -C "$HOME/src/ODS" rev-parse HEAD
git -C "$HOME/src/ODS" status --short
sw_vers
uname -m
docker context show
docker version --format 'client={{.Client.Version}} server={{.Server.Version}}'
```

Confirm that the commit matches the qualification note and the checkout is
clean. Then run these separately, stopping at the first error:

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

The new snapshot publishes nine integrations programs and one owner-run
launchd definition, with no new model tools. Dependency, vendor, image and trust
pins remain unchanged. The composed PR separately carries 25 managed SDK
compatibility repairs; their custody and runtime evidence do not replace the
physical Mac checks below. The controller admits exactly the legacy three-role
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

### Prepare an existing native installation

For a candidate in a separate checkout, run the updater **from that checkout**.
It imports its packaging and verification helpers relative to its own file.
Passing a newer source directory to an older installed helper can therefore
select an older set of native services. Compare `git -C "$HOME/src/ODS" rev-parse HEAD`
with the intended revision, then prepare the existing install.

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

Automatic quarantine retry coverage concerns the retained integrations
private-root prefix. The preexisting later full-native archive loop preserves
interrupted archives for assisted recovery; this change does not claim
automatic recovery from every possible full-uninstall crash.

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
| Retirement | Six-role legacy and seven-role new deployments retire with reboot barrier; private data and vault preserved; unknown residue refused |

The owner-key ACL ABI follows Apple's public
[ACL header](https://github.com/apple-oss-distributions/Libc/blob/main/include/sys/acl.h)
and [membership API](https://github.com/apple-oss-distributions/Libinfo/blob/main/membership.subproj/membership.h).
Mocked ABI tests establish control flow and refusal coverage, not enforcement
by a Mac kernel. Keep this gate pending until the physical evidence exists.
