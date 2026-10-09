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

After support confirms this is the unchanged local-model split, explicitly repair:

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

- The same saved native transaction, with unchanged original host file hashes.
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
validation remains pending; it must not be inferred from unit tests or a host
agent restart.
