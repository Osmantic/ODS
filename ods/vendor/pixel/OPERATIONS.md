# Operations

## Routine commands

```bash
./pixel ui
./pixel verify
./pixel services status
./pixel services logs
./pixel backup /secure/pixel-backups age1CLIENTBACKUPRECIPIENT
./pixel rotate gateway --confirm
systemctl status openclaw-gateway.service
systemctl status pixel-web-courier.service
systemctl status pixel-source-broker.timer
systemctl status pixel-source-broker.service
systemctl status pixel-ops-broker.service
systemctl status pixel-frontier-broker.service
journalctl -u pixel-source-broker.service --since today
journalctl -u pixel-web-courier.service --since today
journalctl -u pixel-ops-broker.service --since today
journalctl -u pixel-frontier-broker.service --since today
./pixel limbs
journalctl -u openclaw-gateway.service --since today
```

The local page shows content-free readiness, limb boundaries, pending counts, adaptive
routing savings, provider/auth mode, rolling Frontier budget totals/limits/remaining,
recent fixed-action outcomes, and read-only release-workspace orientation. The update view
shows only versions, bounded lifecycle counts, interruption state, and a fixed next step;
it does not verify signatures or migrations and exposes no path, hash, signer, source, or
receipt content.

The recovery guide summarizes only exact content-free control receipts. It can say that a
backup creation succeeded or a pause was requested, but cannot verify a backup artifact or
current broker pause state. Follow its fixed terminal sequence to validate and rehearse a
backup before restore, and complete incident review before any terminal-only resume.

It may save public onboarding and run
`configure`, `plan`, or `verify` only after an exact short-lived confirmation. A private,
disabled-by-default policy can separately enable public update checks, signed encrypted
backup creation, and one-way emergency pause. `actions.deepWorkPause`,
`actions.deepWorkResume`, and `actions.deepWorkCancel` additionally require starting the UI
with `--work-controller-config PRIVATE_FILE`; each is separately disabled by default and
presents an exact confirmation. Pause affects only later supervised milestones. Resume is
bound to the exact privately reviewed paused checkpoint and starts no work. Cancellation
is available only for a safely settled goal or an atomically revocable unlaunched child; it
cannot stop or hide a live worker. None reveals the configured path, private review hash,
or command output. Legacy policies without these keys retain the disabled defaults. Stop
the UI when it is not needed. Never expose its port. Activation, approvals, backup
restore/decryption, rotation, update activation, Operations or Frontier broker resume, and
incident recovery remain in the existing CLI workflows.

For guided long-goal drafting, first create the inert input bundle from a reviewed private
selection, then copy `control/work-authoring.example.json` to an owner-only file and replace
its fixed policy, catalog, object-store, and draft-store paths. Set a bounded retention and
enable `actions.deepWorkDraft` in the private control policy. Launch:

```bash
./pixel ui --work-authoring-config /private/pixel/work-authoring.json
```

Use only the exact startup URL. The wizard sees generic opaque input summaries and creates
only a hash-confirmed inert draft under the configured draft store. Review that private
draft before using the separate goal preparation/controller/staging workflows. The page
cannot admit a directory, reveal a host path or object hash, compile, stage, schedule, run,
resume, approve egress, or expand authority. Delete/archive reviewed drafts from the trusted
host when the configured retention fills; the browser intentionally has no deletion route.
Private control logs are under `${XDG_CONFIG_HOME:-$HOME/.config}/pixel-control/logs` and
must be reviewed and sanitized before sharing. See `CONTROL-SURFACE.md`.

The private control policy may also set `views.frontierReviews` to `true`. The page then
loads pending, already-sanitized Frontier capsules only when **Load pending reviews** is
selected. Use this view only on a trusted browser profile with screen sharing stopped;
extensions can read anything rendered in a page. Compare the full capsule and both
hashes, then use the displayed `frontier-show` and `frontier-approve` commands in a
trusted terminal. The broker independently rechecks plan expiry, policy, request,
capsule, cancellation, budget, cache, and exact hash before transmission.
The review endpoint also requires the random token in the exact URL printed by that
`./pixel ui` process. The page removes its fragment from the address bar after capturing
it in memory. A base URL, stale URL, session cookie alone, or token from another process
cannot read capsules. Stop and restart the service if the launch URL may have been seen.

Run verification after host maintenance and monthly. The backup command never writes a
plaintext archive: it streams private state directly into an `age` recipient-encrypted
file, signs it with the deployment's Ed25519 backup key, and writes a checksum and
detached signature. Keep the age identity outside Pixel, retain a trusted copy of the
public `backup-allowed-signers` file separately from the backup set, and test restoration
on a non-production account and host. For unattended jobs, set
`PIXEL_BACKUP_AGE_RECIPIENT` instead of passing the public recipient on the command line.
Override `PIXEL_BACKUP_SIGNING_KEY` and `PIXEL_BACKUP_ALLOWED_SIGNERS` only with absolute,
access-controlled paths.
The default `${XDG_CONFIG_HOME:-$HOME/.config}/pixel-control/policy.json` is included in
the signed encrypted private-state set. A custom policy path must also be exported as
`PIXEL_CONTROL_POLICY_PATH` for both backup and restore so the recovery allowlist remains
exact.
The encrypted manifest records the active installed release, even when newer recovery
tooling captures an older deployment during an upgrade.

```bash
backup=/secure/pixel-backups/pixel-private-TIMESTAMP.tar.gz.age
./pixel restore "$backup" --identity /offline/age-identity --validate-only
./pixel restore "$backup" --identity /offline/age-identity \
  --rehearse /var/tmp/pixel-restore-rehearsal
# After reviewing the rehearsal and an automatic pre-restore backup destination:
./pixel restore "$backup" --identity /offline/age-identity --replace --confirm
```

Restore rejects an absent or non-canonical signature, an untrusted signer, a wrong age
identity, checksum mismatch, duplicate/path-escaping/special archive member, symlink
restore root, oversized archive, or a destination outside the current deployment's
private-state contract. A failed live verification rolls the whole transaction back and
restarts the prior services. Do not delete the automatic pre-restore backup until the
owner accepts the restored system.

## Failure handling

For suspected compromise, begin with [INCIDENT-RESPONSE.md](INCIDENT-RESPONSE.md) and
contain affected limbs before ordinary troubleshooting.

If an apply fails, automatic restoration runs before the command exits. For a bad
release discovered later, use `./pixel rollback --confirm`; it consumes the last
recorded rollback point so accidental repeated rollback is impossible. If the model
or SearXNG is down, leave the gateway configuration untouched and repair that endpoint.
If rendered navigation fails, inspect `pixel-web-courier.service`, confirm the private
address rejection in `./pixel verify`, and check the JSONL audit log configured by
`PIXEL_WEB_COURIER_LOG_PATH`. Logged URLs intentionally omit user information, query
strings, and fragments.

For suspected credential exposure, stop the gateway, revoke the OAuth grant in the
client account, delete the local token, rotate the OAuth client if exposed, inspect
logs without copying email bodies into tickets, then reauthorize.

If the backup signing key may be exposed, stop trusting all signatures from that key,
rotate it, export the new allowed-signers trust anchor through a separate channel, and
create a fresh full backup. Encryption alone does not authenticate a backup producer.

If the gateway credential may be exposed, run `./pixel rotate gateway --confirm`. Pixel
updates the live config and ignored deployment source of truth, restarts and verifies
the gateway, and restores the old credential automatically if verification fails. The
old value remains only in a mode-`0700` rollback record under the private OpenClaw
backup directory; retain or destroy it according to the incident evidence policy.

The live OAuth token is owned by the `pixel-source-broker` system identity under
`/var/lib/pixel-source-broker/private`. Use an operator-approved root backup process;
do not relax its permissions so the gateway owner or Pixel can include it in an ordinary
workspace backup. Projection files are derived data and may be rebuilt with:

```bash
sudo systemctl start pixel-source-broker.service
```

When `PIXEL_CALENDAR_DIRECT_ENABLED=1`, the broker may apply only a private create with
no attendees or a time-only reschedule carrying the current event ETag. A system-owned
path unit wakes the hardened actuator when the unprivileged gateway closes a proposal;
the gateway retains `NoNewPrivileges` and cannot start services or elevate. The broker
revalidates the proposal independently, serializes executions, enforces
`PIXEL_CALENDAR_DIRECT_MAX_PER_HOUR`, and records prior times for recovery before using
Google credentials. Disable this
path immediately by setting the flag to `0`, reinstalling the broker, and restarting the
gateway if direct behavior is not desired.

Review consequential Calendar proposals with `./pixel source-show PROPOSAL_ID`. That command freezes a
broker-owned snapshot and prints its SHA-256. Apply only that snapshot with
`./pixel source-approve PROPOSAL_ID SHA256 --confirm`; do not bulk-approve proposals.
Verify the result echoes the same hash. Updates and deletes also carry the projected
event ETag and use `If-Match`, so a changed event fails instead of being overwritten.
Attendee notifications remain disabled. The actuator atomically creates a
`PROPOSAL_ID.processing.json` claim before contacting Google. If an approval fails and
that claim remains, do not retry or delete it until an operator has inspected the live
event and `journalctl -u pixel-source-action@PROPOSAL_ID.service`; the external outcome
is deliberately treated as uncertain.

Restored pre-action-journal claims receive the same treatment. If a processing claim exists
without a terminal result, `source-approve` stops before writing any new journal state or
contacting Google. `source-reconcile` likewise refuses a legacy create claim that has no
matching shared action journal instead of implicitly migrating it. A legacy terminal result that
has no matching shared action journal also
fails closed rather than being promoted into new proof. Preserve the approved snapshot,
claim/result bytes, hashes, authenticated backup, and unit journal; clearing a failed unit
display does not reconcile the provider outcome and must never be used to authorize a retry.

Review Operations jobs with `./pixel ops-show JOB_ID`. Approve only an exact inspected
plan hash with `./pixel ops-approve JOB_ID PLAN_SHA256 --confirm`. If an SSH identity
changes, quarantine that target and verify the new identity independently before any
key-pin update. Operations output is evidence, never authorization. See
[OPERATIONS-LIMB.md](OPERATIONS-LIMB.md) for downloads, transfers, runners, cancellation,
policy changes, and recovery.

Review Frontier plans with `./pixel frontier-show JOB_ID`. Approve only the displayed
unexpired plan hash with `./pixel frontier-approve JOB_ID PLAN_SHA256 --confirm`. The
approval permits one sanitized provider transmission, not any action described in the
response. Inspect rolling authority with `./pixel frontier-authority show` and its
audit with `./pixel frontier-authority audit`. If egress, classification, account, or
key use looks wrong, immediately run `./pixel frontier-pause "reason" --confirm`, stop
the broker service, preserve content-free event/usage evidence, revoke the provider key,
and follow [INCIDENT-RESPONSE.md](INCIDENT-RESPONSE.md). See
[FRONTIER-LIMB.md](FRONTIER-LIMB.md) for lease, cancellation, retention, and provider
qualification details.

All three plan-approval commands above must run as the non-root deployment owner in a
real terminal with password-backed sudo. Pixel invalidates cached administrator
authentication, requests fresh authentication, displays the complete protected object,
then requires an unpredictable hash-bound phrase and invalidates the new timestamp on normal exit.
Protected JSON is ASCII-escaped for terminal-safe review.
Non-terminal, root, and passwordless-sudo attempts fail closed. `--confirm` expresses
CLI intent but is not sufficient by itself; keep the administrator secret outside Pixel.

## Upgrades

Follow [UPGRADE.md](UPGRADE.md). Never edit an installed release in place. Build and
test a new repository version, plan it, preserve the private backup, then apply it.
