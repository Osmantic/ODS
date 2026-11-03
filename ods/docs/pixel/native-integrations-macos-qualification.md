# Native Portal integrations qualification

Status: implementation and offline regression coverage; **physical Apple
Silicon qualification has not been completed**. A passing Linux/WSL test is
not a launchd, Darwin ACL, Seatbelt, reboot or native process-custody result.

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
