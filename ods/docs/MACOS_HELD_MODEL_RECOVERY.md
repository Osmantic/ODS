# Recovering a held Mac model promotion

Use this for local Apple Silicon installations where bootstrap selected a new
GGUF but native Pixel still has the old model contract. A subsequent interrupted
switch can leave a durable `held` transaction whose `previous.model` names 2B,
`target` is null, while inference already serves 9B. Normal recovery cannot prove
the old model. A completed bootstrap record alone is not proof of a safe repair.

This is **not** native-service retirement (#7456), an uninstall, or the bootstrap
prevention change (#7107). Keep model files, `.env` and all transaction records.
Do not choose an unverified release or edit/delete a journal to clear the error.

## Owner procedure

Requires the updated host agent containing
`POST /v1/model/recover/current-local`. Updating only a source checkout does not
update the running installation. Use the normal reviewed ODS update procedure,
not a fresh install, and retain a backup before maintenance. Keep Docker Desktop
and the local model running. Run as the original installing user without sudo.

Inspect first (read-only; it does not test inference or modify the transaction):

```bash
python3 "$HOME/ods/scripts/recover-macos-local-model.py"
```

After inspection confirms this local-model split meets the proof requirements
below, explicitly repair:

```bash
python3 "$HOME/ods/scripts/recover-macos-local-model.py" --apply
```

For a custom installation, append `--install-dir /your/ods/path` to both commands.
The expected repair result has `pending: false`, `phase: completed`, and
`outcome: commit`, with the same transaction ID. Reload Portal and send a short
message. Restart the Mac, allow Docker/ODS to start, and repeat the message to
confirm persistence. An idle/completed status without a repair request does not
by itself prove that chat works.

On a refusal or timeout, leave everything intact. The host may have applied the
target even if the client lost its reply. Inspect the status and local host log
before retrying; never launch another switch or repeat the repair blindly.
Share only the status/error code, not credentials, `.env`, or protected records.

## Proof and limits

The authenticated action owns the existing model lifecycle lock. It requires:

- The same saved native transaction, with unchanged original host file hashes,
  except for the narrowly validated switchboard record described below.
- Local Apple inference, a regular installed GGUF, and identical saved/current
  context. Remote routes and other platforms are not reinterpreted.
- Current inference identity/context and a real LiteLLM completion before apply.
- Unchanged native state, configuration and journal across that proof.
- Apply through the original native coordinator, then repeated live inference
  proof and exact native ownership before committing and opening admission.

It does not download models, restart inference, replace settings, invent receipts
or discard the previous contract. An interrupted repair resumes only when the
native controller proves the exact target is already applied. An ambiguous
apply that still reads held is refused, not replayed.

### Switchboard-only drift

Older background route verification could change `data/model-state.json` after
a switch was held. A matching list of changed files alone is not permission to
recover. The action accepts this one-file difference only when the file is valid,
stable, and already describes the configured local GGUF at the saved context,
with a completed proof, the standard local endpoint and public alias, no remote
native route, no active operation, and no queued requests. Both the original
and current file must have readable fingerprints; a new/missing file is refused.
All other tracked files must still match the original journal.

Recovery independently proves live inference, the default LiteLLM route and
`ods/current`, checks native transaction ownership, and repeats the stable alias
proof after applying. It does not restore or rewrite `model-state.json` or replace
the original `before` fingerprints. The existing transaction records the actual
final fingerprints only when it proceeds to commit. Concurrent file changes or
unconfirmed native application still stop without replaying a mutation. This
does not infer what originally changed the file on a particular machine.

This is explicit recovery, not an automatic fallback for every failed switch.
The existing background-publication guard prevents new observational writes
while a durable switch is pending; it cannot undo a change made before the guard
was installed. Both controls are needed for those older affected installations.

Other host file changes, changed ownership, a missing model or a failed proof
require diagnosis; this command is not a general-purpose reset. Initial route
proof now checks the durable transaction both before probing and under the
lifecycle lock before publishing, incorporating the protection proposed in
#7013. It defers if transaction custody is unreadable. Bootstrap prevention in
#7107 remains separate work.

## Hardware evidence

On an M5/16 GiB Mac, the official 2B-to-9B bootstrap reproduced the contract split.
An ordinary subsequent switch succeeded. A controlled host-process exit directly
after the production held save then reproduced the reported held/previous-2B/
target-null state without synthesizing a journal. Verified repair completed that
same transaction in 24.16 seconds with no tracked host configuration changes.
A real Portal message then finished in 2m30s with the exact requested answer,
and the composer returned to Available. This qualifies recovery from that
interruption, not the unknown original trigger on Iwan's M1. Post-reboot
validation found the same completed transaction and 9B contract intact, but a
separate admission startup defect required intervention: macOS reused the old
gateway PID for another service. After restarting the identified colliding
service and gateway, live inference passed and Portal returned the requested
answer in 2m37s. That was not an unattended successful boot.

For the switchboard-only extension, an affected M1/16 GiB owner subsequently
reported a successful `completed`/`commit` receipt for the same original held
transaction after installing the scoped host fix. Independent M5 preflight
validated the installed route, real inference and `ods/current` without changing
files. Automated tests cover changed/unsafe routes and interrupted recovery.
The M1 owner then reported Portal unavailable and an unknown chat-activity
notice. The model recovery receipt does not establish Portal availability or
chat completion. Do not repeat repair after a completed receipt: inspect the
Portal/edge/native ingress path and use the existing chat activity/result check
without resending the request. A working Portal conversation and post-reboot
acceptance remain unverified; no fleet-wide claim is made.

## Darwin process ownership

This change also keeps Darwin's existing exclusive kernel claim open for the
gateway's lifetime, instead of releasing it after registration. An atomically
saved private witness binds the exact claim inode and process record incarnation
(device, inode, birth time and change time). Only acquisition of that same
kernel claim with a matching witness proves the previous owner gone when its
PID has been recycled. Held/interrupted admission state is never cleared.

The process record itself remains the legacy `{pid}` format so older runtime
rollback remains readable. An older runtime rewrites that record, invalidating
the witness; a stale witness cannot bypass a live legacy owner. Linux/WSL keeps
its existing boot/start identity checks. Native Mac and Linux tests cover
ownership, process death, concurrent claimants, malformed/stale witnesses and
preservation of held state. A second full-machine reboot of the patched runtime
has not yet been performed; process-death tests are not represented as that.

The plugin must be deployed through the native runtime updater; updating only
the host Python script does not replace the protected gateway bundle. First
repair a held model transaction; do not start a runtime update while it remains
pending. An already-blocked legacy PID record without a matching witness still
requires diagnosis. Do not delete its process/claim files, stop an unidentified
PID or bypass admission checks to install this fix.
