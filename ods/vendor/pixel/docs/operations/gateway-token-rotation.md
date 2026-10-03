---
title: Gateway token rotation
doc_type: runbook
audience: [owner, operator, security-reviewer]
feature_status: supported
owners: [operations, security]
sources_of_truth: [scripts/rotate-gateway-token.sh, OPERATIONS.md, SECURITY.md]
last_verified_at: 2026-08-27
---

# Gateway token rotation

Gateway rotation is a transactional maintenance operation. The trusted script acquires the exclusive deployment lock, validates the live configuration and generated environment, snapshots both into a private rollback directory, generates a new credential, atomically replaces both files, restarts the exact gateway unit, and runs verification.

## Before rotation

- Confirm the installed host is otherwise healthy and no apply, update, restore, or other rotation owns the deployment lock.
- Ensure the owner can reconnect through the normal loopback client path after restart.
- Keep the private backup location available; do not create a second ad-hoc copy of the credential.
- If compromise is suspected, contain and follow [incident response](incident-response.md); routine rotation alone is not incident eradication.

## Rotate

```bash
# Requires exact confirmation; review before running.
./pixel rotate gateway --confirm
```

The command never prints the new token. If generation, file replacement, restart, or verification fails, its exit trap restores the preceding configuration and deployment environment from the private snapshot and attempts to restart the gateway. Treat any incomplete rollback or unhealthy restart as a stopped maintenance transaction, not permission to edit either file manually.

## Accept the result

1. Re-run installed-host verification and inspect the exact gateway service state and loopback listener.
2. Reconnect through the normal owner client flow using the newly generated configuration.
3. Confirm the preceding token is rejected and the new owner session succeeds without exposing either value in logs or evidence.
4. Complete one harmless configured-model turn if the rotation occurred as part of a release or security acceptance sequence.
5. Retain the private rollback record only under the deployment's credential-retention and backup policy. Its `result.json` is content-free; the snapshot files are not.

Rotation proves only the gateway credential transaction and its observed verification. It does not rotate Source Broker, Operations, Frontier, provider, backup, signing, or external issuer credentials.
